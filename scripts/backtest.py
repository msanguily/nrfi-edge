#!/usr/bin/env python3
"""
Phase 3 Backtest: Run NRFI predictions on all historical games 2019-2026.

Bulk-loads all data from Supabase into memory, then runs the Markov chain
prediction pipeline for every game with complete data (lineups + pitcher stats).

Evaluates: Brier Score, ECE, calibration curve.
Trains isotonic regression on 2019-2025, tests on 2026 (out-of-sample).
"""

import sys
import os
import time
import json
import requests
import numpy as np
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from src.markov.odds_ratio import compute_matchup_rates, compute_weighted_rate, apply_marcel_shrinkage
from src.markov.chain import compute_p_zero_runs, compute_gidp_fraction
from src.markov.adjustments import (
    apply_all_adjustments,
    adjust_for_first_inning,
    adjust_for_first_inning_top,
    adjust_for_first_inning_bottom,
)
from src.calibration.calibrator import NRFICalibrator, compute_ece, compute_calibration_curve

# ---------------------------------------------------------------------------
# Supabase config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

OUTCOME_KEYS = ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']
BF_PER_IP = 4.3
MODEL_VERSION = '0.4.0'

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def sb_fetch_all(table, params=None, select="*"):
    """Paginate through Supabase REST API to fetch all rows."""
    rows = []
    offset = 0
    limit = 1000
    if params is None:
        params = {}
    params["select"] = select
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SB_HEADERS, "Range": f"{offset}-{offset + limit - 1}"},
            params=params,
            timeout=60,
        )
        if r.status_code not in (200, 206):
            print(f"  Error fetching {table}: {r.status_code} {r.text[:200]}")
            break
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return rows


def load_all_data():
    """Bulk-load all tables from Supabase into memory."""
    data = {}

    tables = [
        ("games", {"game_type": "eq.regular", "status": "eq.final", "order": "game_date"}),
        ("lineups", {}),
        ("players", {}),
        ("pitcher_stats", {}),
        ("batter_stats", {}),
        ("platoon_splits", {}),
        ("parks", {}),
        ("league_averages", {}),
        ("weather_snapshots", {}),
    ]

    for table_name, params in tables:
        t0 = time.time()
        data[table_name] = sb_fetch_all(table_name, params)
        elapsed = time.time() - t0
        print(f"  Loaded {table_name}: {len(data[table_name]):,} rows ({elapsed:.1f}s)")

    return data


def build_indexes(data):
    """Build dict-based indexes for fast in-memory lookups."""
    idx = {}

    # Games by game_pk
    idx['games'] = {g['game_pk']: g for g in data['games']}

    # Lineups by (game_pk, team_id) -> sorted list
    lineups_map = {}
    for row in data['lineups']:
        key = (row['game_pk'], row['team_id'])
        if key not in lineups_map:
            lineups_map[key] = []
        lineups_map[key].append(row)
    # Sort each lineup by batting_order
    for key in lineups_map:
        lineups_map[key].sort(key=lambda r: r['batting_order'])
    idx['lineups'] = lineups_map

    # Players by mlb_player_id
    idx['players'] = {p['mlb_player_id']: p for p in data['players']}

    # Pitcher stats by (mlb_player_id, season)
    idx['pitcher_stats'] = {}
    for row in data['pitcher_stats']:
        key = (row['mlb_player_id'], row['season'])
        idx['pitcher_stats'][key] = row

    # Batter stats by (mlb_player_id, season)
    idx['batter_stats'] = {}
    for row in data['batter_stats']:
        key = (row['mlb_player_id'], row['season'])
        idx['batter_stats'][key] = row

    # Platoon splits by (mlb_player_id, season, player_type, split)
    idx['platoon_splits'] = {}
    for row in data['platoon_splits']:
        key = (row['mlb_player_id'], row['season'], row['player_type'], row['split'])
        idx['platoon_splits'][key] = row

    # Parks by park_id
    idx['parks'] = {p['park_id']: p for p in data['parks']}

    # League averages by season
    idx['league_averages'] = {r['season']: r for r in data['league_averages']}

    # Weather by game_pk
    idx['weather'] = {w['game_pk']: w for w in data.get('weather_snapshots', [])}

    return idx


# ---------------------------------------------------------------------------
# Rate extraction helpers (mirrors predict.py)
# ---------------------------------------------------------------------------

def extract_rates(row):
    """Pull outcome rates from a stats/split row."""
    return {k: float(row.get(f'{k}_rate', 0) or 0) for k in OUTCOME_KEYS}


def estimate_bf(innings_pitched):
    """Estimate batters faced from innings pitched."""
    if innings_pitched is None or float(innings_pitched or 0) <= 0:
        return 0
    return int(round(float(innings_pitched) * BF_PER_IP))


def get_marcel_weighted_rates(player_id, current_season, table, idx, league_rates):
    """
    Marcel multi-year weighted rates with 5/4/3 weighting.
    Uses in-memory indexes instead of DB calls.
    """
    weights = [5, 4, 3]
    seasons = [current_season, current_season - 1, current_season - 2]
    stats_idx = idx[table]

    rows_by_season = {}
    for s in seasons:
        row = stats_idx.get((player_id, s))
        if row is not None:
            rows_by_season[s] = row

    if not rows_by_season:
        return None

    result = {}
    for key in OUTCOME_KEYS:
        col = f'{key}_rate'
        rates_list = []
        pa_list = []
        w_list = []

        for i, s in enumerate(seasons):
            row = rows_by_season.get(s)
            if row is None:
                continue
            rate_val = row.get(col)
            if rate_val is None:
                continue

            rates_list.append(float(rate_val))

            if table == 'batter_stats':
                pa_list.append(int(row.get('pa', 0) or 0))
            else:
                pa_list.append(estimate_bf(row.get('innings_pitched', 0)))

            w_list.append(weights[i])

        if not rates_list:
            result[key] = league_rates.get(key, 0.0)
            continue

        result[key] = compute_weighted_rate(
            rates_list, pa_list, league_rates.get(key, 0.0), weights=w_list,
        )

    return result


PLATOON_SHRINKAGE_CONSTANT = 500


def get_best_split_rates(player_id, season, player_type, opponent_hand, idx,
                         overall_rates=None):
    """Fetch platoon split rates from in-memory index, shrunk toward overall rates."""
    if player_type == 'batter':
        split = f'vs_{"RHP" if opponent_hand == "R" else "LHP"}'
    else:
        split = f'vs_{"RHB" if opponent_hand == "R" else "LHB"}'

    row = idx['platoon_splits'].get((player_id, season, player_type, split))
    if row is None:
        return None
    split_pa = int(row.get('pa', 0) or 0)
    if split_pa < 30:
        return None

    raw_rates = extract_rates(row)

    # Apply shrinkage toward overall player rates if available
    if overall_rates is not None:
        shrunk = {}
        for key in OUTCOME_KEYS:
            shrunk[key] = apply_marcel_shrinkage(
                raw_rates[key], overall_rates[key], split_pa,
                shrinkage_constant=PLATOON_SHRINKAGE_CONSTANT,
            )
        return shrunk

    return raw_rates


# ---------------------------------------------------------------------------
# Prediction logic (mirrors predict.py but uses in-memory data)
# ---------------------------------------------------------------------------

def predict_game(game, idx):
    """
    Produce P(NRFI) prediction for a single game using in-memory data.
    Returns dict with prediction data, or None if incomplete data.
    """
    game_pk = game['game_pk']
    season = int(str(game['game_date'])[:4])

    home_team_id = game['home_team_id']
    away_team_id = game['away_team_id']
    home_pitcher_id = game.get('home_pitcher_id')
    away_pitcher_id = game.get('away_pitcher_id')
    park_id = game.get('park_id')

    # Must have both pitchers
    if not home_pitcher_id or not away_pitcher_id:
        return None

    # Must have lineups for both teams
    away_lineup = idx['lineups'].get((game_pk, away_team_id))
    home_lineup = idx['lineups'].get((game_pk, home_team_id))
    if not away_lineup or not home_lineup:
        return None

    # Must have league averages
    league_row = idx['league_averages'].get(season)
    if not league_row:
        return None
    league_rates = extract_rates(league_row)

    # Park factors (all hit types)
    park = idx['parks'].get(park_id, {})
    park_hr_factor = float(park.get('hr_factor', 100) or 100)
    park_single_factor = float(park.get('single_factor', 100) or 100)
    park_double_factor = float(park.get('double_factor', 100) or 100)
    park_triple_factor = float(park.get('triple_factor', 100) or 100)

    # Weather data
    weather = idx['weather'].get(game_pk, {})
    is_dome_closed = bool(weather.get('is_dome_closed'))
    if is_dome_closed:
        temperature_f = 75.0  # neutral for dome games
        wind_speed_mph = 0.0
        wind_relative = 'calm'
    else:
        temperature_f = float(weather.get('temperature_f') or 75.0)
        wind_speed_mph = float(weather.get('wind_speed_mph') or 0.0)
        wind_relative = weather.get('wind_relative') or 'calm'

    # Pitcher handedness
    home_pitcher_info = idx['players'].get(home_pitcher_id, {})
    away_pitcher_info = idx['players'].get(away_pitcher_id, {})
    home_pitcher_hand = home_pitcher_info.get('throws', 'R') or 'R'
    away_pitcher_hand = away_pitcher_info.get('throws', 'R') or 'R'

    # --- Top of 1st: away batters vs home pitcher ---
    away_rates = build_half_inning_rates(
        away_lineup, home_pitcher_id, home_pitcher_hand,
        season, league_rates,
        park_hr_factor, park_single_factor, park_double_factor, park_triple_factor,
        temperature_f, wind_speed_mph, wind_relative, idx,
        half='top',
    )
    if away_rates is None:
        return None

    p_nrfi_top = compute_p_zero_runs(away_rates, max_batters=9)

    # --- Bottom of 1st: home batters vs away pitcher ---
    home_rates = build_half_inning_rates(
        home_lineup, away_pitcher_id, away_pitcher_hand,
        season, league_rates,
        park_hr_factor, park_single_factor, park_double_factor, park_triple_factor,
        temperature_f, wind_speed_mph, wind_relative, idx,
        half='bottom',
    )
    if home_rates is None:
        return None

    p_nrfi_bottom = compute_p_zero_runs(home_rates, max_batters=9)

    # --- Combine ---
    p_nrfi_combined = p_nrfi_top * p_nrfi_bottom

    # Actual result
    nrfi_result = game.get('nrfi_result')

    return {
        'game_pk': game_pk,
        'season': season,
        'game_date': game['game_date'],
        'p_nrfi_top': p_nrfi_top,
        'p_nrfi_bottom': p_nrfi_bottom,
        'p_nrfi_combined': p_nrfi_combined,
        'nrfi_result': nrfi_result,
    }


def build_half_inning_rates(lineup, pitcher_id, pitcher_hand, season,
                            league_rates,
                            park_hr_factor, park_single_factor,
                            park_double_factor, park_triple_factor,
                            temperature_f, wind_speed_mph, wind_relative,
                            idx, half='top'):
    """
    Build adjusted matchup rates for each batter in a lineup.
    Returns list of rate dicts, or None if pitcher has no stats.
    """
    # Pitcher Marcel-weighted rates
    pitcher_rates = get_marcel_weighted_rates(
        pitcher_id, season, 'pitcher_stats', idx, league_rates,
    )
    if pitcher_rates is None:
        pitcher_rates = dict(league_rates)

    # Pitcher GB rate for GIDP computation
    pitcher_stats_row = idx['pitcher_stats'].get((pitcher_id, season))
    pitcher_gb_rate = None
    if pitcher_stats_row and pitcher_stats_row.get('gb_rate'):
        pitcher_gb_rate = float(pitcher_stats_row['gb_rate'])

    # Select half-specific first-inning adjustment
    fi_adjust = adjust_for_first_inning_top if half == 'top' else adjust_for_first_inning_bottom

    batter_matchup_list = []

    for lineup_row in lineup:
        batter_id = lineup_row['mlb_player_id']
        batter_info = idx['players'].get(batter_id, {})
        batter_hand = batter_info.get('bats', 'R') or 'R'
        batter_speed = batter_info.get('sprint_speed')

        # Get overall batter rates first (shrinkage target for splits)
        batter_overall = get_marcel_weighted_rates(
            batter_id, season, 'batter_stats', idx, league_rates,
        )
        if batter_overall is None:
            batter_overall = dict(league_rates)

        # Try platoon split (shrunk toward overall rates)
        split_rates = get_best_split_rates(
            batter_id, season, 'batter', pitcher_hand, idx,
            overall_rates=batter_overall,
        )
        batter_rates = split_rates if split_rates is not None else batter_overall

        # Pitcher split rates (shrunk toward overall pitcher rates)
        pitcher_split = get_best_split_rates(
            pitcher_id, season, 'pitcher', batter_hand, idx,
            overall_rates=pitcher_rates,
        )
        p_rates = pitcher_split if pitcher_split is not None else pitcher_rates

        # Odds Ratio matchup
        matchup = compute_matchup_rates(batter_rates, p_rates, league_rates)

        # Per-batter GIDP fraction (pitcher GB% × batter speed)
        matchup['gidp_fraction'] = compute_gidp_fraction(
            pitcher_gb_rate,
            float(batter_speed) if batter_speed else None,
        )

        # First-inning adjustment (asymmetric by half)
        fi_adjusted = fi_adjust(matchup)

        # Environmental adjustments (park + weather)
        adjusted = apply_all_adjustments(
            fi_adjusted,
            park_hr_factor=park_hr_factor,
            park_single_factor=park_single_factor,
            park_double_factor=park_double_factor,
            park_triple_factor=park_triple_factor,
            temperature_f=temperature_f,
            wind_speed_mph=wind_speed_mph,
            wind_relative=wind_relative,
        )
        batter_matchup_list.append(adjusted)

    return batter_matchup_list


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def print_calibration_table(cal_curve):
    """Print a formatted calibration table."""
    print(f"\n  {'Bin Center':>12} {'Predicted':>10} {'Actual':>10} {'Count':>8} {'Gap':>8}")
    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*8} {'-'*8}")
    for i in range(len(cal_curve['bin_centers'])):
        center = cal_curve['bin_centers'][i]
        conf = cal_curve['bin_confidences'][i]
        acc = cal_curve['bin_accuracies'][i]
        count = cal_curve['bin_counts'][i]
        gap = acc - conf
        print(f"  {center:>12.2f} {conf:>10.4f} {acc:>10.4f} {count:>8} {gap:>+8.4f}")


def evaluate_predictions(predictions, actuals, label=""):
    """Compute and print evaluation metrics."""
    predictions = np.array(predictions)
    actuals = np.array(actuals)

    brier = float(np.mean((predictions - actuals) ** 2))
    ece = compute_ece(predictions, actuals, n_bins=10)

    # Log loss
    clipped = np.clip(predictions, 0.001, 0.999)
    log_loss = float(-np.mean(
        actuals * np.log(clipped) + (1 - actuals) * np.log(1 - clipped)
    ))

    # Accuracy (threshold at 0.5)
    binary_preds = (predictions >= 0.5).astype(float)
    accuracy = float(np.mean(binary_preds == actuals))

    # Calibration curve
    cal_curve = compute_calibration_curve(predictions, actuals, n_bins=10)

    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  Samples:      {len(predictions):,}")
    print(f"  Actual NRFI:  {actuals.mean():.4f} ({int(actuals.sum()):,}/{len(actuals):,})")
    print(f"  Mean Pred:    {predictions.mean():.4f}")
    print(f"  Brier Score:  {brier:.6f}")
    print(f"  Log Loss:     {log_loss:.6f}")
    print(f"  ECE:          {ece:.6f}")
    print(f"  Accuracy:     {accuracy:.4f}")

    # Baseline Brier (always predict the base rate)
    base_rate = actuals.mean()
    baseline_brier = float(np.mean((base_rate - actuals) ** 2))
    print(f"  Baseline Brier (always {base_rate:.4f}): {baseline_brier:.6f}")
    print(f"  Brier Skill:  {1 - brier / baseline_brier:.4f}")

    print_calibration_table(cal_curve)

    return {
        'brier': brier,
        'log_loss': log_loss,
        'ece': ece,
        'accuracy': accuracy,
        'base_rate': float(base_rate),
        'baseline_brier': baseline_brier,
        'brier_skill': 1 - brier / baseline_brier,
        'cal_curve': cal_curve,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  NRFI Backtest — Phase 3")
    print("=" * 60)

    # Step 1: Load all data
    print("\n[1/5] Loading data from Supabase...")
    t0 = time.time()
    data = load_all_data()
    idx = build_indexes(data)
    load_time = time.time() - t0
    print(f"\n  Data loaded and indexed in {load_time:.1f}s")
    print(f"  Games: {len(idx['games']):,}")
    print(f"  Lineups: {len(idx['lineups']):,} game-team combinations")
    print(f"  Players: {len(idx['players']):,}")
    print(f"  Pitcher stats: {len(idx['pitcher_stats']):,} player-seasons")
    print(f"  Batter stats: {len(idx['batter_stats']):,} player-seasons")
    print(f"  Platoon splits: {len(idx['platoon_splits']):,}")
    print(f"  Parks: {len(idx['parks']):,}")
    print(f"  League averages: {len(idx['league_averages']):,} seasons")

    # Step 2: Run predictions on all games
    print("\n[2/5] Running predictions on all games...")
    t0 = time.time()

    results = []
    skipped_no_lineups = 0
    skipped_no_pitcher = 0
    skipped_no_result = 0
    skipped_other = 0

    games_list = sorted(data['games'], key=lambda g: g['game_date'])
    total_games = len(games_list)

    for i, game in enumerate(games_list):
        if game.get('nrfi_result') is None:
            skipped_no_result += 1
            continue

        pred = predict_game(game, idx)
        if pred is None:
            # Determine skip reason
            gp = game['game_pk']
            if not game.get('home_pitcher_id') or not game.get('away_pitcher_id'):
                skipped_no_pitcher += 1
            elif not idx['lineups'].get((gp, game['home_team_id'])) or \
                 not idx['lineups'].get((gp, game['away_team_id'])):
                skipped_no_lineups += 1
            else:
                skipped_other += 1
            continue

        results.append(pred)

        # Progress
        if (i + 1) % 2000 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            print(f"  Processed {i+1:,}/{total_games:,} games "
                  f"({len(results):,} predicted, {rate:.0f} games/s)")

    predict_time = time.time() - t0
    print(f"\n  Predictions complete in {predict_time:.1f}s "
          f"({len(results):,} games predicted)")
    print(f"  Skipped: {skipped_no_lineups:,} no lineups, "
          f"{skipped_no_pitcher:,} no pitcher, "
          f"{skipped_no_result:,} no result, "
          f"{skipped_other:,} other")

    if not results:
        print("\nNo predictions generated. Check data completeness.")
        return

    # Step 3: Evaluate raw model
    print("\n[3/5] Evaluating raw (uncalibrated) model...")
    all_preds = np.array([r['p_nrfi_combined'] for r in results])
    all_actuals = np.array([1.0 if r['nrfi_result'] else 0.0 for r in results])

    raw_metrics = evaluate_predictions(
        all_preds, all_actuals, "RAW MODEL — All Games (2019-2026)"
    )

    # Per-season breakdown
    print("\n  Per-season breakdown:")
    print(f"  {'Season':>8} {'Games':>7} {'NRFI%':>7} {'Pred%':>7} {'Brier':>8} {'ECE':>8}")
    print(f"  {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*8}")
    for season in sorted(set(r['season'] for r in results)):
        season_mask = np.array([r['season'] == season for r in results])
        sp = all_preds[season_mask]
        sa = all_actuals[season_mask]
        brier = float(np.mean((sp - sa) ** 2))
        ece = compute_ece(sp, sa, n_bins=10)
        print(f"  {season:>8} {len(sp):>7} {sa.mean():>7.3f} {sp.mean():>7.3f} "
              f"{brier:>8.5f} {ece:>8.5f}")

    # Step 4: Calibration — Train on 2019-2025, test on 2026
    print("\n[4/6] Training isotonic regression calibrator...")
    print("  Train: 2019-2025 | Test: 2026 (out-of-sample)")

    train_mask = np.array([r['season'] <= 2025 for r in results])
    test_mask = np.array([r['season'] == 2026 for r in results])

    train_preds = all_preds[train_mask]
    train_actuals = all_actuals[train_mask]
    test_preds = all_preds[test_mask]
    test_actuals = all_actuals[test_mask]

    print(f"  Train: {len(train_preds):,} games | Test: {len(test_preds):,} games")

    calibrator = NRFICalibrator()
    calibrator.fit(train_preds, train_actuals)

    # Test on 2026 (out-of-sample)
    test_raw_metrics = None
    test_cal_metrics = None
    if len(test_preds) > 0:
        test_calibrated = calibrator.calibrate_batch(test_preds)
        print("\n  --- 2026 Test Set (Out-of-Sample) ---")
        test_raw_metrics = evaluate_predictions(
            test_preds, test_actuals, "2026 RAW"
        )
        test_cal_metrics = evaluate_predictions(
            test_calibrated, test_actuals, "2026 CALIBRATED"
        )

    # Save calibrator (trained on 2019-2025)
    cal_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')
    os.makedirs(cal_dir, exist_ok=True)
    cal_path = os.path.join(cal_dir, 'calibrator.json')
    calibrator.save(cal_path)
    print(f"\n  Calibrator saved to {cal_path}")

    # Step 5: Discrimination analysis
    print("\n[5/6] Discrimination Analysis...")

    # Calibrate ALL predictions for discrimination analysis
    all_calibrated = calibrator.calibrate_batch(all_preds)

    # Decile analysis on raw predictions
    print("\n  DECILE ANALYSIS (raw predictions, all games):")
    print(f"  {'Decile':>7} {'Mean Pred':>10} {'Actual':>10} {'Games':>7} {'Diff':>8}")
    print(f"  {'-'*7} {'-'*10} {'-'*10} {'-'*7} {'-'*8}")

    sorted_indices = np.argsort(all_preds)
    decile_size = len(all_preds) // 10
    for d in range(10):
        start = d * decile_size
        end = (d + 1) * decile_size if d < 9 else len(all_preds)
        di = sorted_indices[start:end]
        dp = all_preds[di]
        da = all_actuals[di]
        print(f"  {d+1:>7} {dp.mean():>10.4f} {da.mean():>10.4f} {len(di):>7} {da.mean()-dp.mean():>+8.4f}")

    # Decile analysis on calibrated predictions
    print("\n  DECILE ANALYSIS (calibrated predictions, all games):")
    print(f"  {'Decile':>7} {'Mean Pred':>10} {'Actual':>10} {'Games':>7} {'Diff':>8}")
    print(f"  {'-'*7} {'-'*10} {'-'*10} {'-'*7} {'-'*8}")

    sorted_indices_cal = np.argsort(all_calibrated)
    for d in range(10):
        start = d * decile_size
        end = (d + 1) * decile_size if d < 9 else len(all_calibrated)
        di = sorted_indices_cal[start:end]
        dp = all_calibrated[di]
        da = all_actuals[di]
        print(f"  {d+1:>7} {dp.mean():>10.4f} {da.mean():>10.4f} {len(di):>7} {da.mean()-dp.mean():>+8.4f}")

    # High-confidence analysis
    print("\n  HIGH-CONFIDENCE ANALYSIS:")
    for threshold in [0.54, 0.56, 0.58, 0.60]:
        mask = all_calibrated > threshold
        n = mask.sum()
        if n > 0:
            actual = all_actuals[mask].mean()
            pred = all_calibrated[mask].mean()
            print(f"  Calibrated P(NRFI) > {threshold:.2f}: "
                  f"{n:,} games, actual NRFI = {actual:.4f}, "
                  f"mean pred = {pred:.4f}")
        else:
            print(f"  Calibrated P(NRFI) > {threshold:.2f}: 0 games")

    # 2026 out-of-sample high-confidence
    if len(test_preds) > 0:
        print("\n  HIGH-CONFIDENCE ANALYSIS (2026 out-of-sample only):")
        for threshold in [0.54, 0.56, 0.58, 0.60]:
            mask = test_calibrated > threshold
            n = mask.sum()
            if n > 0:
                actual = test_actuals[mask].mean()
                pred = test_calibrated[mask].mean()
                print(f"  Calibrated P(NRFI) > {threshold:.2f}: "
                      f"{n:,} games, actual NRFI = {actual:.4f}, "
                      f"mean pred = {pred:.4f}")
            else:
                print(f"  Calibrated P(NRFI) > {threshold:.2f}: 0 games")

    # Step 6: Summary
    print("\n[6/6] Final Summary")
    print("=" * 60)
    print(f"  NRFI BACKTEST RESULTS")
    print(f"  Model: {MODEL_VERSION}")
    print("=" * 60)
    print(f"\n  Total games in DB:     {total_games:,}")
    print(f"  Games predicted:       {len(results):,}")
    print(f"  Coverage:              {len(results)/total_games*100:.1f}%")
    print(f"\n  Historical NRFI rate:  {all_actuals.mean():.4f} "
          f"({int(all_actuals.sum()):,}/{len(all_actuals):,})")
    print(f"  Model mean prediction: {all_preds.mean():.4f}")
    print(f"  Prediction std dev:    {all_preds.std():.4f}")
    print(f"  Calibrated mean:       {all_calibrated.mean():.4f}")
    print(f"  Calibrated std dev:    {all_calibrated.std():.4f}")

    print(f"\n  {'Metric':<25} {'Raw':>12} {'Calibrated':>12}")
    print(f"  {'-'*25} {'-'*12} {'-'*12}")

    if test_raw_metrics and test_cal_metrics:
        print(f"  {'Brier Score (2026)':<25} {test_raw_metrics['brier']:>12.6f} {test_cal_metrics['brier']:>12.6f}")
        print(f"  {'ECE (2026)':<25} {test_raw_metrics['ece']:>12.6f} {test_cal_metrics['ece']:>12.6f}")
        print(f"  {'Log Loss (2026)':<25} {test_raw_metrics['log_loss']:>12.6f} {test_cal_metrics['log_loss']:>12.6f}")
        print(f"  {'Accuracy (2026)':<25} {test_raw_metrics['accuracy']:>12.4f} {test_cal_metrics['accuracy']:>12.4f}")
        print(f"  {'Brier Skill (2026)':<25} {test_raw_metrics['brier_skill']:>12.4f} {test_cal_metrics['brier_skill']:>12.4f}")

    print(f"\n  {'Brier Score (all)':<25} {raw_metrics['brier']:>12.6f}")
    print(f"  {'ECE (all)':<25} {raw_metrics['ece']:>12.6f}")
    print(f"  {'Baseline Brier (all)':<25} {raw_metrics['baseline_brier']:>12.6f}")
    print(f"  {'Brier Skill (all)':<25} {raw_metrics['brier_skill']:>12.4f}")

    # Save results to JSON for further analysis
    results_path = os.path.join(cal_dir, 'backtest_results.json')
    output = {
        'model_version': MODEL_VERSION,
        'total_games_db': total_games,
        'games_predicted': len(results),
        'overall_nrfi_rate': float(all_actuals.mean()),
        'overall_mean_prediction': float(all_preds.mean()),
        'prediction_std': float(all_preds.std()),
        'calibrated_std': float(all_calibrated.std()),
        'raw_metrics': {
            'brier': raw_metrics['brier'],
            'ece': raw_metrics['ece'],
            'log_loss': raw_metrics['log_loss'],
            'accuracy': raw_metrics['accuracy'],
            'brier_skill': raw_metrics['brier_skill'],
        },
        'test_2026_raw': {
            'brier': test_raw_metrics['brier'],
            'ece': test_raw_metrics['ece'],
            'brier_skill': test_raw_metrics['brier_skill'],
        } if test_raw_metrics else None,
        'test_2026_calibrated': {
            'brier': test_cal_metrics['brier'],
            'ece': test_cal_metrics['ece'],
            'brier_skill': test_cal_metrics['brier_skill'],
        } if test_cal_metrics else None,
        'per_season': {},
    }
    for season in sorted(set(r['season'] for r in results)):
        sm = np.array([r['season'] == season for r in results])
        sp = all_preds[sm]
        sa = all_actuals[sm]
        output['per_season'][season] = {
            'games': int(len(sp)),
            'nrfi_rate': float(sa.mean()),
            'mean_pred': float(sp.mean()),
            'brier': float(np.mean((sp - sa) ** 2)),
        }

    with open(results_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\n  Full results saved to {results_path}")

    # Store predictions to Supabase
    print("\n  Storing predictions to Supabase...")
    store_predictions(results, calibrator)
    print("  Done.")


def store_predictions(results, calibrator):
    """Batch-insert predictions to Supabase predictions table."""
    rows = []
    for r in results:
        calibrated = calibrator.calibrate(r['p_nrfi_combined'])
        rows.append({
            'game_pk': r['game_pk'],
            'prediction_type': 'confirmed',
            'model_version': MODEL_VERSION,
            'p_nrfi_top': round(r['p_nrfi_top'], 4),
            'p_nrfi_bottom': round(r['p_nrfi_bottom'], 4),
            'p_nrfi_combined': round(r['p_nrfi_combined'], 4),
            'p_nrfi_calibrated': round(float(calibrated), 4),
            'result': r['nrfi_result'],
        })

    # Batch upsert in chunks of 200 (on_conflict on game_pk + prediction_type)
    upsert_headers = {
        **SB_HEADERS,
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    # Delete old backtest predictions first to avoid 409 conflicts
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/predictions",
        headers=SB_HEADERS,
        params={"prediction_type": "eq.confirmed", "model_version": f"neq.{MODEL_VERSION}"},
        timeout=30,
    )
    total = len(rows)
    for i in range(0, total, 200):
        batch = rows[i:i+200]
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/predictions",
            headers=upsert_headers,
            json=batch,
            timeout=30,
        )
        if r.status_code not in (200, 201):
            print(f"    Warning: batch {i//200 + 1} failed: {r.status_code} {r.text[:200]}")
        if (i + 200) % 2000 == 0:
            print(f"    Stored {min(i+200, total):,}/{total:,} predictions")


if __name__ == '__main__':
    main()
