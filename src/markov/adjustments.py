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


def _apply_first_inning_multipliers(rates: dict, multipliers: dict) -> dict:
    """Apply a set of first-inning multipliers and renormalize."""
    result = dict(rates)
    non_residual = [k for k in result if k != 'out_in_play']

    for k in non_residual:
        multiplier = multipliers.get(k, 1.0)
        result[k] *= multiplier

    other_sum = sum(result[k] for k in non_residual)

    if other_sum > 0.99:
        scale = 0.99 / other_sum
        for k in non_residual:
            result[k] *= scale
        result['out_in_play'] = 0.01
    else:
        result['out_in_play'] = 1.0 - other_sum

    return result


def adjust_for_first_inning(rates: dict) -> dict:
    """
    Apply first-inning-specific multipliers to matchup rates.
    Uses league-wide averages (no home/away distinction).
    """
    return _apply_first_inning_multipliers(rates, FIRST_INNING_MULTIPLIERS)


# Home/away asymmetric first-inning adjustments.
# FanGraphs research: home pitchers have K%-BB% of 6.8% vs away 3.6%.
# Home pitchers throw 0.15 mph faster in 1st inning. Effect persists
# even without fans (2020). Explanation: warmup timing advantage.
FIRST_INNING_MULTIPLIERS_TOP = {
    # Top of 1st: away batters face HOME pitcher (pitcher has advantage)
    'k': 0.99 * 1.02,     # +2% K rate (home pitcher advantage)
    'bb': 1.00 * 0.99,    # -1% BB rate
    'hr': 1.12,
    'single': 1.12,
    'double': 1.12,
    'triple': 1.12,
    'hbp': 0.87,
}

FIRST_INNING_MULTIPLIERS_BOTTOM = {
    # Bottom of 1st: home batters face AWAY pitcher (pitcher disadvantaged)
    'k': 0.99 * 0.98,     # -2% K rate (away pitcher disadvantage)
    'bb': 1.00 * 1.01,    # +1% BB rate
    'hr': 1.12,
    'single': 1.12,
    'double': 1.12,
    'triple': 1.12,
    'hbp': 0.87,
}


def adjust_for_first_inning_top(rates: dict) -> dict:
    """Apply first-inning multipliers for top of 1st (away batters vs home pitcher)."""
    return _apply_first_inning_multipliers(rates, FIRST_INNING_MULTIPLIERS_TOP)


def adjust_for_first_inning_bottom(rates: dict) -> dict:
    """Apply first-inning multipliers for bottom of 1st (home batters vs away pitcher)."""
    return _apply_first_inning_multipliers(rates, FIRST_INNING_MULTIPLIERS_BOTTOM)


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


def adjust_for_park(
    rates: dict,
    park_hr_factor: float,
    park_single_factor: float = 100.0,
    park_double_factor: float = 100.0,
    park_triple_factor: float = 100.0,
) -> dict:
    """
    Multiply each hit type by its park factor / 100. Recalculate residual.

    FanGraphs publishes per-hit-type park factors. All are scaled to 100
    (neutral). Example: Coors HR=131, 1B=105, 2B=107, 3B=275.

    HR factors are most stable year-to-year (r=0.74). Non-HR factors
    are noisier (1B r=0.37, 2B r=0.47, 3B r=0.66) and should use
    longer rolling averages and be regressed harder toward 100.
    """
    result = dict(rates)
    result['hr'] *= park_hr_factor / 100.0
    result['single'] *= park_single_factor / 100.0
    result['double'] *= park_double_factor / 100.0
    result['triple'] *= park_triple_factor / 100.0
    return _recalculate_residual(result)


def adjust_for_temperature(rates: dict, temperature_f: float) -> dict:
    """
    Adjust batted-ball rates for temperature.

    Temperature affects all fly ball outcomes via air density/drag.
    HR is most affected; doubles and triples at reduced magnitudes.
    Based on Dr. Alan Nathan's research (+3ft carry per 10°F above 75°F).

    Coefficients per °F from 75°F baseline:
      HR:     0.0015 (1.5% per 10°F)
      Double: 0.0006 (40% of HR effect — wall doubles are temperature-sensitive)
      Triple: 0.00045 (30% of HR effect — gap hits less affected)
    """
    result = dict(rates)
    delta = temperature_f - 75.0
    result['hr'] *= 1.0 + delta * 0.0015
    result['double'] *= 1.0 + delta * 0.0006
    result['triple'] *= 1.0 + delta * 0.00045
    return _recalculate_residual(result)


def adjust_for_wind(rates: dict, wind_speed_mph: float, wind_relative: str) -> dict:
    """
    Adjust batted-ball rates for wind direction and speed.

    Wind affects all fly ball outcomes. HR most affected; doubles and triples
    at reduced magnitudes. At Wrigley, wind-in vs wind-out swings total runs
    by 42%, far more than HR alone explains.

    Coefficients per mph:
      HR:     0.008 (0.8% per mph)
      Double: 0.0032 (40% of HR — wall doubles affected by carry)
      Triple: 0.0016 (20% of HR — gap hits less affected)

    'cross_l', 'cross_r', 'calm': no adjustment.
    """
    result = dict(rates)

    if wind_relative == 'out':
        result['hr'] *= 1.0 + wind_speed_mph * 0.008
        result['double'] *= 1.0 + wind_speed_mph * 0.0032
        result['triple'] *= 1.0 + wind_speed_mph * 0.0016
    elif wind_relative == 'in':
        result['hr'] *= max(0.5, 1.0 - wind_speed_mph * 0.008)
        result['double'] *= max(0.7, 1.0 - wind_speed_mph * 0.0032)
        result['triple'] *= max(0.7, 1.0 - wind_speed_mph * 0.0016)
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
    park_single_factor: float = 100.0,
    park_double_factor: float = 100.0,
    park_triple_factor: float = 100.0,
    temperature_f: float = 75.0,
    wind_speed_mph: float = 0.0,
    wind_relative: str = 'calm',
    walk_rate_impact: float = 0.0,
    framing_runs: float = 0.0,
) -> dict:
    """
    Apply all environmental adjustments in sequence, then normalize.
    """
    result = adjust_for_park(
        rates, park_hr_factor, park_single_factor,
        park_double_factor, park_triple_factor,
    )
    result = adjust_for_temperature(result, temperature_f)
    result = adjust_for_wind(result, wind_speed_mph, wind_relative)
    result = adjust_for_umpire(result, walk_rate_impact)
    result = adjust_for_catcher_framing(result, framing_runs)
    return normalize_rates(result)
