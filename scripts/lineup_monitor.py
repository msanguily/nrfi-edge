#!/usr/bin/env python3
"""Monitor for confirmed lineups, load weather/odds, run predictions.

cron: */15 11-20 * * * (every 15 min, 11 AM - 8 PM ET)
"""

import sys
import time
import traceback

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.utils import get_today_et, is_mlb_season, setup_logging, get_supabase_client, get_now_et
from scripts.daily_schedule import ensure_player_exists
from scripts.nightly_results import grade_game, grade_predictions
from src.data.mlb_api import get_confirmed_lineups, get_player_info
from src.data.odds_api import fetch_nrfi_odds, store_odds
from src.data.weather_api import get_game_weather_for_prediction
from src.pipeline.predict import predict_nrfi

logger = setup_logging("lineup_monitor").getChild("lineup_monitor")

# Statuses that mean the game is done or cancelled — don't process these
SKIP_STATUSES = {"final", "postponed", "cancelled", "suspended"}


def get_todays_pending_games(db, today_str):
    """Fetch today's games that are not yet final/postponed."""
    resp = (
        db.table("games")
        .select("game_pk, status, home_team_id, away_team_id, home_pitcher_id, away_pitcher_id")
        .eq("game_date", today_str)
        .execute()
    )
    if not resp.data:
        return []
    return [g for g in resp.data if (g.get("status") or "scheduled").lower() not in SKIP_STATUSES]


def get_existing_prediction(db, game_pk):
    """Check if we already have a live prediction for this game."""
    resp = (
        db.table("predictions")
        .select("game_pk, prediction_type, factor_details, created_at")
        .eq("game_pk", game_pk)
        .in_("prediction_type", ["confirmed", "preliminary", "live"])
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_existing_lineup(db, game_pk, team_id):
    """Fetch currently stored lineup for a team in a game."""
    resp = (
        db.table("lineups")
        .select("mlb_player_id, batting_order")
        .eq("game_pk", game_pk)
        .eq("team_id", team_id)
        .order("batting_order")
        .execute()
    )
    return resp.data if resp.data else []


def lineup_changed(old_lineup, new_ids):
    """Check if lineup has changed (different players or order)."""
    if not old_lineup:
        return True
    old_ids = [row["mlb_player_id"] for row in old_lineup]
    return old_ids != new_ids


def store_lineup(db, game_pk, team_id, player_ids):
    """Upsert lineup rows for a team. Ensures all players exist first."""
    now = get_now_et().isoformat()

    for batting_order, pid in enumerate(player_ids, 1):
        ensure_player_exists(pid, db, position="OF", team_id=team_id)

        row = {
            "game_pk": game_pk,
            "team_id": team_id,
            "batting_order": batting_order,
            "mlb_player_id": pid,
            "confirmed_at": now,
        }
        db.table("lineups").upsert(
            row, on_conflict="game_pk,team_id,batting_order"
        ).execute()


def store_weather(db, game_pk, weather):
    """Insert or update weather snapshot for a game."""
    if weather is None:
        return

    row = {
        "game_pk": game_pk,
        "temperature_f": weather.get("temp_f"),
        "wind_speed_mph": weather.get("wind_speed_mph"),
        "wind_relative": weather.get("wind_direction"),
        "is_dome_closed": not weather.get("is_outdoor", True),
    }

    db.table("weather_snapshots").upsert(row, on_conflict="game_pk").execute()


def update_game_status(db, game_pk, status):
    """Update game status (e.g., to 'in_progress', 'postponed')."""
    db.table("games").update({"status": status}).eq("game_pk", game_pk).execute()


def run():
    today = get_today_et()
    today_str = today.isoformat()
    logger.info("=== Lineup Monitor: %s ===", today_str)

    if not is_mlb_season(today):
        logger.info("Not MLB season — skipping")
        return 0

    db = get_supabase_client()

    # Get today's pending games
    pending_games = get_todays_pending_games(db, today_str)
    if not pending_games:
        logger.info("No pending games for %s", today_str)
        return 0

    logger.info("Checking %d pending games", len(pending_games))

    # Refresh odds once for all games
    try:
        odds_list = fetch_nrfi_odds(today_str)
        if odds_list:
            odds_stored = store_odds(odds_list, db)
            logger.info("Refreshed odds: %d rows stored", odds_stored)
    except Exception:
        logger.warning("Odds refresh failed:\n%s", traceback.format_exc())

    new_lineups = 0
    predictions_made = 0
    bets_recommended = 0
    errors = 0

    for game in pending_games:
        game_pk = game["game_pk"]
        try:
            # Check MLB API for confirmed lineups
            lineups = get_confirmed_lineups(game_pk)
            has_confirmed_lineup = False

            if lineups is not None:
                home_ids = lineups.get("home")
                away_ids = lineups.get("away")

                if (home_ids and away_ids
                        and len(home_ids) >= 9 and len(away_ids) >= 9):
                    has_confirmed_lineup = True

                    # Check if lineup has changed from what we have stored
                    old_home = get_existing_lineup(db, game_pk, game["home_team_id"])
                    old_away = get_existing_lineup(db, game_pk, game["away_team_id"])
                    home_changed = lineup_changed(old_home, home_ids)
                    away_changed = lineup_changed(old_away, away_ids)

                    existing_pred = get_existing_prediction(db, game_pk)

                    if not home_changed and not away_changed and existing_pred is not None:
                        logger.debug("Game %d: lineup unchanged, prediction exists — skipping", game_pk)
                        continue

                    # New or changed lineup detected
                    if home_changed or away_changed:
                        logger.info("Game %d: %s lineup detected",
                                    game_pk, "new" if existing_pred is None else "changed")
                        new_lineups += 1

                    # Store lineups
                    if home_changed:
                        store_lineup(db, game_pk, game["home_team_id"], home_ids)
                    if away_changed:
                        store_lineup(db, game_pk, game["away_team_id"], away_ids)

            # If no confirmed lineup, check if we already have a preliminary prediction
            if not has_confirmed_lineup:
                existing_pred = get_existing_prediction(db, game_pk)
                if existing_pred is not None:
                    logger.debug("Game %d: no lineup yet, preliminary prediction exists — skipping", game_pk)
                    continue
                # Must have pitchers to make even a preliminary prediction
                if not game.get("home_pitcher_id") or not game.get("away_pitcher_id"):
                    logger.debug("Game %d: no pitchers assigned — skipping", game_pk)
                    continue
                logger.info("Game %d: no lineup yet — generating preliminary prediction", game_pk)

            # Fetch and store weather
            try:
                weather = get_game_weather_for_prediction(game_pk, db)
                store_weather(db, game_pk, weather)
            except Exception:
                logger.warning("Weather fetch failed for game %d:\n%s",
                               game_pk, traceback.format_exc())

            # Run prediction (works with or without confirmed lineups)
            try:
                result = predict_nrfi(game_pk, db)
                if result is not None:
                    predictions_made += 1
                    if result.get("bet_recommended"):
                        bets_recommended += 1
                    logger.info(
                        "Game %d: P(NRFI)=%.3f, edge=%s, bet=%s%s",
                        game_pk,
                        result["p_nrfi_calibrated"],
                        f'{result["edge"]:.3f}' if result.get("edge") is not None else "N/A",
                        result.get("bet_recommended", False),
                        "" if has_confirmed_lineup else " [preliminary]",
                    )
                else:
                    logger.warning("Game %d: predict_nrfi returned None", game_pk)
            except Exception:
                logger.error("Prediction failed for game %d:\n%s",
                             game_pk, traceback.format_exc())
                errors += 1

        except Exception:
            logger.error("Error processing game %d:\n%s", game_pk, traceback.format_exc())
            errors += 1

    # Grade first-inning results for today's games that have started
    games_graded = 0
    ungraded = (
        db.table("games")
        .select("game_pk, status")
        .eq("game_date", today_str)
        .neq("status", "final")
        .neq("status", "postponed")
        .neq("status", "cancelled")
        .execute()
    )
    for game in (ungraded.data or []):
        try:
            result = grade_game(db, game["game_pk"])
            if result is not None:
                nrfi, away_runs, home_runs = result
                graded = grade_predictions(db, game["game_pk"], nrfi)
                games_graded += 1
                logger.info("Graded game %d: NRFI=%s (%d-%d), %d predictions updated",
                            game["game_pk"], nrfi, away_runs, home_runs, graded)
        except Exception:
            logger.debug("Could not grade game %d yet", game["game_pk"])

    logger.info(
        "Summary: checked %d games, %d new lineups, %d predictions made, "
        "%d bets recommended, %d games graded",
        len(pending_games), new_lineups, predictions_made, bets_recommended, games_graded,
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
