"""Betting math: odds conversion, vig removal, edge calculation, Kelly sizing."""

from scipy.optimize import brentq


def american_to_decimal(american_odds: int) -> float:
    """Convert American odds to decimal odds.

    -135 → 1.741, +115 → 2.15
    """
    if american_odds < 0:
        return 1 + 100 / abs(american_odds)
    else:
        return 1 + american_odds / 100


def decimal_to_implied(decimal_odds: float) -> float:
    """Simple implied probability: 1 / decimal_odds."""
    return 1 / decimal_odds


def remove_vig_power_method(
    nrfi_decimal: float, yrfi_decimal: float
) -> tuple[float, float]:
    """Power method vig removal.

    Find z such that (1/nrfi_decimal)^z + (1/yrfi_decimal)^z = 1.
    Return (true_nrfi_prob, true_yrfi_prob).
    """
    p1 = 1 / nrfi_decimal
    p2 = 1 / yrfi_decimal

    def equation(z: float) -> float:
        return p1**z + p2**z - 1

    z = brentq(equation, 0.01, 100.0)
    return (p1**z, p2**z)


def compute_edge(model_prob: float, implied_prob: float) -> float:
    """Edge = model_prob - implied_prob."""
    return model_prob - implied_prob


def kelly_fraction(
    model_prob: float,
    decimal_odds: float,
    fraction: float = 1 / 6,
    max_bet: float = 0.02,
) -> float:
    """Fractional Kelly criterion bet sizing.

    Full Kelly: f* = (p * d - 1) / (d - 1)
    Apply fractional Kelly (default 1/6), cap at max_bet (default 2%).
    Return 0 if edge is negative.
    """
    full_kelly = (model_prob * decimal_odds - 1) / (decimal_odds - 1)
    if full_kelly <= 0:
        return 0.0
    return min(full_kelly * fraction, max_bet)


def find_best_line(odds_list: list[dict]) -> dict:
    """Return the book with the best (highest) NRFI decimal odds.

    Each dict: {'book': str, 'nrfi_price': int, 'yrfi_price': int}
    """
    return max(odds_list, key=lambda x: american_to_decimal(x["nrfi_price"]))
