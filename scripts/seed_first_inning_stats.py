#!/usr/bin/env python3
"""Seed first-inning pitcher stats from MLB play-by-play data (Step 1.6).

For each completed regular-season game, fetches play-by-play from the MLB Stats
API, identifies the ACTUAL first-inning pitcher (not trusting the games table),
parses first-inning events, and updates pitcher_stats with aggregated stats.

Runs come from the linescore already in the games table (first_inn_away_runs,
first_inn_home_runs), NOT re-derived from play-by-play.
"""

import requests
import time
import sys
from collections import defaultdict

SUPABASE_URL = "https://cdomrqoslgewamcqhbal.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNkb21ycW9zbGdld2FtY3FoYmFsIiwi"
    "cm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTAxMDIwNSwiZXhwIjoyMDkw"
    "NTg2MjA1fQ._sYGKhDp5LL-8G7ZxZm2xsjfQBuUh-L4-0TEwKatvvk"
)

MLB_BASE = "https://statsapi.mlb.com/api/v1"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

UPSERT_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def fetch_all_games():
    """Fetch all final regular-season games with first-inning linescore."""
    all_games = []
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/games",
            headers=HEADERS,
            params={
                "select": "game_pk,game_date,home_pitcher_id,away_pitcher_id,"
                          "first_inn_away_runs,first_inn_home_runs",
                "status": "eq.final",
                "game_type": "eq.regular",
                "order": "game_date.asc",
                "limit": 1000,
                "offset": offset,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        all_games.extend(data)
        if len(data) < 1000:
            break
        offset += 1000
    return all_games


def sb_upsert(table, rows, on_conflict=None):
    """Batch upsert rows, 200 at a time."""
    if not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    for i in range(0, len(rows), 200):
        batch = rows[i : i + 200]
        for attempt in range(3):
            try:
                r = requests.post(
                    url, headers=UPSERT_HEADERS, json=batch, timeout=30
                )
                if r.status_code in (200, 201):
                    break
                print(
                    f"\n  WARN {table}[{i}]: HTTP {r.status_code} {r.text[:300]}",
                    flush=True,
                )
                if attempt < 2:
                    time.sleep(2)
            except Exception as e:
                print(f"\n  ERR {table}[{i}]: {e}", flush=True)
                if attempt < 2:
                    time.sleep(2)


# ---------------------------------------------------------------------------
# MLB API
# ---------------------------------------------------------------------------
def fetch_play_by_play(game_pk):
    """Fetch play-by-play for a single game. Returns parsed JSON or None."""
    for attempt in range(3):
        try:
            r = requests.get(
                f"{MLB_BASE}/game/{game_pk}/playByPlay",
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except Exception:
            pass
        if attempt < 2:
            time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# Play-by-play parsing
# ---------------------------------------------------------------------------
def identify_first_inning_pitcher(pbp_data, half_inning):
    """Find the FIRST pitcher who appears in a given half-inning from play-by-play.

    Returns (pitcher_id, pitcher_name) or (None, None) if no plays found.
    """
    for play in pbp_data.get("allPlays", []):
        about = play.get("about", {})
        if about.get("inning") != 1:
            continue
        if about.get("halfInning") != half_inning:
            continue

        matchup = play.get("matchup", {})
        pitcher = matchup.get("pitcher", {})
        pid = pitcher.get("id")
        pname = pitcher.get("fullName", "Unknown")
        if pid:
            return pid, pname

    return None, None


def parse_first_inning(pbp_data, pitcher_id, half_inning):
    """Extract first-inning PA stats for a specific pitcher in a specific half.

    Only counts plays where matchup.pitcher.id == pitcher_id, so mid-inning
    pitching changes are handled correctly.

    Uses result.eventType (snake_case) for classification, which is more
    consistent than result.event (title case).

    Returns dict with batters_faced, strikeouts, walks, hits, hr, hbp, pitches.
    """
    stats = {
        "batters_faced": 0,
        "strikeouts": 0,
        "walks": 0,
        "hits": 0,
        "hr": 0,
        "hbp": 0,
        "pitches": 0,
        "pitches_available": True,
    }

    for play in pbp_data.get("allPlays", []):
        about = play.get("about", {})
        if about.get("inning") != 1:
            continue
        if about.get("halfInning") != half_inning:
            continue

        # Only count PAs where this pitcher was on the mound
        pid = play.get("matchup", {}).get("pitcher", {}).get("id")
        if pid != pitcher_id:
            continue

        result = play.get("result", {})

        # Only count completed plate appearances
        if result.get("type") != "atBat":
            continue

        stats["batters_faced"] += 1

        # Count pitches from playEvents where type == 'pitch'
        play_events = play.get("playEvents", [])
        if play_events:
            pitch_count = sum(
                1 for ev in play_events if ev.get("type") == "pitch"
            )
            stats["pitches"] += pitch_count
        else:
            # playEvents missing — mark pitches as unavailable
            stats["pitches_available"] = False

        # Classify by eventType (snake_case, more consistent)
        event_type = result.get("eventType", "")

        if "strikeout" in event_type:
            stats["strikeouts"] += 1
        elif event_type in ("walk", "intent_walk"):
            stats["walks"] += 1
        elif event_type == "hit_by_pitch":
            stats["hbp"] += 1
        elif event_type == "home_run":
            stats["hits"] += 1
            stats["hr"] += 1
        elif event_type in ("single", "double", "triple"):
            stats["hits"] += 1
        # All other atBat events (field_out, grounded_into_double_play,
        # fielders_choice, sac_fly, sac_bunt, field_error, etc.)
        # just count toward batters_faced.

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading games from Supabase ...", end=" ", flush=True)
    games = fetch_all_games()
    print(f"{len(games)} regular-season final games", flush=True)

    # Keep only games with first-inning linescore data
    games = [
        g for g in games
        if g["first_inn_away_runs"] is not None
        and g["first_inn_home_runs"] is not None
    ]
    print(f"  {len(games)} with first-inning linescore data", flush=True)

    # Group by season
    by_season = defaultdict(list)
    for g in games:
        by_season[int(g["game_date"][:4])].append(g)

    grand_total = 0
    skipped_games = []

    for season in sorted(by_season):
        sg = by_season[season]
        print(f"\n{'='*60}")
        print(f"  SEASON {season}  ({len(sg)} games)")
        print(f"{'='*60}")

        # Accumulate: pitcher_id -> first_inn_* totals
        agg = defaultdict(lambda: {
            "first_inn_starts": 0,
            "first_inn_scoreless": 0,
            "first_inn_runs": 0,
            "first_inn_hits": 0,
            "first_inn_walks": 0,
            "first_inn_strikeouts": 0,
            "first_inn_hr": 0,
            "first_inn_hbp": 0,
            "first_inn_batters_faced": 0,
            "first_inn_pitches": 0,
            "pitches_available": True,
        })

        # Track pitcher names for player inserts
        pitcher_names = {}

        processed = 0
        errors = 0

        for i, game in enumerate(sg):
            pbp = fetch_play_by_play(game["game_pk"])
            if pbp is None or not pbp.get("allPlays"):
                errors += 1
                skipped_games.append(game["game_pk"])
                if (i + 1) % 500 == 0:
                    print(
                        f"  Processed {i+1}/{len(sg)} games for season {season} "
                        f"(errors={errors})",
                        flush=True,
                    )
                time.sleep(0.3)
                continue

            # Top of 1st: identify the actual pitcher (usually home starter)
            # Bottom of 1st: identify the actual pitcher (usually away starter)
            #
            # CRITICAL MAPPING:
            #   Top-of-1st pitcher ALLOWED first_inn_away_runs (away team batting)
            #   Bottom-of-1st pitcher ALLOWED first_inn_home_runs (home team batting)
            for half, runs_col in [
                ("top", "first_inn_away_runs"),
                ("bottom", "first_inn_home_runs"),
            ]:
                pid, pname = identify_first_inning_pitcher(pbp, half)
                if pid is None:
                    continue

                pitcher_names[pid] = pname

                ps = parse_first_inning(pbp, pid, half)
                if ps["batters_faced"] == 0:
                    continue

                runs = game[runs_col]
                a = agg[pid]
                a["first_inn_starts"] += 1
                a["first_inn_runs"] += runs
                if runs == 0:
                    a["first_inn_scoreless"] += 1
                a["first_inn_hits"] += ps["hits"]
                a["first_inn_walks"] += ps["walks"]
                a["first_inn_strikeouts"] += ps["strikeouts"]
                a["first_inn_hr"] += ps["hr"]
                a["first_inn_hbp"] += ps["hbp"]
                a["first_inn_batters_faced"] += ps["batters_faced"]
                a["first_inn_pitches"] += ps["pitches"]
                if not ps["pitches_available"]:
                    a["pitches_available"] = False

            processed += 1
            if (i + 1) % 500 == 0:
                print(
                    f"  Processed {i+1}/{len(sg)} games for season {season} "
                    f"(errors={errors})",
                    flush=True,
                )

            time.sleep(0.3)

        print(
            f"  Done: {processed} games processed, {errors} errors, "
            f"{len(agg)} pitchers found",
            flush=True,
        )

        # --- Ensure all pitchers exist in players table ---
        player_rows = []
        for pid, pname in pitcher_names.items():
            if pid in agg:  # only insert if they have stats
                player_rows.append({
                    "mlb_player_id": pid,
                    "name": pname,
                    "position": "P",
                })

        if player_rows:
            print(
                f"  Ensuring {len(player_rows)} pitchers exist in players ...",
                end=" ",
                flush=True,
            )
            sb_upsert("players", player_rows, on_conflict="mlb_player_id")
            print("done", flush=True)

        # --- Build pitcher_stats upsert rows ---
        rows = []
        for pid, stats in agg.items():
            row = {
                "mlb_player_id": pid,
                "season": season,
                "first_inn_starts": stats["first_inn_starts"],
                "first_inn_scoreless": stats["first_inn_scoreless"],
                "first_inn_runs": stats["first_inn_runs"],
                "first_inn_hits": stats["first_inn_hits"],
                "first_inn_walks": stats["first_inn_walks"],
                "first_inn_strikeouts": stats["first_inn_strikeouts"],
                "first_inn_hr": stats["first_inn_hr"],
                "first_inn_hbp": stats["first_inn_hbp"],
                "first_inn_batters_faced": stats["first_inn_batters_faced"],
            }
            # Only set pitches if data was available for ALL appearances
            if stats["pitches_available"]:
                row["first_inn_pitches"] = stats["first_inn_pitches"]
            else:
                row["first_inn_pitches"] = None

            rows.append(row)

        print(
            f"  Upserting {len(rows)} pitcher-season rows ...",
            end=" ",
            flush=True,
        )
        sb_upsert("pitcher_stats", rows, on_conflict="mlb_player_id,season")
        print("done", flush=True)

        # Season summary
        tot_starts = sum(s["first_inn_starts"] for s in agg.values())
        tot_scoreless = sum(s["first_inn_scoreless"] for s in agg.values())
        tot_bf = sum(s["first_inn_batters_faced"] for s in agg.values())
        tot_k = sum(s["first_inn_strikeouts"] for s in agg.values())
        tot_h = sum(s["first_inn_hits"] for s in agg.values())
        rate = tot_scoreless / tot_starts if tot_starts else 0
        k_per_bf = tot_k / tot_bf if tot_bf else 0
        h_per_bf = tot_h / tot_bf if tot_bf else 0
        print(f"  >> {tot_starts} pitcher-starts | scoreless rate: "
              f"{tot_scoreless}/{tot_starts} = {rate:.3f}")
        print(f"  >> {tot_bf} BF | K/BF {k_per_bf:.3f} | H/BF {h_per_bf:.3f}")

        grand_total += len(agg)

    print(f"\n{'='*60}")
    print(f"  COMPLETE — {grand_total} pitcher-season rows updated")
    if skipped_games:
        print(f"  {len(skipped_games)} games skipped (no play-by-play)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
