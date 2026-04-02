#!/usr/bin/env python3
"""Seed platoon_splits table from MLB Stats API statSplits endpoint.

Fetches vs-LHP and vs-RHP splits for all batters (PA >= 50) and
vs-LHB and vs-RHB splits for all starting pitchers (GS >= 3)
across 2019-2025 seasons.
"""

import os
import requests
import time
import math
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

UPSERT_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

READ_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
SEASONS = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
API_DELAY = 0.3  # seconds between MLB API calls
MIN_PA = 10  # minimum PA to store a split


def safe_float(val, decimals=4):
    """Convert to float rounded to `decimals` places; return None if NaN/Inf."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


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


def fetch_player_seasons(table, filter_col, filter_op, filter_val):
    """Fetch distinct (mlb_player_id, season) from a stats table via Supabase REST."""
    results = []
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {
        "select": "mlb_player_id,season",
        filter_col: f"{filter_op}.{filter_val}",
    }
    # Supabase REST returns max 1000 rows by default; paginate with Range header
    offset = 0
    page_size = 1000
    while True:
        headers = {
            **READ_HEADERS,
            "Range": f"{offset}-{offset + page_size - 1}",
            "Prefer": "count=exact",
        }
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code not in (200, 206):
            print(f"  ERROR fetching {table}: HTTP {r.status_code}", flush=True)
            break
        batch = r.json()
        if not batch:
            break
        results.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    # Deduplicate (mlb_player_id, season) tuples
    seen = set()
    deduped = []
    for row in results:
        key = (row["mlb_player_id"], row["season"])
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def fetch_splits(mlb_player_id, season, group):
    """Fetch platoon splits from MLB Stats API.

    Args:
        group: 'hitting' for batters, 'pitching' for pitchers

    Returns list of (split_description, stat_dict) tuples, or empty list on error.
    """
    url = (
        f"{MLB_API_BASE}/people/{mlb_player_id}/stats"
        f"?stats=statSplits&group={group}&gameType=R"
        f"&sitCodes=vl,vr&season={season}"
    )
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []

    results = []
    stats_list = data.get("stats", [])
    if not stats_list:
        return []

    for stat_group in stats_list:
        for split_entry in stat_group.get("splits", []):
            split_info = split_entry.get("split", {})
            desc = split_info.get("description", "")
            stat = split_entry.get("stat", {})
            if desc and stat:
                results.append((desc, stat))
    return results


def parse_split_row(mlb_player_id, season, player_type, split_label, stat):
    """Parse MLB API stat dict into a platoon_splits row dict.

    Returns the row dict, or None if PA < MIN_PA.
    """
    # Batters have plateAppearances; pitchers have battersFaced
    pa = int(stat.get("plateAppearances") or stat.get("battersFaced") or 0)
    if pa < MIN_PA:
        return None

    so = int(stat.get("strikeOuts", 0) or 0)
    bb = int(stat.get("baseOnBalls", 0) or 0)
    hbp = int(stat.get("hitByPitch", 0) or 0)
    hr = int(stat.get("homeRuns", 0) or 0)
    hits = int(stat.get("hits", 0) or 0)
    doubles = int(stat.get("doubles", 0) or 0)
    triples = int(stat.get("triples", 0) or 0)
    singles = hits - doubles - triples - hr

    return {
        "mlb_player_id": mlb_player_id,
        "season": season,
        "player_type": player_type,
        "split": split_label,
        "pa": pa,
        "k_rate": safe_float(so / pa),
        "bb_rate": safe_float(bb / pa),
        "hbp_rate": safe_float(hbp / pa),
        "hr_rate": safe_float(hr / pa),
        "single_rate": safe_float(singles / pa),
        "double_rate": safe_float(doubles / pa),
        "triple_rate": safe_float(triples / pa),
        "woba": None,
        "xwoba": None,
    }


# Mapping from MLB API split description to our split labels
BATTER_SPLIT_MAP = {
    "vs Left": "vs_LHP",
    "vs Right": "vs_RHP",
}
PITCHER_SPLIT_MAP = {
    "vs Left": "vs_LHB",
    "vs Right": "vs_RHB",
}


def process_season(season, batter_ids, pitcher_ids):
    """Process all players for a single season. Returns count of rows inserted."""
    rows = []

    # --- Batters ---
    batter_count = len(batter_ids)
    print(f"  Batters: {batter_count} to fetch", flush=True)
    for i, mlb_id in enumerate(batter_ids, 1):
        splits = fetch_splits(mlb_id, season, "hitting")
        for desc, stat in splits:
            label = BATTER_SPLIT_MAP.get(desc)
            if not label:
                continue
            row = parse_split_row(mlb_id, season, "batter", label, stat)
            if row:
                rows.append(row)
        if i % 100 == 0:
            print(f"    {i}/{batter_count} batters ...", flush=True)
        time.sleep(API_DELAY)

    # --- Pitchers ---
    pitcher_count = len(pitcher_ids)
    print(f"  Pitchers: {pitcher_count} to fetch", flush=True)
    for i, mlb_id in enumerate(pitcher_ids, 1):
        splits = fetch_splits(mlb_id, season, "pitching")
        for desc, stat in splits:
            label = PITCHER_SPLIT_MAP.get(desc)
            if not label:
                continue
            row = parse_split_row(mlb_id, season, "pitcher", label, stat)
            if row:
                rows.append(row)
        if i % 100 == 0:
            print(f"    {i}/{pitcher_count} pitchers ...", flush=True)
        time.sleep(API_DELAY)

    # Deduplicate: MLB API can return multiple entries for traded players.
    # Keep the last entry (highest PA) for each unique key.
    seen = {}
    for row in rows:
        key = (row["mlb_player_id"], row["season"], row["player_type"], row["split"])
        if key not in seen or row["pa"] > seen[key]["pa"]:
            seen[key] = row
    deduped = list(seen.values())

    before = len(rows)
    after = len(deduped)
    if before != after:
        print(f"  Deduplicated: {before} -> {after} rows ({before - after} dupes removed)", flush=True)

    # Upsert all rows for this season
    sb_upsert(
        "platoon_splits", deduped,
        on_conflict="mlb_player_id,season,player_type,split",
    )
    return len(deduped)


def main():
    print("Fetching player-season lists from Supabase ...", flush=True)

    # Get all (mlb_player_id, season) pairs
    batter_seasons = fetch_player_seasons("batter_stats", "pa", "gte", 50)
    pitcher_seasons = fetch_player_seasons("pitcher_stats", "games_started", "gte", 3)

    print(f"  {len(batter_seasons)} batter-seasons, {len(pitcher_seasons)} pitcher-seasons", flush=True)

    # Group by season
    batters_by_season = {}
    for mlb_id, season in batter_seasons:
        batters_by_season.setdefault(season, []).append(mlb_id)

    pitchers_by_season = {}
    for mlb_id, season in pitcher_seasons:
        pitchers_by_season.setdefault(season, []).append(mlb_id)

    total_rows = 0

    for season in SEASONS:
        b_ids = batters_by_season.get(season, [])
        p_ids = pitchers_by_season.get(season, [])
        if not b_ids and not p_ids:
            print(f"\n  Season {season}: no players, skipping", flush=True)
            continue

        print(f"\n{'='*50}", flush=True)
        print(f"  SEASON {season}", flush=True)
        print(f"{'='*50}", flush=True)

        count = process_season(season, b_ids, p_ids)
        total_rows += count
        print(f"  {count} split rows inserted for {season}", flush=True)

    print(f"\n{'='*50}", flush=True)
    print(f"  COMPLETE", flush=True)
    print(f"  Total platoon_splits rows: {total_rows}", flush=True)
    print(f"{'='*50}", flush=True)


if __name__ == "__main__":
    main()
