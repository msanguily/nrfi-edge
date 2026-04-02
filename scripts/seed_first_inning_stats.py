#!/usr/bin/env python3
"""Seed first-inning pitcher stats from MLB play-by-play data (Step 1.6).

For each game in the games table, fetches play-by-play from the MLB Stats API,
parses first-inning events, and updates pitcher_stats with aggregated first-inning
stats (K, BB, H, HBP, HR, BF, pitches, starts, scoreless, runs).

Runs are NOT re-derived from play-by-play — they come from the linescore data
already in the games table (first_inn_away_runs, first_inn_home_runs).
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

UPSERT_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

# ---------------------------------------------------------------------------
# Event classification
# ---------------------------------------------------------------------------
STRIKEOUT_EVENTS = {"Strikeout", "Strikeout - DP", "Strikeout - TP"}
WALK_EVENTS = {"Walk", "Intent Walk"}
HBP_EVENTS = {"Hit By Pitch"}
SINGLE_EVENTS = {"Single"}
DOUBLE_EVENTS = {"Double"}
TRIPLE_EVENTS = {"Triple"}
HR_EVENTS = {"Home Run"}

# Non-PA event prefixes — do NOT count as batters faced.
# MLB API uses variants like "Caught Stealing 2B", "Stolen Base 3B", etc.
NON_PA_PREFIXES = (
    "Stolen Base", "Caught Stealing", "Pickoff", "Wild Pitch",
    "Passed Ball", "Balk", "Runner Out",
)


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def fetch_all_games():
    """Fetch all final games with pitchers and first-inning linescore."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    all_games = []
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/games",
            headers=headers,
            params={
                "select": "game_pk,game_date,home_pitcher_id,away_pitcher_id,"
                          "first_inn_away_runs,first_inn_home_runs",
                "status": "eq.final",
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


def parse_first_inning(pbp_data, pitcher_id, half_inning):
    """Extract first-inning PA stats for a specific pitcher in a specific half.

    Args:
        pbp_data: Full play-by-play JSON response.
        pitcher_id: MLB player ID of the starting pitcher.
        half_inning: "top" (home pitcher) or "bottom" (away pitcher).

    Returns:
        dict with batters_faced, strikeouts, walks, hits, hr, hbp, pitches.
    """
    stats = {
        "batters_faced": 0,
        "strikeouts": 0,
        "walks": 0,
        "hits": 0,
        "hr": 0,
        "hbp": 0,
        "pitches": 0,
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
        event = result.get("event", "")

        # Skip non-PA events (stolen bases, pickoffs, etc.)
        if event.startswith(NON_PA_PREFIXES):
            continue
        # Only count completed plate appearances
        if result.get("type") != "atBat":
            continue

        stats["batters_faced"] += 1
        stats["pitches"] += len(play.get("pitchIndex", []))

        if event in STRIKEOUT_EVENTS:
            stats["strikeouts"] += 1
        elif event in WALK_EVENTS:
            stats["walks"] += 1
        elif event in HBP_EVENTS:
            stats["hbp"] += 1
        elif event in HR_EVENTS:
            stats["hits"] += 1
            stats["hr"] += 1
        elif event in SINGLE_EVENTS or event in DOUBLE_EVENTS or event in TRIPLE_EVENTS:
            stats["hits"] += 1
        # All other atBat events (outs, FC, errors, sac) just count as BF

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading games from Supabase ...", end=" ", flush=True)
    games = fetch_all_games()
    print(f"{len(games)} total", flush=True)

    # Keep only games with both pitchers and first-inning linescore
    games = [
        g for g in games
        if g["home_pitcher_id"] and g["away_pitcher_id"]
        and g["first_inn_away_runs"] is not None
        and g["first_inn_home_runs"] is not None
    ]
    print(f"  {len(games)} usable (both pitchers + first-inning runs)", flush=True)

    # Group by season (year from game_date)
    by_season = defaultdict(list)
    for g in games:
        by_season[int(g["game_date"][:4])].append(g)

    grand_total = 0

    for season in sorted(by_season):
        sg = by_season[season]
        print(f"\n{'='*55}")
        print(f"  SEASON {season}  ({len(sg)} games)")
        print(f"{'='*55}")

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
        })

        processed = 0
        errors = 0

        for i, game in enumerate(sg):
            pbp = fetch_play_by_play(game["game_pk"])
            if pbp is None:
                errors += 1
                if (i + 1) % 200 == 0:
                    print(f"  {i+1}/{len(sg)} (errors={errors})", flush=True)
                time.sleep(0.25)
                continue

            # Home pitcher pitches top of 1st; away pitcher pitches bottom of 1st
            for side, half, runs_col in [
                ("home", "top", "first_inn_away_runs"),
                ("away", "bottom", "first_inn_home_runs"),
            ]:
                pid = game[f"{side}_pitcher_id"]
                runs = game[runs_col]

                ps = parse_first_inning(pbp, pid, half)
                if ps["batters_faced"] == 0:
                    continue  # pitcher didn't record any PAs (last-minute change?)

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

            processed += 1
            if (i + 1) % 200 == 0:
                print(f"  {i+1}/{len(sg)} (errors={errors})", flush=True)

            time.sleep(0.25)

        print(f"  Done: {processed} games, {errors} errors, {len(agg)} pitchers")

        # Build upsert rows
        rows = []
        for pid, stats in agg.items():
            row = {"mlb_player_id": pid, "season": season}
            row.update(stats)
            rows.append(row)

        print(f"  Upserting {len(rows)} pitcher-season rows ...", end=" ", flush=True)
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
        print(f"  >> {tot_starts} pitcher-starts | scoreless {tot_scoreless}/{tot_starts} = {rate:.3f}")
        print(f"  >> {tot_bf} BF | K/BF {k_per_bf:.3f} | H/BF {h_per_bf:.3f}")

        grand_total += len(agg)

    print(f"\n{'='*55}")
    print(f"  COMPLETE — {grand_total} pitcher-season rows updated")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
