"""
NRFI prediction pipeline.

Orchestrates data fetch, Marcel shrinkage, Odds Ratio matchups,
environmental adjustments, Markov chain computation, and betting math
to produce a single-game NRFI prediction.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.markov.odds_ratio import compute_matchup_rates, compute_weighted_rate
from src.markov.chain import compute_p_zero_runs
from src.markov.adjustments import apply_all_adjustments
from src.betting.edge import (
    american_to_decimal,
    remove_vig_power_method,
    compute_edge,
    kelly_fraction,
    find_best_line,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = '0.1.0'

OUTCOME_KEYS = ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']

# Estimated batters faced per inning pitched (league average).
# Used when pitcher_stats lacks an explicit BF column.
BF_PER_IP = 4.3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_bf(innings_pitched: float) -> int:
    """Estimate batters faced from innings pitched."""
    if innings_pitched is None or innings_pitched <= 0:
        return 0
    return int(round(innings_pitched * BF_PER_IP))


def _extract_rates(row: dict) -> dict:
    """Pull outcome rates from a stats/split row, defaulting missing keys to 0."""
    return {k: float(row.get(f'{k}_rate', 0) or 0) for k in OUTCOME_KEYS}


def get_marcel_weighted_rates(
    player_id: int,
    current_season: int,
    table: str,
    db,
    league_rates: dict,
) -> dict:
    """
    Fetch up to 3 seasons of stats, apply Marcel multi-year weighting + shrinkage.

    Returns a dict of shrunk outcome rates.
    """
    weights = [5, 4, 3]
    seasons = [current_season, current_season - 1, current_season - 2]

    rows_by_season = {}
    for s in seasons:
        resp = (
            db.table(table)
            .select('*')
            .eq('mlb_player_id', player_id)
            .eq('season', s)
            .execute()
        )
        if resp.data:
            rows_by_season[s] = resp.data[0]

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
                pa_list.append(_estimate_bf(float(row.get('innings_pitched', 0) or 0)))

            w_list.append(weights[i])

        if not rates_list:
            result[key] = league_rates.get(key, 0.0)
            continue

        result[key] = compute_weighted_rate(
            rates_list, pa_list, league_rates.get(key, 0.0), weights=w_list,
        )

    return result


def get_best_split_rates(
    player_id: int,
    season: int,
    player_type: str,
    opponent_hand: str,
    db,
) -> Optional[dict]:
    """
    Fetch platoon split rates for a player. Returns None if not found or PA < 30.

    player_type: 'batter' or 'pitcher'
    opponent_hand: 'R' or 'L'
    """
    if player_type == 'batter':
        split = f'vs_{"RHP" if opponent_hand == "R" else "LHP"}'
    else:
        split = f'vs_{"RHB" if opponent_hand == "R" else "LHB"}'

    resp = (
        db.table('platoon_splits')
        .select('*')
        .eq('mlb_player_id', player_id)
        .eq('season', season)
        .eq('player_type', player_type)
        .eq('split', split)
        .execute()
    )

    if not resp.data:
        return None

    row = resp.data[0]
    if int(row.get('pa', 0) or 0) < 30:
        return None

    return _extract_rates(row)


def _build_half_inning_rates(
    lineup: list[dict],
    pitcher_id: int,
    pitcher_hand: str,
    season: int,
    league_rates: dict,
    park: dict,
    weather: Optional[dict],
    umpire: Optional[dict],
    is_indoor: bool,
    db,
) -> tuple[list[dict], list[str]]:
    """
    Build adjusted matchup rates for each batter in a lineup.

    Returns (list_of_matchup_rate_dicts, list_of_adjustments_applied).
    """
    adjustments_applied = set()

    # Pitcher Marcel-weighted rates
    pitcher_rates = get_marcel_weighted_rates(
        pitcher_id, season, 'pitcher_stats', db, league_rates,
    )
    if pitcher_rates is None:
        logger.warning('Pitcher %d has no stats — using league averages', pitcher_id)
        pitcher_rates = dict(league_rates)

    batter_matchup_list = []

    for lineup_row in lineup:
        batter_id = lineup_row['mlb_player_id']
        batter_hand = lineup_row.get('bats', 'R')

        # --- Batter rates ---
        # Try platoon split first
        split_rates = get_best_split_rates(
            batter_id, season, 'batter', pitcher_hand, db,
        )
        if split_rates is not None:
            batter_rates = split_rates
        else:
            batter_rates = get_marcel_weighted_rates(
                batter_id, season, 'batter_stats', db, league_rates,
            )
        if batter_rates is None:
            logger.warning(
                'Batter %d has no stats — using league averages', batter_id,
            )
            batter_rates = dict(league_rates)

        # --- Pitcher split rates ---
        pitcher_split = get_best_split_rates(
            pitcher_id, season, 'pitcher', batter_hand, db,
        )
        p_rates = pitcher_split if pitcher_split is not None else pitcher_rates

        # --- Odds Ratio matchup ---
        matchup = compute_matchup_rates(batter_rates, p_rates, league_rates)

        # --- Environmental adjustments ---
        adj_kwargs = {'park_hr_factor': float(park.get('hr_factor', 100) or 100)}
        adjustments_applied.add('park_factor')

        if not is_indoor:
            if weather:
                temp = weather.get('temperature_f')
                if temp is not None:
                    adj_kwargs['temperature_f'] = float(temp)
                    adjustments_applied.add('temperature')
                wind_speed = weather.get('wind_speed_mph')
                wind_rel = weather.get('wind_relative')
                if wind_speed is not None and wind_rel is not None:
                    adj_kwargs['wind_speed_mph'] = float(wind_speed)
                    adj_kwargs['wind_relative'] = wind_rel
                    adjustments_applied.add('wind')

        if umpire:
            wri = umpire.get('walk_rate_impact')
            if wri is not None:
                adj_kwargs['walk_rate_impact'] = float(wri)
                adjustments_applied.add('umpire')

        adjusted = apply_all_adjustments(matchup, **adj_kwargs)
        batter_matchup_list.append(adjusted)

    return batter_matchup_list, sorted(adjustments_applied)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def predict_nrfi(game_pk: int, supabase_client) -> Optional[dict]:
    """
    Produce an NRFI prediction for a single game.

    Returns a dict with all computed values, or None if the game cannot
    be predicted (non-regular, missing lineups, etc.).
    """
    db = supabase_client

    # ------------------------------------------------------------------
    # Step 0: Validate game
    # ------------------------------------------------------------------
    game_resp = db.table('games').select('*').eq('game_pk', game_pk).execute()
    if not game_resp.data:
        logger.error('Game %d not found', game_pk)
        return None

    game = game_resp.data[0]

    if game.get('game_type') != 'regular':
        logger.warning('Game %d is %s — skipping', game_pk, game.get('game_type'))
        return None

    is_backtest = game.get('status') == 'final'
    if is_backtest:
        logger.info('Game %d is final — running as backtest prediction', game_pk)

    season = int(str(game['game_date'])[:4])

    # ------------------------------------------------------------------
    # Step 1: Fetch all data
    # ------------------------------------------------------------------

    home_team_id = game['home_team_id']
    away_team_id = game['away_team_id']
    home_pitcher_id = game.get('home_pitcher_id')
    away_pitcher_id = game.get('away_pitcher_id')
    park_id = game.get('park_id')
    umpire_id = game.get('hp_umpire_id')

    # Lineups (all 9, in batting order)
    away_lineup_resp = (
        db.table('lineups')
        .select('mlb_player_id, batting_order')
        .eq('game_pk', game_pk)
        .eq('team_id', away_team_id)
        .order('batting_order')
        .execute()
    )
    home_lineup_resp = (
        db.table('lineups')
        .select('mlb_player_id, batting_order')
        .eq('game_pk', game_pk)
        .eq('team_id', home_team_id)
        .order('batting_order')
        .execute()
    )

    if not away_lineup_resp.data or not home_lineup_resp.data:
        logger.warning('No confirmed lineups for game %d', game_pk)
        return None

    lineups_confirmed = bool(
        away_lineup_resp.data[0].get('confirmed_at')
        and home_lineup_resp.data[0].get('confirmed_at')
    )

    # Enrich lineup rows with batter handedness from players table
    away_lineup = _enrich_lineup(away_lineup_resp.data, db)
    home_lineup = _enrich_lineup(home_lineup_resp.data, db)

    # Park
    park = {}
    if park_id:
        park_resp = db.table('parks').select('*').eq('park_id', park_id).execute()
        if park_resp.data:
            park = park_resp.data[0]

    is_indoor = bool(park.get('is_dome') or park.get('is_retractable_roof'))

    # Weather
    weather = None
    weather_resp = (
        db.table('weather_snapshots')
        .select('*')
        .eq('game_pk', game_pk)
        .order('captured_at', desc=True)
        .limit(1)
        .execute()
    )
    if weather_resp.data:
        weather = weather_resp.data[0]

    # Umpire
    umpire = None
    if umpire_id:
        umpire_resp = (
            db.table('umpires').select('*').eq('mlb_umpire_id', umpire_id).execute()
        )
        if umpire_resp.data:
            umpire = umpire_resp.data[0]

    # League averages
    league_resp = (
        db.table('league_averages').select('*').eq('season', season).execute()
    )
    if not league_resp.data:
        logger.error('No league averages for season %d', season)
        return None
    league_row = league_resp.data[0]
    league_rates = _extract_rates(league_row)

    # ------------------------------------------------------------------
    # Step 2: Pitcher handedness
    # ------------------------------------------------------------------
    home_pitcher_info = _get_player(home_pitcher_id, db)
    away_pitcher_info = _get_player(away_pitcher_id, db)

    home_pitcher_hand = (home_pitcher_info or {}).get('throws', 'R')
    away_pitcher_hand = (away_pitcher_info or {}).get('throws', 'R')

    # ------------------------------------------------------------------
    # Steps 3 & 4: Top of 1st (away batters vs home pitcher)
    # ------------------------------------------------------------------
    away_matchup_rates, adj_top = _build_half_inning_rates(
        lineup=away_lineup,
        pitcher_id=home_pitcher_id,
        pitcher_hand=home_pitcher_hand,
        season=season,
        league_rates=league_rates,
        park=park,
        weather=weather,
        umpire=umpire,
        is_indoor=is_indoor,
        db=db,
    )

    p_nrfi_top = compute_p_zero_runs(away_matchup_rates, max_batters=9)

    # ------------------------------------------------------------------
    # Steps 5 & 6: Bottom of 1st (home batters vs away pitcher)
    # ------------------------------------------------------------------
    home_matchup_rates, adj_bottom = _build_half_inning_rates(
        lineup=home_lineup,
        pitcher_id=away_pitcher_id,
        pitcher_hand=away_pitcher_hand,
        season=season,
        league_rates=league_rates,
        park=park,
        weather=weather,
        umpire=umpire,
        is_indoor=is_indoor,
        db=db,
    )

    p_nrfi_bottom = compute_p_zero_runs(home_matchup_rates, max_batters=9)

    # ------------------------------------------------------------------
    # Step 7: Combine
    # ------------------------------------------------------------------
    p_nrfi_combined = p_nrfi_top * p_nrfi_bottom

    # ------------------------------------------------------------------
    # Step 8: Calibration placeholder
    # ------------------------------------------------------------------
    # TODO: Replace with isotonic regression calibrator in Phase 3
    p_nrfi_calibrated = p_nrfi_combined

    # ------------------------------------------------------------------
    # Step 9: Compare against odds
    # ------------------------------------------------------------------
    best_book = None
    best_nrfi_price = None
    implied_prob = None
    edge = None
    kelly = None
    bet_recommended = False

    odds_resp = (
        db.table('odds').select('*').eq('game_pk', game_pk).execute()
    )
    if odds_resp.data:
        odds_list = [
            {
                'book': r['book'],
                'nrfi_price': int(r['nrfi_price']),
                'yrfi_price': int(r['yrfi_price']),
            }
            for r in odds_resp.data
            if r.get('nrfi_price') is not None and r.get('yrfi_price') is not None
        ]
        if odds_list:
            best = find_best_line(odds_list)
            best_book = best['book']
            best_nrfi_price = best['nrfi_price']

            nrfi_dec = american_to_decimal(best['nrfi_price'])
            yrfi_dec = american_to_decimal(best['yrfi_price'])
            true_nrfi, _ = remove_vig_power_method(nrfi_dec, yrfi_dec)
            implied_prob = true_nrfi

            edge = compute_edge(p_nrfi_calibrated, implied_prob)
            kelly = kelly_fraction(p_nrfi_calibrated, nrfi_dec)
            bet_recommended = edge > 0.03

    # ------------------------------------------------------------------
    # Step 10: factor_details
    # ------------------------------------------------------------------
    adjustments_applied = sorted(set(adj_top) | set(adj_bottom))

    factor_details = {
        'home_pitcher': _pitcher_detail(home_pitcher_id, home_pitcher_info, home_matchup_rates),
        'away_pitcher': _pitcher_detail(away_pitcher_id, away_pitcher_info, away_matchup_rates),
        'away_top4_batters': _top4_batter_details(away_lineup, away_matchup_rates),
        'home_top4_batters': _top4_batter_details(home_lineup, home_matchup_rates),
        'park': {
            'name': park.get('name'),
            'hr_factor': float(park.get('hr_factor', 100) or 100),
            'is_dome': bool(park.get('is_dome')),
        },
        'weather': (
            {
                'temp': float(weather['temperature_f']) if weather.get('temperature_f') else None,
                'wind_speed': float(weather['wind_speed_mph']) if weather.get('wind_speed_mph') else None,
                'wind_relative': weather.get('wind_relative'),
            }
            if weather
            else None
        ),
        'umpire': (
            {
                'name': umpire.get('name'),
                'walk_rate_impact': float(umpire['walk_rate_impact']) if umpire.get('walk_rate_impact') else None,
            }
            if umpire
            else None
        ),
        'adjustments_applied': adjustments_applied,
    }

    # ------------------------------------------------------------------
    # Step 11: Store prediction
    # ------------------------------------------------------------------
    prediction_type = 'confirmed' if lineups_confirmed else 'preliminary'

    prediction_row = {
        'game_pk': game_pk,
        'prediction_type': prediction_type,
        'model_version': MODEL_VERSION,
        'p_nrfi_top': round(p_nrfi_top, 4),
        'p_nrfi_bottom': round(p_nrfi_bottom, 4),
        'p_nrfi_combined': round(p_nrfi_combined, 4),
        'p_nrfi_calibrated': round(p_nrfi_calibrated, 4),
        'best_book': best_book,
        'best_nrfi_price': best_nrfi_price,
        'implied_prob_best': round(implied_prob, 4) if implied_prob is not None else None,
        'edge': round(edge, 4) if edge is not None else None,
        'bet_recommended': bet_recommended,
        'kelly_fraction': round(kelly, 4) if kelly is not None else None,
        'factor_details': factor_details,
    }

    db.table('predictions').upsert(prediction_row, on_conflict='game_pk,prediction_type').execute()

    # ------------------------------------------------------------------
    # Step 12: Return
    # ------------------------------------------------------------------
    return {
        'game_pk': game_pk,
        'season': season,
        'prediction_type': prediction_type,
        'p_nrfi_top': p_nrfi_top,
        'p_nrfi_bottom': p_nrfi_bottom,
        'p_nrfi_combined': p_nrfi_combined,
        'p_nrfi_calibrated': p_nrfi_calibrated,
        'best_book': best_book,
        'best_nrfi_price': best_nrfi_price,
        'implied_prob': implied_prob,
        'edge': edge,
        'kelly_fraction': kelly,
        'bet_recommended': bet_recommended,
        'factor_details': factor_details,
        'is_backtest': is_backtest,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_player(player_id: Optional[int], db) -> Optional[dict]:
    """Fetch a player record by ID."""
    if player_id is None:
        return None
    resp = db.table('players').select('*').eq('mlb_player_id', player_id).execute()
    return resp.data[0] if resp.data else None


def _enrich_lineup(lineup_rows: list[dict], db) -> list[dict]:
    """Add 'bats' field to each lineup row from the players table."""
    enriched = []
    for row in lineup_rows:
        pid = row['mlb_player_id']
        player = _get_player(pid, db)
        entry = dict(row)
        entry['bats'] = (player or {}).get('bats', 'R')
        enriched.append(entry)
    return enriched


def _pitcher_detail(
    pitcher_id: int,
    pitcher_info: Optional[dict],
    matchup_rates: list[dict],
) -> dict:
    """Build factor_details entry for a pitcher."""
    if not pitcher_info:
        pitcher_info = {}
    # Average the matchup rates across all batters for summary
    avg_rates = {}
    if matchup_rates:
        for key in ('k', 'bb', 'hr'):
            avg_rates[key] = sum(r[key] for r in matchup_rates) / len(matchup_rates)
    return {
        'id': pitcher_id,
        'name': pitcher_info.get('name'),
        'throws': pitcher_info.get('throws'),
        'k_rate': round(avg_rates.get('k', 0), 4),
        'bb_rate': round(avg_rates.get('bb', 0), 4),
        'hr_rate': round(avg_rates.get('hr', 0), 4),
    }


def _top4_batter_details(lineup: list[dict], matchup_rates: list[dict]) -> list[dict]:
    """Build factor_details for first 4 batters in lineup."""
    details = []
    for i in range(min(4, len(lineup))):
        row = lineup[i]
        rates = matchup_rates[i] if i < len(matchup_rates) else {}
        details.append({
            'id': row['mlb_player_id'],
            'name': row.get('name'),
            'bats': row.get('bats'),
            'matchup_hr_rate': round(rates.get('hr', 0), 4),
        })
    return details
