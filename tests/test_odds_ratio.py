"""Tests for the Odds Ratio module."""

import math
import pytest
from src.markov.odds_ratio import (
    compute_matchup_rate,
    compute_matchup_rates,
    apply_marcel_shrinkage,
    compute_weighted_rate,
)


class TestComputeMatchupRate:
    """Tests for the single-outcome Tango Odds Ratio."""

    def test_tango_published_example_raw(self):
        """
        Tango's published example (two-league version):
        Batter .400 OBP in .300 league, pitcher .250 OBP in .350 league.

        For the raw two-league version, we compute each side's odds against
        their own league, then combine:
          Odds(b) / Odds(L_b) * Odds(p) / Odds(L_p) * Odds(overall_L)
        But the simpler single-league form from STRATEGY.md is:
          Odds(matchup) = Odds(.400) * Odds(.250) / Odds(.300)
        which gives P ≈ .341.

        With a different league baseline we test the raw formula directly.
        """
        # From strategy.md worked example:
        # Batter .400, Pitcher .250, League .300
        # Odds(matchup) = (.400/.600) * (.250/.750) / (.300/.700)
        #               = 0.6667 * 0.3333 / 0.4286 = 0.5185
        # P = 0.5185 / 1.5185 ≈ .341
        result = compute_matchup_rate(0.400, 0.250, 0.300)
        assert abs(result - 0.341) < 0.002, f"Expected ~.341, got {result}"

    def test_neutral_matchup(self):
        """League-average batter vs league-average pitcher returns league rate."""
        league = 0.228
        result = compute_matchup_rate(league, league, league)
        assert abs(result - league) < 1e-10, f"Expected {league}, got {result}"

    def test_neutral_matchup_various_rates(self):
        """Neutral matchup works for various league rates."""
        for league in [0.05, 0.15, 0.30, 0.50, 0.80]:
            result = compute_matchup_rate(league, league, league)
            assert abs(result - league) < 1e-10

    def test_edge_case_zero_batter(self):
        """Batter rate of 0 returns 0."""
        assert compute_matchup_rate(0, 0.250, 0.300) == 0.0

    def test_edge_case_zero_pitcher(self):
        """Pitcher rate of 0 returns 0."""
        assert compute_matchup_rate(0.300, 0, 0.300) == 0.0

    def test_edge_case_one_batter(self):
        """Batter rate of 1 returns 1."""
        assert compute_matchup_rate(1.0, 0.250, 0.300) == 1.0

    def test_edge_case_one_pitcher(self):
        """Pitcher rate of 1 returns 1."""
        assert compute_matchup_rate(0.300, 1.0, 0.300) == 1.0

    def test_extreme_low_rate_uses_log_odds(self):
        """Very small rates (< 0.001) use log-odds formulation."""
        # HR-like scenario with extreme rates
        result = compute_matchup_rate(0.0005, 0.0008, 0.0006)
        assert 0 < result < 1
        # Verify mathematically: log_odds approach
        lo_b = math.log(0.0005 / 0.9995)
        lo_p = math.log(0.0008 / 0.9992)
        lo_l = math.log(0.0006 / 0.9994)
        lo_m = lo_b + lo_p - lo_l
        expected = 1 / (1 + math.exp(-lo_m))
        assert abs(result - expected) < 1e-10

    def test_good_batter_vs_good_pitcher(self):
        """A good batter facing a good pitcher should be near league average."""
        # Good batter (high OBP), good pitcher (low OBP allowed)
        result = compute_matchup_rate(0.380, 0.220, 0.310)
        # Should be between pitcher and batter rates
        assert 0.220 < result < 0.380


class TestComputeMatchupRates:
    """Tests for the full outcome profile computation."""

    def test_rates_sum_to_one(self):
        """All matchup rates (events + out_in_play) must sum to 1.0."""
        batter = {'k': 0.20, 'bb': 0.10, 'hbp': 0.01,
                  'single': 0.15, 'double': 0.05, 'triple': 0.005, 'hr': 0.03}
        pitcher = {'k': 0.22, 'bb': 0.08, 'hbp': 0.01,
                   'single': 0.14, 'double': 0.04, 'triple': 0.004, 'hr': 0.025}
        league = {'k': 0.228, 'bb': 0.083, 'hbp': 0.012,
                  'single': 0.148, 'double': 0.046, 'triple': 0.005, 'hr': 0.032}

        result = compute_matchup_rates(batter, pitcher, league)
        total = sum(result.values())
        assert abs(total - 1.0) < 1e-10, f"Rates sum to {total}, expected 1.0"

    def test_contains_all_keys(self):
        """Result contains all outcome types plus out_in_play."""
        batter = {'k': 0.20, 'bb': 0.10, 'hbp': 0.01,
                  'single': 0.15, 'double': 0.05, 'triple': 0.005, 'hr': 0.03}
        pitcher = {'k': 0.22, 'bb': 0.08, 'hbp': 0.01,
                   'single': 0.14, 'double': 0.04, 'triple': 0.004, 'hr': 0.025}
        league = {'k': 0.228, 'bb': 0.083, 'hbp': 0.012,
                  'single': 0.148, 'double': 0.046, 'triple': 0.005, 'hr': 0.032}

        result = compute_matchup_rates(batter, pitcher, league)
        expected_keys = {'k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr', 'out_in_play'}
        assert set(result.keys()) == expected_keys

    def test_out_in_play_non_negative(self):
        """out_in_play should be non-negative even with extreme rates."""
        # Extreme high rates that would sum > 1 without scaling
        batter = {'k': 0.35, 'bb': 0.20, 'hbp': 0.05,
                  'single': 0.25, 'double': 0.10, 'triple': 0.02, 'hr': 0.08}
        pitcher = {'k': 0.30, 'bb': 0.15, 'hbp': 0.03,
                   'single': 0.20, 'double': 0.08, 'triple': 0.015, 'hr': 0.06}
        league = {'k': 0.228, 'bb': 0.083, 'hbp': 0.012,
                  'single': 0.148, 'double': 0.046, 'triple': 0.005, 'hr': 0.032}

        result = compute_matchup_rates(batter, pitcher, league)
        assert result['out_in_play'] >= 0
        assert abs(sum(result.values()) - 1.0) < 1e-10

    def test_league_average_matchup(self):
        """League-avg batter vs league-avg pitcher returns league rates."""
        league = {'k': 0.228, 'bb': 0.083, 'hbp': 0.012,
                  'single': 0.148, 'double': 0.046, 'triple': 0.005, 'hr': 0.032}

        result = compute_matchup_rates(league, league, league)
        for outcome in league:
            assert abs(result[outcome] - league[outcome]) < 1e-10


class TestMarcelShrinkage:
    """Tests for Marcel regression toward the mean."""

    def test_first_inning_pitcher(self):
        """
        Pitcher with 120 BF in first innings:
        r = 120 / (120 + 1200) = 0.0909
        observed K rate .300, league .228
        adjusted = 0.0909 * .300 + 0.9091 * .228 ≈ .235
        """
        r = 120 / (120 + 1200)
        assert abs(r - 0.0909) < 0.001, f"Expected r ≈ 0.091, got {r}"

        result = apply_marcel_shrinkage(0.300, 0.228, 120)
        expected = r * 0.300 + (1 - r) * 0.228
        assert abs(result - expected) < 1e-10
        assert abs(result - 0.2345) < 0.002, f"Expected ~.235, got {result}"

    def test_large_sample_minimal_shrinkage(self):
        """With many PAs, shrinkage is minimal — result near observed."""
        result = apply_marcel_shrinkage(0.300, 0.228, 6000)
        # r = 6000/7200 = 0.833
        assert abs(result - 0.300) < 0.02

    def test_zero_pa_returns_league(self):
        """With 0 PAs, result is exactly the league rate."""
        result = apply_marcel_shrinkage(0.300, 0.228, 0)
        assert result == 0.228

    def test_shrinkage_between_observed_and_league(self):
        """Result is always between observed and league rates."""
        result = apply_marcel_shrinkage(0.350, 0.228, 500)
        assert 0.228 <= result <= 0.350


class TestComputeWeightedRate:
    """Tests for Marcel multi-year weighted rate."""

    def test_three_year_weighting(self):
        """
        3 years of data with 5/4/3 weights should weight most recent heaviest.
        """
        rates = [0.300, 0.250, 0.200]  # most recent first
        pa_counts = [600, 550, 500]
        league = 0.228

        result = compute_weighted_rate(rates, pa_counts, league)

        # Manual calculation
        weighted_rate = (5 * 0.300 + 4 * 0.250 + 3 * 0.200) / 12  # 0.2583
        weighted_pa = 5 * 600 + 4 * 550 + 3 * 500  # 6700
        r = 6700 / (6700 + 1200)  # 0.848
        expected = r * weighted_rate + (1 - r) * league
        assert abs(result - expected) < 1e-10

    def test_most_recent_year_weighted_heaviest(self):
        """Changing only the most recent year should have the largest effect."""
        pa = [500, 500, 500]
        league = 0.228

        result_high_recent = compute_weighted_rate([0.350, 0.250, 0.250], pa, league)
        result_high_oldest = compute_weighted_rate([0.250, 0.250, 0.350], pa, league)
        # Higher recent rate should pull the result higher
        assert result_high_recent > result_high_oldest

    def test_single_year(self):
        """Works with only 1 year of data."""
        result = compute_weighted_rate([0.300], [600], 0.228)
        # Uses weight [5], so weighted_rate = 0.300, weighted_pa = 3000
        r = 3000 / (3000 + 1200)
        expected = r * 0.300 + (1 - r) * 0.228
        assert abs(result - expected) < 1e-10

    def test_two_years(self):
        """Works with only 2 years of data."""
        result = compute_weighted_rate([0.300, 0.250], [600, 500], 0.228)
        weighted_rate = (5 * 0.300 + 4 * 0.250) / 9
        weighted_pa = 5 * 600 + 4 * 500
        r = weighted_pa / (weighted_pa + 1200)
        expected = r * weighted_rate + (1 - r) * 0.228
        assert abs(result - expected) < 1e-10

    def test_no_data_returns_league(self):
        """With no data, returns league rate."""
        result = compute_weighted_rate([], [], 0.228)
        assert result == 0.228

    def test_custom_weights(self):
        """Custom weights are respected."""
        result = compute_weighted_rate(
            [0.300, 0.250, 0.200], [600, 550, 500], 0.228,
            weights=[1, 1, 1],
        )
        weighted_rate = (0.300 + 0.250 + 0.200) / 3
        weighted_pa = 600 + 550 + 500
        r = weighted_pa / (weighted_pa + 1200)
        expected = r * weighted_rate + (1 - r) * 0.228
        assert abs(result - expected) < 1e-10
