"""
Environmental adjustment functions for Markov chain transition probabilities.

These modify outcome rates BEFORE they enter the Markov chain.
They are NOT separate terms in a linear model — they adjust the
transition probabilities directly.
"""

from __future__ import annotations

# Data-driven first-inning multipliers derived from comparing first-inning
# outcome rates to season-long rates across 2019-2025 historical data.
# Applied to matchup rates BEFORE the Markov chain to account for first-inning
# specific tendencies (e.g., pitchers throw more strikes, hitters are more
# aggressive, more HR due to fastball-heavy first-inning pitch mix).
FIRST_INNING_MULTIPLIERS = {
    'k': 0.99,       # Batters K 1% less in the first inning
    'bb': 1.00,      # Walk rate unchanged
    'hr': 1.12,      # 12% more HR (fastball-heavy pitch mix)
    'single': 1.12,  # 12% more hits overall
    'double': 1.12,
    'triple': 1.12,
    'hbp': 0.87,     # 13% fewer HBP
}


def adjust_for_first_inning(rates: dict) -> dict:
    """
    Apply first-inning-specific multipliers to matchup rates.

    Multiplies each outcome rate by its empirical first-inning adjustment,
    then recomputes out_in_play = 1 - sum(other rates).
    If out_in_play drops below 0.01, clip it and scale down other rates.
    """
    result = dict(rates)
    non_residual = [k for k in result if k != 'out_in_play']

    for k in non_residual:
        multiplier = FIRST_INNING_MULTIPLIERS.get(k, 1.0)
        result[k] *= multiplier

    other_sum = sum(result[k] for k in non_residual)

    if other_sum > 0.99:
        # Scale down to leave at least 1% for out_in_play
        scale = 0.99 / other_sum
        for k in non_residual:
            result[k] *= scale
        result['out_in_play'] = 0.01
    else:
        result['out_in_play'] = 1.0 - other_sum

    return result


def normalize_rates(rates: dict) -> dict:
    """
    Ensure all rates are non-negative and sum to 1.0.

    If any rate went negative from adjustments, set to 0.
    Recalculate 'out_in_play' as 1.0 - sum(other rates).
    If 'out_in_play' < 0, proportionally scale down all rates.
    """
    result = dict(rates)

    # Clamp negatives to 0 (except out_in_play which we recalculate)
    non_residual = [k for k in result if k != 'out_in_play']
    for k in non_residual:
        if result[k] < 0:
            result[k] = 0.0

    other_sum = sum(result[k] for k in non_residual)

    if other_sum > 1.0:
        # Proportionally scale down all non-residual rates
        scale = 1.0 / other_sum
        for k in non_residual:
            result[k] *= scale
        result['out_in_play'] = 0.0
    else:
        result['out_in_play'] = 1.0 - other_sum

    return result


def _recalculate_residual(rates: dict) -> dict:
    """Recalculate 'out_in_play' as the residual after other rates."""
    result = dict(rates)
    non_residual = [k for k in result if k != 'out_in_play']
    result['out_in_play'] = 1.0 - sum(result[k] for k in non_residual)
    return result


def adjust_for_park(rates: dict, park_hr_factor: float) -> dict:
    """
    Multiply HR rate by (park_hr_factor / 100). Recalculate residual.

    park_hr_factor: 100 = neutral, 115 = 15% more HR (e.g. Coors).
    """
    result = dict(rates)
    result['hr'] *= park_hr_factor / 100.0
    return _recalculate_residual(result)


def adjust_for_temperature(rates: dict, temperature_f: float) -> dict:
    """
    Adjust HR rate for temperature.

    For every 10°F above 75°F, increase HR rate by 1.5%.
    Below 75°F, decrease by same amount.
    Formula: hr_multiplier = 1 + (temperature_f - 75) * 0.0015
    """
    result = dict(rates)
    hr_multiplier = 1.0 + (temperature_f - 75.0) * 0.0015
    result['hr'] *= hr_multiplier
    return _recalculate_residual(result)


def adjust_for_wind(rates: dict, wind_speed_mph: float, wind_relative: str) -> dict:
    """
    Adjust HR rate for wind direction and speed.

    'out': hr_multiplier = 1 + wind_speed_mph * 0.008
    'in':  hr_multiplier = 1 - wind_speed_mph * 0.008  (floor at 0.5)
    'cross_l', 'cross_r', 'calm': no adjustment.
    """
    result = dict(rates)

    if wind_relative == 'out':
        hr_multiplier = 1.0 + wind_speed_mph * 0.008
        result['hr'] *= hr_multiplier
    elif wind_relative == 'in':
        hr_multiplier = max(0.5, 1.0 - wind_speed_mph * 0.008)
        result['hr'] *= hr_multiplier
    # 'cross_l', 'cross_r', 'calm' — no adjustment

    return _recalculate_residual(result)


def adjust_for_umpire(rates: dict, walk_rate_impact: float) -> dict:
    """
    Adjust BB rate by umpire's deviation from league average.

    walk_rate_impact: e.g. +0.01 means 1% more walks.
    Clamp BB rate to [0.01, 0.20].
    """
    result = dict(rates)
    result['bb'] = max(0.01, min(0.20, result['bb'] + walk_rate_impact))
    return _recalculate_residual(result)


def adjust_for_catcher_framing(rates: dict, framing_runs_per_game: float) -> dict:
    """
    Adjust BB rate based on catcher framing quality.

    1 framing run above average ≈ 0.003 reduction in BB rate.
    Clamp BB rate to [0.01, 0.20].
    """
    result = dict(rates)
    result['bb'] = max(0.01, min(0.20, result['bb'] - framing_runs_per_game * 0.003))
    return _recalculate_residual(result)


def apply_all_adjustments(
    rates: dict,
    park_hr_factor: float = 100.0,
    temperature_f: float = 75.0,
    wind_speed_mph: float = 0.0,
    wind_relative: str = 'calm',
    walk_rate_impact: float = 0.0,
    framing_runs: float = 0.0,
) -> dict:
    """
    Apply all environmental adjustments in sequence, then normalize.
    """
    result = adjust_for_park(rates, park_hr_factor)
    result = adjust_for_temperature(result, temperature_f)
    result = adjust_for_wind(result, wind_speed_mph, wind_relative)
    result = adjust_for_umpire(result, walk_rate_impact)
    result = adjust_for_catcher_framing(result, framing_runs)
    return normalize_rates(result)
