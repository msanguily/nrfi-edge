#!/usr/bin/env python3
"""Seed pitcher_stats and batter_stats tables from pybaseball (FanGraphs data) 2019-2026."""

import os
import requests
import time
import math
import warnings
import sys
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from pybaseball import pitching_stats, batting_stats, chadwick_register

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

UPSERT_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

SEASONS = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]

# League-average hit-type proportions for splitting pitcher H into 1B/2B/3B.
# From league_averages: single≈0.152, double≈0.046, triple≈0.005 → total 0.203
_HIT_TOTAL = 0.152 + 0.046 + 0.005
SINGLE_PCT = 0.152 / _HIT_TOTAL  # ~0.749
DOUBLE_PCT = 0.046 / _HIT_TOTAL  # ~0.227
TRIPLE_PCT = 0.005 / _HIT_TOTAL  # ~0.025


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


def safe_int(val):
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f):
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def num(row, col, default=0):
    """Get a numeric value from a row, returning default if missing/NaN."""
    v = row.get(col)
    if v is None:
        return default
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Chadwick register: FanGraphs ID → MLB ID
# ---------------------------------------------------------------------------
def build_id_map():
    print("Loading chadwick register ...", end=" ", flush=True)
    reg = chadwick_register()
    fg_to_mlb = {}
    for _, row in reg.iterrows():
        fg = row.get("key_fangraphs")
        mlb = row.get("key_mlbam")
        if fg is not None and mlb is not None:
            try:
                fg_i, mlb_i = int(float(fg)), int(float(mlb))
                fg_to_mlb[fg_i] = mlb_i
            except (ValueError, TypeError):
                pass
    print(f"{len(fg_to_mlb)} entries", flush=True)
    return fg_to_mlb


# ---------------------------------------------------------------------------
# Supabase REST helpers
# ---------------------------------------------------------------------------
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
# Main
# ---------------------------------------------------------------------------
def main():
    fg_to_mlb = build_id_map()

    total_pitchers = 0
    total_batters = 0

    for season in SEASONS:
        print(f"\n{'='*50}", flush=True)
        print(f"  SEASON {season}", flush=True)
        print(f"{'='*50}", flush=True)

        # ── PITCHING ─────────────────────────────────────
        print(f"  Fetching pitching stats ...", end=" ", flush=True)
        try:
            pit = pitching_stats(season, qual=0)
        except Exception as e:
            print(f"ERROR: {e}", flush=True)
            time.sleep(10)
            continue
        pit = pit[pit["GS"] >= 3]
        print(f"{len(pit)} pitchers (GS >= 3)", flush=True)

        p_players = []
        p_stats = []
        skipped_p = 0

        for _, row in pit.iterrows():
            fg_id = safe_int(row.get("IDfg"))
            if fg_id is None or fg_id not in fg_to_mlb:
                skipped_p += 1
                continue
            mlb_id = fg_to_mlb[fg_id]

            tbf = num(row, "TBF")
            if tbf == 0:
                continue

            h = num(row, "H")
            hr = num(row, "HR")
            non_hr_hits = max(h - hr, 0)

            p_players.append(
                {"mlb_player_id": mlb_id, "name": str(row["Name"]), "position": "P"}
            )
            p_stats.append(
                {
                    "mlb_player_id": mlb_id,
                    "season": season,
                    "games_started": safe_int(row.get("GS")),
                    "innings_pitched": safe_float(row.get("IP"), 1),
                    "era": safe_float(row.get("ERA"), 2),
                    "fip": safe_float(row.get("FIP"), 2),
                    "whip": safe_float(row.get("WHIP"), 3),
                    "k_rate": safe_float(row.get("K%"), 3),
                    "bb_rate": safe_float(row.get("BB%"), 3),
                    "hbp_rate": safe_float(num(row, "HBP") / tbf),
                    "hr_rate": safe_float(hr / tbf),
                    "single_rate": safe_float(non_hr_hits * SINGLE_PCT / tbf, 3),
                    "double_rate": safe_float(non_hr_hits * DOUBLE_PCT / tbf, 3),
                    "triple_rate": safe_float(non_hr_hits * TRIPLE_PCT / tbf),
                    "gb_rate": safe_float(row.get("GB%"), 3),
                }
            )

        sb_upsert("players", p_players)
        sb_upsert("pitcher_stats", p_stats, on_conflict="mlb_player_id,season")
        print(
            f"  Pitching done: {len(p_stats)} inserted, {skipped_p} skipped (no MLB ID)",
            flush=True,
        )
        total_pitchers += len(p_stats)
        time.sleep(8)

        # ── BATTING ──────────────────────────────────────
        print(f"  Fetching batting stats ...", end=" ", flush=True)
        try:
            bat = batting_stats(season, qual=0)
        except Exception as e:
            print(f"ERROR: {e}", flush=True)
            time.sleep(10)
            continue
        bat = bat[bat["PA"] >= 50]
        print(f"{len(bat)} batters (PA >= 50)", flush=True)

        b_players = []
        b_stats = []
        skipped_b = 0

        for _, row in bat.iterrows():
            fg_id = safe_int(row.get("IDfg"))
            if fg_id is None or fg_id not in fg_to_mlb:
                skipped_b += 1
                continue
            mlb_id = fg_to_mlb[fg_id]

            pa = num(row, "PA")
            if pa == 0:
                continue

            bat_hand = row.get("Bat")
            bat_hand = str(bat_hand) if bat_hand is not None and str(bat_hand) != "nan" else None
            pos = row.get("Pos")
            pos = str(pos) if pos is not None and str(pos) != "nan" else None

            b_players.append(
                {
                    "mlb_player_id": mlb_id,
                    "name": str(row["Name"]),
                    "bats": bat_hand,
                    "position": pos,
                }
            )
            b_stats.append(
                {
                    "mlb_player_id": mlb_id,
                    "season": season,
                    "pa": int(pa),
                    "avg": safe_float(row.get("AVG"), 3),
                    "obp": safe_float(row.get("OBP"), 3),
                    "slg": safe_float(row.get("SLG"), 3),
                    "woba": safe_float(row.get("wOBA"), 3),
                    "xwoba": safe_float(row.get("xwOBA"), 3),
                    "k_rate": safe_float(row.get("K%"), 3),
                    "bb_rate": safe_float(row.get("BB%"), 3),
                    "hr_rate": safe_float(num(row, "HR") / pa),
                    "single_rate": safe_float(num(row, "1B") / pa, 3),
                    "double_rate": safe_float(num(row, "2B") / pa, 3),
                    "triple_rate": safe_float(num(row, "3B") / pa),
                    "hbp_rate": safe_float(num(row, "HBP") / pa),
                }
            )

        sb_upsert("players", b_players)
        sb_upsert("batter_stats", b_stats, on_conflict="mlb_player_id,season")
        print(
            f"  Batting done: {len(b_stats)} inserted, {skipped_b} skipped (no MLB ID)",
            flush=True,
        )
        total_batters += len(b_stats)
        time.sleep(8)

    print(f"\n{'='*50}", flush=True)
    print(f"  COMPLETE", flush=True)
    print(f"  Total pitcher-season rows: {total_pitchers}", flush=True)
    print(f"  Total batter-season rows:  {total_batters}", flush=True)
    print(f"{'='*50}", flush=True)


if __name__ == "__main__":
    main()
