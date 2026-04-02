"""Tests for environmental adjustment functions."""

import pytest

from src.markov.adjustments import (
    adjust_for_catcher_framing,
    adjust_for_park,
    adjust_for_temperature,
    adjust_for_umpire,
    adjust_for_wind,
    apply_all_adjustments,
    normalize_rates,
)

# Realistic base rates (roughly league average)
BASE_RATES = {
    'k': 0.220,
    'bb': 0.080,
    'hbp': 0.010,
    'single': 0.150,
    'double': 0.045,
    'triple': 0.005,
    'hr': 0.030,
    'out_in_play': 0.460,
}


def _rates_sum(rates: dict) -> float:
    return sum(rates.values())


def _copy_rates() -> dict:
    return dict(BASE_RATES)


class TestNeutralInputs:
    """Neutral inputs should return rates unchanged."""

    def test_park_neutral(self):
        result = adjust_for_park(_copy_rates(), 100)
        for k, v in BASE_RATES.items():
            assert result[k] == pytest.approx(v, abs=1e-10)

    def test_temperature_neutral(self):
        result = adjust_for_temperature(_copy_rates(), 75)
        for k, v in BASE_RATES.items():
            assert result[k] == pytest.approx(v, abs=1e-10)

    def test_wind_calm(self):
        result = adjust_for_wind(_copy_rates(), 10, 'calm')
        for k, v in BASE_RATES.items():
            assert result[k] == pytest.approx(v, abs=1e-10)

    def test_wind_cross(self):
        result = adjust_for_wind(_copy_rates(), 15, 'cross_l')
        for k, v in BASE_RATES.items():
            assert result[k] == pytest.approx(v, abs=1e-10)

    def test_umpire_neutral(self):
        result = adjust_for_umpire(_copy_rates(), 0.0)
        for k, v in BASE_RATES.items():
            assert result[k] == pytest.approx(v, abs=1e-10)

    def test_framing_neutral(self):
        result = adjust_for_catcher_framing(_copy_rates(), 0.0)
        for k, v in BASE_RATES.items():
            assert result[k] == pytest.approx(v, abs=1e-10)

    def test_all_neutral(self):
        result = apply_all_adjustments(_copy_rates())
        for k, v in BASE_RATES.items():
            assert result[k] == pytest.approx(v, abs=1e-10)


class TestParkFactor:
    """Coors Field (park_hr_factor=115) should increase HR rate by 15%."""

    def test_coors_hr_increase(self):
        result = adjust_for_park(_copy_rates(), 115)
        expected_hr = BASE_RATES['hr'] * 1.15
        assert result['hr'] == pytest.approx(expected_hr, rel=1e-6)

    def test_coors_sum_to_one(self):
        result = adjust_for_park(_copy_rates(), 115)
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)

    def test_oracle_park_hr_decrease(self):
        result = adjust_for_park(_copy_rates(), 85)
        expected_hr = BASE_RATES['hr'] * 0.85
        assert result['hr'] == pytest.approx(expected_hr, rel=1e-6)

    def test_residual_absorbs_change(self):
        result = adjust_for_park(_copy_rates(), 115)
        # out_in_play should decrease to compensate for HR increase
        assert result['out_in_play'] < BASE_RATES['out_in_play']


class TestTemperature:
    """Hot day (95°F) should increase HR rate by ~3%."""

    def test_hot_day_hr_increase(self):
        result = adjust_for_temperature(_copy_rates(), 95)
        # (95 - 75) * 0.0015 = 0.03 → 3% increase
        expected_hr = BASE_RATES['hr'] * 1.03
        assert result['hr'] == pytest.approx(expected_hr, rel=1e-6)

    def test_hot_day_sum_to_one(self):
        result = adjust_for_temperature(_copy_rates(), 95)
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)

    def test_cold_day_hr_decrease(self):
        result = adjust_for_temperature(_copy_rates(), 45)
        # (45 - 75) * 0.0015 = -0.045 → 4.5% decrease
        expected_hr = BASE_RATES['hr'] * (1.0 + (45 - 75) * 0.0015)
        assert result['hr'] == pytest.approx(expected_hr, rel=1e-6)


class TestWind:
    """Wind out 15mph should increase HR by ~12%, wind in should decrease."""

    def test_wind_out_15mph(self):
        result = adjust_for_wind(_copy_rates(), 15, 'out')
        # multiplier = 1 + 15 * 0.008 = 1.12 → 12% increase
        expected_hr = BASE_RATES['hr'] * 1.12
        assert result['hr'] == pytest.approx(expected_hr, rel=1e-6)

    def test_wind_out_sum(self):
        result = adjust_for_wind(_copy_rates(), 15, 'out')
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)

    def test_wind_in_15mph(self):
        result = adjust_for_wind(_copy_rates(), 15, 'in')
        # multiplier = 1 - 15 * 0.008 = 0.88 → 12% decrease
        expected_hr = BASE_RATES['hr'] * 0.88
        assert result['hr'] == pytest.approx(expected_hr, rel=1e-6)

    def test_wind_in_sum(self):
        result = adjust_for_wind(_copy_rates(), 15, 'in')
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)

    def test_wind_in_floor(self):
        """Extreme wind in should floor multiplier at 0.5."""
        result = adjust_for_wind(_copy_rates(), 100, 'in')
        # 1 - 100 * 0.008 = -0.7, floored to 0.5
        expected_hr = BASE_RATES['hr'] * 0.5
        assert result['hr'] == pytest.approx(expected_hr, rel=1e-6)

    def test_cross_r_no_change(self):
        result = adjust_for_wind(_copy_rates(), 20, 'cross_r')
        assert result['hr'] == pytest.approx(BASE_RATES['hr'], abs=1e-10)


class TestUmpire:
    def test_generous_umpire(self):
        result = adjust_for_umpire(_copy_rates(), 0.02)
        assert result['bb'] == pytest.approx(0.10, abs=1e-10)

    def test_tight_umpire(self):
        result = adjust_for_umpire(_copy_rates(), -0.03)
        assert result['bb'] == pytest.approx(0.05, abs=1e-10)

    def test_clamp_low(self):
        result = adjust_for_umpire(_copy_rates(), -0.10)
        assert result['bb'] == pytest.approx(0.01, abs=1e-10)

    def test_clamp_high(self):
        result = adjust_for_umpire(_copy_rates(), 0.20)
        assert result['bb'] == pytest.approx(0.20, abs=1e-10)

    def test_sum(self):
        result = adjust_for_umpire(_copy_rates(), 0.02)
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)


class TestCatcherFraming:
    def test_elite_framer(self):
        # 2 framing runs above average → reduce BB by 0.006
        result = adjust_for_catcher_framing(_copy_rates(), 2.0)
        assert result['bb'] == pytest.approx(0.074, abs=1e-10)

    def test_poor_framer(self):
        # -1 framing run → increase BB by 0.003
        result = adjust_for_catcher_framing(_copy_rates(), -1.0)
        assert result['bb'] == pytest.approx(0.083, abs=1e-10)

    def test_sum(self):
        result = adjust_for_catcher_framing(_copy_rates(), 2.0)
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)


class TestNormalize:
    def test_negative_rate_zeroed(self):
        rates = _copy_rates()
        rates['hr'] = -0.05
        result = normalize_rates(rates)
        assert result['hr'] == 0.0
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)

    def test_overflow_scaled_down(self):
        rates = _copy_rates()
        rates['hr'] = 0.60  # push total way over 1.0
        result = normalize_rates(rates)
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)
        assert result['out_in_play'] >= 0.0

    def test_already_valid(self):
        result = normalize_rates(_copy_rates())
        for k, v in BASE_RATES.items():
            assert result[k] == pytest.approx(v, abs=1e-10)


class TestApplyAll:
    def test_combined_sum(self):
        result = apply_all_adjustments(
            _copy_rates(),
            park_hr_factor=115,
            temperature_f=90,
            wind_speed_mph=10,
            wind_relative='out',
            walk_rate_impact=0.01,
            framing_runs=1.0,
        )
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)

    def test_extreme_adjustments_no_negatives(self):
        """Extreme adjustments should not produce negative rates."""
        result = apply_all_adjustments(
            _copy_rates(),
            park_hr_factor=200,
            temperature_f=110,
            wind_speed_mph=30,
            wind_relative='out',
            walk_rate_impact=0.05,
            framing_runs=-5.0,
        )
        for k, v in result.items():
            assert v >= 0.0, f"{k} is negative: {v}"
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)

    def test_suppressive_extremes_no_negatives(self):
        """Extreme suppressive adjustments should not produce negative rates."""
        result = apply_all_adjustments(
            _copy_rates(),
            park_hr_factor=50,
            temperature_f=20,
            wind_speed_mph=50,
            wind_relative='in',
            walk_rate_impact=-0.10,
            framing_runs=10.0,
        )
        for k, v in result.items():
            assert v >= 0.0, f"{k} is negative: {v}"
        assert _rates_sum(result) == pytest.approx(1.0, abs=1e-10)
