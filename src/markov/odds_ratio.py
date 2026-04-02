"""
Odds Ratio module for NRFI matchup probability computation.

Implements Tango's Odds Ratio method for combining batter/pitcher rates,
Marcel shrinkage for regression toward the mean, and multi-year weighting.
"""

from __future__ import annotations

import math
from typing import Optional

OUTCOME_TYPES = ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']


def compute_matchup_rate(batter_rate: float, pitcher_rate: float, league_rate: float) -> float:
    """
    Tango Odds Ratio method.

    Odds(matchup) = Odds(batter) × Odds(pitcher) / Odds(league)
    P(matchup) = Odds(matchup) / (1 + Odds(matchup))

    For rates near 0 or 1, use log-odds for numerical stability.
    """
    # Edge cases: any rate is exactly 0 or 1
    if batter_rate == 0 or pitcher_rate == 0:
        return 0.0
    if batter_rate == 1 or pitcher_rate == 1:
        return 1.0
    if league_rate == 0 or league_rate == 1:
        # league_rate 0 would cause division by zero in odds;
        # league_rate 1 would make league odds infinite
        return 0.0 if league_rate == 0 else 1.0

    # Use log-odds formulation for numerical stability when any rate is extreme
    if (batter_rate < 0.001 or batter_rate > 0.999 or
            pitcher_rate < 0.001 or pitcher_rate > 0.999 or
            league_rate < 0.001 or league_rate > 0.999):
        log_odds_b = math.log(batter_rate / (1 - batter_rate))
        log_odds_p = math.log(pitcher_rate / (1 - pitcher_rate))
        log_odds_l = math.log(league_rate / (1 - league_rate))
        log_odds_matchup = log_odds_b + log_odds_p - log_odds_l
        return 1.0 / (1.0 + math.exp(-log_odds_matchup))

    # Standard odds ratio formulation
    odds_b = batter_rate / (1 - batter_rate)
    odds_p = pitcher_rate / (1 - pitcher_rate)
    odds_l = league_rate / (1 - league_rate)

    odds_matchup = odds_b * odds_p / odds_l
    return odds_matchup / (1 + odds_matchup)


def compute_matchup_rates(
    batter_rates: dict,
    pitcher_rates: dict,
    league_rates: dict,
) -> dict:
    """
    Compute matchup-specific rates for all outcome types using Tango's Odds Ratio.

    Returns a dict with all event rates plus 'out_in_play', summing to 1.0.
    If computed event rates exceed 1.0, they are proportionally scaled down.
    """
    matchup = {}
    for outcome in OUTCOME_TYPES:
        matchup[outcome] = compute_matchup_rate(
            batter_rates[outcome],
            pitcher_rates[outcome],
            league_rates[outcome],
        )

    event_sum = sum(matchup.values())

    if event_sum >= 1.0:
        # Proportionally scale down so all events + out_in_play sum to 1.0
        # Reserve a tiny sliver for out_in_play to keep it non-negative
        scale = 0.999 / event_sum
        for outcome in OUTCOME_TYPES:
            matchup[outcome] *= scale
        matchup['out_in_play'] = 1.0 - sum(matchup[o] for o in OUTCOME_TYPES)
    else:
        matchup['out_in_play'] = 1.0 - event_sum

    return matchup


def apply_marcel_shrinkage(
    observed_rate: float,
    league_rate: float,
    plate_appearances: int,
    shrinkage_constant: int = 1200,
) -> float:
    """
    Marcel regression toward the mean.

    r = PA / (PA + 1200)
    adjusted = r * observed + (1 - r) * league
    """
    r = plate_appearances / (plate_appearances + shrinkage_constant)
    return r * observed_rate + (1 - r) * league_rate


def compute_weighted_rate(
    rates: list[float],
    pa_counts: list[int],
    league_rate: float,
    weights: Optional[list[int]] = None,
    shrinkage_constant: int = 1200,
) -> float:
    """
    Marcel multi-year weighted rate with shrinkage.

    1. Compute weighted average of rates using 5/4/3 weights
    2. Compute weighted PA sum
    3. Apply shrinkage: r = weighted_PA / (weighted_PA + 1200)
    4. Return r * weighted_rate + (1-r) * league_rate

    Handles cases where fewer than 3 years of data are available.
    """
    if weights is None:
        weights = [5, 4, 3]

    n = len(rates)
    if n == 0:
        return league_rate

    # Use only the first n weights if fewer than 3 years available
    w = weights[:n]

    total_weight = sum(w)
    weighted_rate = sum(r * wt for r, wt in zip(rates, w)) / total_weight
    weighted_pa = sum(pa * wt for pa, wt in zip(pa_counts, w))

    r = weighted_pa / (weighted_pa + shrinkage_constant)
    return r * weighted_rate + (1 - r) * league_rate
