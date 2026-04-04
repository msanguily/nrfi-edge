#!/usr/bin/env python3
"""Weekly refresh: update current-season player stats, platoon splits, retrain calibrator.

cron: 0 6 * * 1 (6:00 AM ET, Mondays)
"""

import json
import math
import sys
import time
import traceback
import warnings

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

warnings.filterwarnings("ignore")

from scripts.utils import get_today_et, setup_logging, get_supabase_client

logger = setup_logging("weekly_refresh").getChild("weekly_refresh")

# ---------------------------------------------------------------------------
# Helpers (shared with seed_player_stats.py)
# ---------------------------------------------------------------------------

def safe_float(val, decimals=4):
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


def num(row, col, default=0):
    v = row.get(col)
    if v is None:
        return default
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


_HIT_TOTAL = 0.152 + 0.046 + 0.005
SINGLE_PCT = 0.152 / _HIT_TOTAL
DOUBLE_PCT = 0.046 / _HIT_TOTAL
TRIPLE_PCT = 0.005 / _HIT_TOTAL


# ---------------------------------------------------------------------------
# Step 1: Refresh pitcher stats (current season)
# ---------------------------------------------------------------------------

def _early_season(season) -> bool:
    """True if we're in the first 4 weeks of the season (lower data thresholds)."""
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    # Opening Day is typically late March / early April
    season_start = _date(season, 3, 27)
    return today < season_start + _td(days=28)


def refresh_pitcher_stats(db, season, fg_to_mlb):
    """Refresh pitcher stats for the current season from FanGraphs via pybaseball."""
    from pybaseball import pitching_stats

    early = _early_season(season)
    min_gs = 1 if early else 3

    logger.info("Fetching %d pitching stats (min GS=%d) ...", season, min_gs)
    pit = pitching_stats(season, qual=0)
    pit = pit[pit["GS"] >= min_gs]
    logger.info("Found %d pitchers (GS >= %d)", len(pit), min_gs)

    count = 0
    for _, row in pit.iterrows():
        fg_id = row.get("IDfg")
        if fg_id is None:
            continue
        try:
            fg_id = int(float(fg_id))
        except (ValueError, TypeError):
            continue
        if fg_id not in fg_to_mlb:
            continue

        mlb_id = fg_to_mlb[fg_id]
        tbf = num(row, "TBF")
        if tbf == 0:
            continue

        h = num(row, "H")
        hr = num(row, "HR")
        non_hr_hits = max(h - hr, 0)

        # Upsert player
        db.table("players").upsert({
            "mlb_player_id": mlb_id,
            "name": str(row["Name"]),
            "position": "P",
        }, on_conflict="mlb_player_id").execute()

        # Upsert stats
        db.table("pitcher_stats").upsert({
            "mlb_player_id": mlb_id,
            "season": season,
            "games_started": int(float(row.get("GS", 0) or 0)),
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
        }, on_conflict="mlb_player_id,season").execute()
        count += 1

    logger.info("Upserted %d pitcher stat rows for %d", count, season)
    return count


# ---------------------------------------------------------------------------
# Step 2: Refresh batter stats (current season)
# ---------------------------------------------------------------------------

def refresh_batter_stats(db, season, fg_to_mlb):
    """Refresh batter stats for the current season from FanGraphs via pybaseball."""
    from pybaseball import batting_stats

    early = _early_season(season)
    min_pa = 1 if early else 50

    logger.info("Fetching %d batting stats (min PA=%d) ...", season, min_pa)
    bat = batting_stats(season, qual=0)
    bat = bat[bat["PA"] >= min_pa]
    logger.info("Found %d batters (PA >= %d)", len(bat), min_pa)

    count = 0
    for _, row in bat.iterrows():
        fg_id = row.get("IDfg")
        if fg_id is None:
            continue
        try:
            fg_id = int(float(fg_id))
        except (ValueError, TypeError):
            continue
        if fg_id not in fg_to_mlb:
            continue

        mlb_id = fg_to_mlb[fg_id]
        pa = num(row, "PA")
        if pa == 0:
            continue

        bat_hand = row.get("Bat")
        bat_hand = str(bat_hand) if bat_hand is not None and str(bat_hand) != "nan" else None
        pos = row.get("Pos")
        pos = str(pos) if pos is not None and str(pos) != "nan" else None

        # Upsert player
        db.table("players").upsert({
            "mlb_player_id": mlb_id,
            "name": str(row["Name"]),
            "bats": bat_hand,
            "position": pos,
        }, on_conflict="mlb_player_id").execute()

        # Upsert stats
        db.table("batter_stats").upsert({
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
        }, on_conflict="mlb_player_id,season").execute()
        count += 1

    logger.info("Upserted %d batter stat rows for %d", count, season)
    return count


# ---------------------------------------------------------------------------
# Step 3: Refresh platoon splits (current season)
# ---------------------------------------------------------------------------

def refresh_platoon_splits(db, season):
    """Refresh platoon splits for all players with current-season stats."""
    import requests as req

    MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
    MIN_PA = 10
    API_DELAY = 0.3

    BATTER_SPLIT_MAP = {"vs Left": "vs_LHP", "vs Right": "vs_RHP"}
    PITCHER_SPLIT_MAP = {"vs Left": "vs_LHB", "vs Right": "vs_RHB"}

    # Get all batters with stats this season
    batter_resp = (
        db.table("batter_stats")
        .select("mlb_player_id")
        .eq("season", season)
        .execute()
    )
    batter_ids = [r["mlb_player_id"] for r in (batter_resp.data or [])]

    # Get all pitchers with stats this season
    pitcher_resp = (
        db.table("pitcher_stats")
        .select("mlb_player_id")
        .eq("season", season)
        .execute()
    )
    pitcher_ids = [r["mlb_player_id"] for r in (pitcher_resp.data or [])]

    logger.info("Refreshing splits: %d batters, %d pitchers", len(batter_ids), len(pitcher_ids))

    rows = []

    def fetch_splits(mlb_id, group):
        url = (
            f"{MLB_API_BASE}/people/{mlb_id}/stats"
            f"?stats=statSplits&group={group}&gameType=R"
            f"&sitCodes=vl,vr&season={season}"
        )
        try:
            r = req.get(url, timeout=15)
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception:
            return []
        results = []
        for sg in data.get("stats", []):
            for se in sg.get("splits", []):
                desc = se.get("split", {}).get("description", "")
                stat = se.get("stat", {})
                if desc and stat:
                    results.append((desc, stat))
        return results

    def parse_split(mlb_id, player_type, label, stat):
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
            "mlb_player_id": mlb_id, "season": season,
            "player_type": player_type, "split": label, "pa": pa,
            "k_rate": safe_float(so / pa), "bb_rate": safe_float(bb / pa),
            "hbp_rate": safe_float(hbp / pa), "hr_rate": safe_float(hr / pa),
            "single_rate": safe_float(singles / pa), "double_rate": safe_float(doubles / pa),
            "triple_rate": safe_float(triples / pa),
        }

    for i, mlb_id in enumerate(batter_ids, 1):
        for desc, stat in fetch_splits(mlb_id, "hitting"):
            label = BATTER_SPLIT_MAP.get(desc)
            if label:
                row = parse_split(mlb_id, "batter", label, stat)
                if row:
                    rows.append(row)
        if i % 100 == 0:
            logger.info("  Batters: %d/%d", i, len(batter_ids))
        time.sleep(API_DELAY)

    for i, mlb_id in enumerate(pitcher_ids, 1):
        for desc, stat in fetch_splits(mlb_id, "pitching"):
            label = PITCHER_SPLIT_MAP.get(desc)
            if label:
                row = parse_split(mlb_id, "pitcher", label, stat)
                if row:
                    rows.append(row)
        if i % 100 == 0:
            logger.info("  Pitchers: %d/%d", i, len(pitcher_ids))
        time.sleep(API_DELAY)

    # Upsert all splits
    for row in rows:
        db.table("platoon_splits").upsert(
            row, on_conflict="mlb_player_id,season,player_type,split"
        ).execute()

    logger.info("Upserted %d platoon split rows for %d", len(rows), season)
    return len(rows)


# ---------------------------------------------------------------------------
# Step 4: Retrain calibrator
# ---------------------------------------------------------------------------

def retrain_calibrator(db):
    """Retrain isotonic calibrator on all predictions with results."""
    import numpy as np
    from src.calibration.calibrator import NRFICalibrator

    cal_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "calibrator.json",
    )

    # Load old calibrator to compare
    old_size = 0
    if os.path.exists(cal_path):
        with open(cal_path, "r") as f:
            old_data = json.load(f)
            old_size = old_data.get("training_size", 0)

    # Fetch all predictions with results and raw probabilities
    # Paginate since there may be many rows
    all_preds = []
    offset = 0
    page_size = 1000
    while True:
        resp = (
            db.table("predictions")
            .select("p_nrfi_combined, result")
            .not_.is_("result", "null")
            .not_.is_("p_nrfi_combined", "null")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        if not resp.data:
            break
        all_preds.extend(resp.data)
        if len(resp.data) < page_size:
            break
        offset += page_size

    if len(all_preds) < 100:
        logger.warning("Only %d predictions with results — skipping retrain (need 100+)", len(all_preds))
        return 0

    raw_probs = np.array([float(p["p_nrfi_combined"]) for p in all_preds])
    outcomes = np.array([1.0 if p["result"] else 0.0 for p in all_preds])

    cal = NRFICalibrator()
    cal.fit(raw_probs, outcomes)
    cal.save(cal_path)

    logger.info(
        "Calibrator retrained: %d samples (was %d), saved to %s",
        len(all_preds), old_size, cal_path,
    )
    return len(all_preds)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    today = get_today_et()
    season = today.year
    logger.info("=== Weekly Refresh: season %d ===", season)

    db = get_supabase_client()
    errors = 0

    # Build FanGraphs → MLB ID map
    logger.info("Loading Chadwick register ...")
    try:
        from pybaseball import chadwick_register
        reg = chadwick_register()
        fg_to_mlb = {}
        for _, row in reg.iterrows():
            fg = row.get("key_fangraphs")
            mlb = row.get("key_mlbam")
            if fg is not None and mlb is not None:
                try:
                    fg_to_mlb[int(float(fg))] = int(float(mlb))
                except (ValueError, TypeError):
                    pass
        logger.info("Chadwick register: %d entries", len(fg_to_mlb))
    except Exception:
        logger.error("Failed to load Chadwick register:\n%s", traceback.format_exc())
        return 1

    # Step 1: Pitcher stats
    n_pitchers = 0
    try:
        n_pitchers = refresh_pitcher_stats(db, season, fg_to_mlb)
    except Exception:
        logger.error("Pitcher stats refresh failed:\n%s", traceback.format_exc())
        errors += 1

    # Step 2: Batter stats
    n_batters = 0
    try:
        n_batters = refresh_batter_stats(db, season, fg_to_mlb)
    except Exception:
        logger.error("Batter stats refresh failed:\n%s", traceback.format_exc())
        errors += 1

    # Step 3: Platoon splits
    n_splits = 0
    try:
        n_splits = refresh_platoon_splits(db, season)
    except Exception:
        logger.error("Platoon splits refresh failed:\n%s", traceback.format_exc())
        errors += 1

    # Step 4: Retrain calibrator
    n_cal = 0
    try:
        n_cal = retrain_calibrator(db)
    except Exception:
        logger.error("Calibrator retrain failed:\n%s", traceback.format_exc())
        errors += 1

    logger.info(
        "Summary: %d pitchers, %d batters updated, %d splits refreshed, calibrator retrained (%d samples)",
        n_pitchers, n_batters, n_splits, n_cal,
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
