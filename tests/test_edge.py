"""Tests for src/betting/edge.py."""

import pytest
from src.betting.edge import (
    american_to_decimal,
    compute_edge,
    decimal_to_implied,
    find_best_line,
    kelly_fraction,
    remove_vig_power_method,
)


class TestAmericanToDecimal:
    def test_negative_135(self):
        assert american_to_decimal(-135) == pytest.approx(1.741, abs=0.001)

    def test_positive_115(self):
        assert american_to_decimal(115) == pytest.approx(2.15, abs=0.001)

    def test_negative_110(self):
        assert american_to_decimal(-110) == pytest.approx(1.909, abs=0.001)

    def test_even_money(self):
        assert american_to_decimal(100) == pytest.approx(2.0, abs=0.001)


class TestPowerMethod:
    def test_standard_nrfi_line(self):
        """NRFI -135 / YRFI +115 → true NRFI prob ≈ 0.558."""
        nrfi_dec = american_to_decimal(-135)
        yrfi_dec = american_to_decimal(115)
        nrfi_prob, yrfi_prob = remove_vig_power_method(nrfi_dec, yrfi_dec)
        assert nrfi_prob == pytest.approx(0.558, abs=0.005)
        assert yrfi_prob == pytest.approx(1 - nrfi_prob, abs=0.001)

    def test_even_vig(self):
        """-110/-110 (standard vig) should give 0.50/0.50."""
        dec = american_to_decimal(-110)
        p1, p2 = remove_vig_power_method(dec, dec)
        assert p1 == pytest.approx(0.50, abs=0.001)
        assert p2 == pytest.approx(0.50, abs=0.001)


class TestKelly:
    def test_positive_edge(self):
        """72% model prob at 1.80 decimal odds."""
        full = (0.72 * 1.80 - 1) / (1.80 - 1)
        assert full == pytest.approx(0.370, abs=0.001)

        sixth = kelly_fraction(0.72, 1.80, fraction=1 / 6, max_bet=1.0)
        assert sixth == pytest.approx(0.062, abs=0.002)

        capped = kelly_fraction(0.72, 1.80, fraction=1 / 6, max_bet=0.02)
        assert capped == pytest.approx(0.02, abs=0.001)

    def test_negative_edge(self):
        """Negative edge returns 0."""
        assert kelly_fraction(0.40, 1.80) == 0.0


class TestFindBestLine:
    def test_selects_highest_nrfi_decimal(self):
        odds = [
            {"book": "draftkings", "nrfi_price": -135, "yrfi_price": 115},
            {"book": "fanduel", "nrfi_price": -125, "yrfi_price": 105},
            {"book": "betmgm", "nrfi_price": -140, "yrfi_price": 120},
        ]
        best = find_best_line(odds)
        assert best["book"] == "fanduel"
