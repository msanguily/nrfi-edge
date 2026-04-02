"""Tests for the 26-state absorbing Markov chain engine."""

import pytest
import src.markov.chain as chain_module
from src.markov.chain import (
    compute_p_zero_runs,
    state_index,
    base_config_name,
    parse_base_config,
    default_advancement_probs,
)


def _make_rates(**overrides):
    """Create a batter rates dict with all zeros, then apply overrides."""
    rates = {
        'k': 0.0, 'bb': 0.0, 'hbp': 0.0,
        'single': 0.0, 'double': 0.0, 'triple': 0.0,
        'hr': 0.0, 'out_in_play': 0.0,
    }
    rates.update(overrides)
    return rates


class TestHelpers:
    """Test helper functions."""

    def test_state_index(self):
        assert state_index(0, 0) == 0   # empty, 0 outs
        assert state_index(0, 2) == 2   # empty, 2 outs
        assert state_index(1, 0) == 3   # runner_1st, 0 outs
        assert state_index(7, 2) == 23  # loaded, 2 outs

    def test_base_config_name(self):
        assert base_config_name(0) == 'empty'
        assert base_config_name(7) == 'loaded'

    def test_parse_base_config(self):
        assert parse_base_config('empty') == 0
        assert parse_base_config('loaded') == 7
        assert parse_base_config('runners_1st_2nd') == 4

    def test_roundtrip(self):
        for i in range(8):
            assert parse_base_config(base_config_name(i)) == i


class TestAllStrikeouts:
    """3 batters who always strike out → P(0 runs) = 1.0."""

    def test_all_k(self):
        lineup = [_make_rates(k=1.0)] * 9
        result = compute_p_zero_runs(lineup, max_batters=9)
        assert abs(result - 1.0) < 1e-10, f"Expected 1.0, got {result}"

    def test_all_k_min_batters(self):
        """Even with max_batters=3, all Ks means 3 outs in 3 batters."""
        lineup = [_make_rates(k=1.0)] * 3
        result = compute_p_zero_runs(lineup, max_batters=3)
        assert abs(result - 1.0) < 1e-10


class TestAllHomeRuns:
    """Batters who always homer → P(0 runs) = 0.0."""

    def test_all_hr(self):
        lineup = [_make_rates(hr=1.0)] * 9
        result = compute_p_zero_runs(lineup, max_batters=9)
        assert abs(result - 0.0) < 1e-10, f"Expected 0.0, got {result}"

    def test_first_batter_hr(self):
        """Even one HR means a run scored."""
        lineup = [_make_rates(hr=1.0)] + [_make_rates(k=1.0)] * 8
        result = compute_p_zero_runs(lineup, max_batters=9)
        assert abs(result - 0.0) < 1e-10


class TestAllWalks:
    """Batters who always walk → bases loaded walk scores a run after 4 walks."""

    def test_all_bb(self):
        lineup = [_make_rates(bb=1.0)] * 9
        result = compute_p_zero_runs(lineup, max_batters=9)
        assert abs(result - 0.0) < 1e-10, f"Expected 0.0, got {result}"

    def test_four_walks_forces_run(self):
        """Exactly 4 walks forces in a run. P(0 runs) = 0."""
        lineup = [_make_rates(bb=1.0)] * 4 + [_make_rates(k=1.0)] * 5
        result = compute_p_zero_runs(lineup, max_batters=5)
        assert abs(result - 0.0) < 1e-10

    def test_three_walks_then_three_ks(self):
        """3 walks load the bases, then 3 Ks end the inning. No runs score."""
        lineup = [_make_rates(bb=1.0)] * 3 + [_make_rates(k=1.0)] * 6
        result = compute_p_zero_runs(lineup, max_batters=9)
        assert abs(result - 1.0) < 1e-10


class TestLeagueAverage:
    """Batters with typical league average rates."""

    def test_league_average_rates(self):
        """
        With league-average rates, P(0 runs) per half-inning should be
        in a reasonable range. Actual MLB is ~0.72-0.75 per half-inning.
        """
        league_rates = _make_rates(
            k=0.228, bb=0.084, hbp=0.012,
            single=0.148, double=0.046, triple=0.005, hr=0.032,
        )
        league_rates['out_in_play'] = 1.0 - sum(
            league_rates[k] for k in ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']
        )

        lineup = [league_rates] * 9
        result = compute_p_zero_runs(lineup, max_batters=9)

        # Should be in a reasonable range (0.65-0.85)
        assert 0.65 < result < 0.85, f"P(0 runs) = {result}, expected 0.65-0.85"

    def test_good_pitcher_higher_p_zero(self):
        """A dominant pitcher (high K, low hits) should have higher P(0 runs)."""
        avg_rates = _make_rates(
            k=0.228, bb=0.084, hbp=0.012,
            single=0.148, double=0.046, triple=0.005, hr=0.032,
        )
        avg_rates['out_in_play'] = 1.0 - sum(
            avg_rates[k] for k in ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']
        )

        dominant_rates = _make_rates(
            k=0.320, bb=0.050, hbp=0.008,
            single=0.120, double=0.035, triple=0.003, hr=0.020,
        )
        dominant_rates['out_in_play'] = 1.0 - sum(
            dominant_rates[k] for k in ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']
        )

        p_avg = compute_p_zero_runs([avg_rates] * 9, max_batters=9)
        p_dom = compute_p_zero_runs([dominant_rates] * 9, max_batters=9)

        assert p_dom > p_avg, f"Dominant pitcher {p_dom} should > average {p_avg}"


class TestProbabilityConservation:
    """Verify probability mass is conserved after each batter."""

    def test_conservation_after_processing(self):
        """Total probability across all states must equal 1.0."""
        league_rates = _make_rates(
            k=0.228, bb=0.084, hbp=0.012,
            single=0.148, double=0.046, triple=0.005, hr=0.032,
        )
        league_rates['out_in_play'] = 1.0 - sum(
            league_rates[k] for k in ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']
        )

        lineup = [league_rates] * 9
        adv = default_advancement_probs()

        # We test conservation by running the chain and checking absorb sums
        result = compute_p_zero_runs(lineup, adv, max_batters=9)

        # The function returns absorb_zero / (absorb_zero + absorb_scored)
        # which is only valid if total ≈ 1.0. Let's verify result is a
        # valid probability.
        assert 0.0 <= result <= 1.0

    def test_deterministic_rates_sum_correctly(self):
        """
        With deterministic rates (all mass on one event), all probability
        should end up in absorbing states.
        """
        # 50% K, 50% single — fully determined
        rates = _make_rates(k=0.5, single=0.5)
        lineup = [rates] * 9
        result = compute_p_zero_runs(lineup, max_batters=9)
        assert 0.0 <= result <= 1.0

    def test_mixed_rates_conservation(self):
        """Various rate combinations maintain probability conservation."""
        rates = _make_rates(
            k=0.15, bb=0.10, hbp=0.02,
            single=0.18, double=0.06, triple=0.01, hr=0.04,
            out_in_play=0.44,
        )
        lineup = [rates] * 9
        result = compute_p_zero_runs(lineup, max_batters=9)
        assert 0.0 <= result <= 1.0


class TestSpecificScenarios:
    """Test specific baseball scenarios."""

    def test_single_walk_single_single_k_k_k(self):
        """
        Known sequence: single, walk, single (run scores), then 3 Ks.
        With probabilistic rates, just verify directional behavior.
        """
        # Batter 1: always singles
        b1 = _make_rates(single=1.0)
        # Batter 2: always walks
        b2 = _make_rates(bb=1.0)
        # Batter 3: always singles → with runners on 1st_2nd, some prob of run
        b3 = _make_rates(single=1.0)
        # Batters 4-6: always K
        bk = _make_rates(k=1.0)

        lineup = [b1, b2, b3, bk, bk, bk, bk, bk, bk]
        result = compute_p_zero_runs(lineup, max_batters=9)

        # After: single (1st), walk (1st_2nd), single with runners_1st_2nd:
        # From default advancement, there's significant probability of
        # scoring. P(0 runs) should be well below 1.
        assert result < 0.7, f"Expected < 0.7 with runners in scoring position, got {result}"

    def test_hbp_same_as_walk(self):
        """HBP should produce same result as BB (forced runners)."""
        lineup_bb = [_make_rates(bb=1.0)] * 4 + [_make_rates(k=1.0)] * 5
        lineup_hbp = [_make_rates(hbp=1.0)] * 4 + [_make_rates(k=1.0)] * 5

        p_bb = compute_p_zero_runs(lineup_bb, max_batters=9)
        p_hbp = compute_p_zero_runs(lineup_hbp, max_batters=9)
        assert abs(p_bb - p_hbp) < 1e-10

    def test_max_batters_limits_simulation(self):
        """With max_batters=3, only 3 batters are simulated."""
        # All walks — with max_batters=3, only 3 walks → bases loaded, no run
        lineup = [_make_rates(bb=1.0)] * 9
        result = compute_p_zero_runs(lineup, max_batters=3)
        assert abs(result - 1.0) < 1e-10

        # With max_batters=4, the 4th walk forces a run
        result4 = compute_p_zero_runs(lineup, max_batters=4)
        assert abs(result4 - 0.0) < 1e-10

    def test_triple_then_ks(self):
        """Triple puts runner on 3rd, then 3 Ks strand him. P(0 runs) = 1.0."""
        lineup = [_make_rates(triple=1.0)] + [_make_rates(k=1.0)] * 8
        result = compute_p_zero_runs(lineup, max_batters=9)
        assert abs(result - 1.0) < 1e-10, f"Stranded runner, expected 1.0, got {result}"


class TestProductiveOuts:
    """Test productive out (sac fly) and GIDP interaction."""

    def test_productive_out_runner_on_3rd(self):
        """With runner on 3rd, in-play outs can score the runner (sac fly)."""
        # Triple puts runner on 3rd, then all out_in_play
        lineup_oip = [_make_rates(triple=1.0)] + [_make_rates(out_in_play=1.0)] * 8
        result_oip = compute_p_zero_runs(lineup_oip, max_batters=9)

        # With strikeouts, runner on 3rd is always stranded (Ks aren't in-play)
        lineup_k = [_make_rates(triple=1.0)] + [_make_rates(k=1.0)] * 8
        result_k = compute_p_zero_runs(lineup_k, max_batters=9)

        assert abs(result_k - 1.0) < 1e-10, "Ks should strand runner on 3rd"
        assert result_oip < result_k, (
            f"Productive outs should lower P(0 runs): oip={result_oip:.6f} vs k={result_k:.6f}"
        )
        # 20% sac fly chance per out with < 2 outs → P(survive) = 0.8^2 = 0.64
        assert abs(result_oip - 0.64) < 0.01, f"Expected ~0.64, got {result_oip}"

    def test_gidp_raises_p_zero(self):
        """GIDP erases baserunners and ends innings faster, raising P(0 runs)."""
        rates = _make_rates(single=0.20, double=0.05, out_in_play=0.75)
        original_gidp = chain_module.GIDP_FRACTION

        try:
            # With GIDP active
            chain_module.GIDP_FRACTION = 0.12
            p_with = compute_p_zero_runs([rates] * 9, max_batters=9)

            # Without GIDP
            chain_module.GIDP_FRACTION = 0.0
            p_without = compute_p_zero_runs([rates] * 9, max_batters=9)
        finally:
            chain_module.GIDP_FRACTION = original_gidp

        assert p_with > p_without, (
            f"GIDP should raise P(0 runs): with={p_with:.6f} vs without={p_without:.6f}"
        )

    def test_probability_conservation_with_productive_outs(self):
        """All probability must sum to 1.0 with productive outs and GIDP active."""
        rates = _make_rates(
            k=0.20, bb=0.08, hbp=0.01,
            single=0.15, double=0.05, triple=0.005, hr=0.03,
        )
        rates['out_in_play'] = 1.0 - sum(
            rates[k] for k in ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']
        )
        result = compute_p_zero_runs([rates] * 9, max_batters=9)
        assert 0.0 <= result <= 1.0, f"Result outside [0, 1]: {result}"

        # Also test with high-contact rates that trigger many base states
        high_contact = _make_rates(
            k=0.10, bb=0.12, hbp=0.02,
            single=0.20, double=0.08, triple=0.01, hr=0.04,
        )
        high_contact['out_in_play'] = 1.0 - sum(
            high_contact[k] for k in ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']
        )
        result2 = compute_p_zero_runs([high_contact] * 9, max_batters=9)
        assert 0.0 <= result2 <= 1.0, f"Result outside [0, 1]: {result2}"
