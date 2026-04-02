#!/usr/bin/env python3
"""Fetch today's MLB schedule, upsert games, and fetch initial NRFI odds.

cron: 0 9 * * * (9:00 AM ET daily during MLB season)
"""

import sys
import time
import traceback

# Ensure project root is on sys.path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.utils import get_today_et, is_mlb_season, setup_logging, get_supabase_client
from src.data.mlb_api import get_games_for_date, get_player_info
from src.data.odds_api import fetch_nrfi_odds, store_odds

logger = setup_logging("daily_schedule").getChild("daily_schedule")


def get_park_map(db):
    """Build home_team_id -> park_id mapping from parks table."""
    resp = db.table("parks").select("park_id, mlb_team_id").execute()
    return {row["mlb_team_id"]: row["park_id"] for row in resp.data}


def ensure_player_exists(mlb_player_id, db, position="P", team_id=None):
    """Insert a player into the players table if they don't already exist.

    Returns True if player was inserted, False if already existed.
    """
    resp = (
        db.table("players")
        .select("mlb_player_id")
        .eq("mlb_player_id", mlb_player_id)
        .execute()
    )
    if resp.data:
        return False

    info = get_player_info(mlb_player_id)
    if info is None:
        # Fallback: insert with just the ID
        row = {
            "mlb_player_id": mlb_player_id,
            "name": f"Unknown ({mlb_player_id})",
            "position": position,
            "current_team_id": team_id,
        }
    else:
        row = {
            "mlb_player_id": mlb_player_id,
            "name": info["name"],
            "throws": info.get("throws"),
            "bats": info.get("bats"),
            "position": info.get("position", position),
            "current_team_id": info.get("current_team_id", team_id),
        }

    db.table("players").upsert(row, on_conflict="mlb_player_id").execute()
    logger.info("Inserted new player: %s (%d)", row["name"], mlb_player_id)
    return True


def run():
    today = get_today_et()
    today_str = today.isoformat()
    logger.info("=== Daily Schedule: %s ===", today_str)

    if not is_mlb_season(today):
        logger.info("Not MLB season — skipping")
        return 0

    db = get_supabase_client()
    park_map = get_park_map(db)

    # Step 1: Fetch schedule from MLB API
    games = get_games_for_date(today_str)
    if games is None:
        logger.error("Failed to fetch schedule from MLB API")
        return 1

    if not games:
        logger.info("No games scheduled for %s", today_str)
        return 0

    # Step 2: Upsert games into database
    games_upserted = 0
    pitchers_found = 0
    errors = 0

    for g in games:
        try:
            # Skip non-regular season games
            if g.get("game_type") != "regular":
                logger.debug("Skipping %s game %d", g.get("game_type"), g["game_pk"])
                continue

            # Ensure pitchers exist in players table
            for pid, tid in [
                (g.get("home_pitcher_id"), g.get("home_team_id")),
                (g.get("away_pitcher_id"), g.get("away_team_id")),
            ]:
                if pid is not None:
                    ensure_player_exists(pid, db, position="P", team_id=tid)
                    pitchers_found += 1

            # Build game row
            row = {
                "game_pk": g["game_pk"],
                "game_date": g["game_date"],
                "game_type": g.get("game_type", "regular"),
                "game_time_utc": g.get("game_time_utc"),
                "status": "scheduled",
                "home_team_id": g["home_team_id"],
                "away_team_id": g["away_team_id"],
                "home_pitcher_id": g.get("home_pitcher_id"),
                "away_pitcher_id": g.get("away_pitcher_id"),
                "park_id": park_map.get(g["home_team_id"]),
                "is_day_game": g.get("is_day_game"),
            }

            db.table("games").upsert(row, on_conflict="game_pk").execute()
            games_upserted += 1

        except Exception:
            logger.error("Error processing game %s:\n%s", g.get("game_pk"), traceback.format_exc())
            errors += 1

    logger.info(
        "Upserted %d games, %d probable pitchers found",
        games_upserted, pitchers_found,
    )

    # Step 3: Fetch and store NRFI odds
    odds_stored = 0
    try:
        odds_list = fetch_nrfi_odds(today_str)
        if odds_list:
            odds_stored = store_odds(odds_list, db)
            logger.info("Stored %d odds rows", odds_stored)
        else:
            logger.warning("No NRFI odds available yet for %s", today_str)
    except Exception:
        logger.error("Error fetching odds:\n%s", traceback.format_exc())
        errors += 1

    # Summary
    logger.info(
        "Summary: %d games today, %d with probable pitchers, %d odds rows stored",
        games_upserted, pitchers_found // 2, odds_stored,
    )

    return 1 if errors > 0 else 0


def main():
    start = time.time()
    try:
        exit_code = run()
    except Exception:
        logger.error("Fatal error:\n%s", traceback.format_exc())
        exit_code = 1
    elapsed = time.time() - start
    logger.info("Finished in %.1f seconds (exit code %d)", elapsed, exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
