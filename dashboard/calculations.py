"""P/L, CLV, ROI, odds conversion math for the NRFI dashboard."""


def american_to_decimal(american_odds: int) -> float:
    """Convert American odds to decimal odds. -135 -> 1.741, +115 -> 2.15."""
    if american_odds < 0:
        return 1 + 100 / abs(american_odds)
    return 1 + american_odds / 100


def american_to_implied(american_odds: int) -> float:
    """Convert American odds to implied probability (no vig removal)."""
    if american_odds < 0:
        return abs(american_odds) / (abs(american_odds) + 100)
    return 100 / (american_odds + 100)


def calculate_profit(american_odds: int, units_wagered: float, won: bool) -> float:
    """
    Calculate profit/loss from a bet.
    Won at -110 wagering 1 unit: profit = 1 * (100/110) = +0.909 units
    Won at +150 wagering 1 unit: profit = 1 * (150/100) = +1.500 units
    Lost any bet wagering 1 unit: profit = -1.000 units
    """
    if not won:
        return -units_wagered
    if american_odds > 0:
        return units_wagered * (american_odds / 100)
    else:
        return units_wagered * (100 / abs(american_odds))


def calculate_clv(bet_odds: int, closing_odds: int) -> float:
    """
    CLV = closing_implied_prob - bet_implied_prob.
    Positive CLV = you got better odds than the closing line.
    Example: bet at -110 (52.4%), closes at -125 (55.6%) -> CLV = +3.2%
    """
    bet_implied = american_to_implied(bet_odds)
    closing_implied = american_to_implied(closing_odds)
    return closing_implied - bet_implied


def clv_beat_rate(bets: list) -> float:
    """
    Percentage of bets where you beat the closing line.
    Above 50% = consistently finding value before market corrects.
    Each bet dict must have 'clv' key (float or None).
    """
    valid = [b for b in bets if b.get("clv") is not None]
    if not valid:
        return 0.0
    beats = sum(1 for b in valid if b["clv"] > 0)
    return beats / len(valid) * 100


def calculate_roi(total_profit: float, total_wagered: float) -> float:
    """ROI = total_profit / total_wagered * 100."""
    if total_wagered == 0:
        return 0.0
    return total_profit / total_wagered * 100


def format_odds(american_odds) -> str:
    """Format American odds with sign. +110, -135."""
    if american_odds is None:
        return "-"
    odds = int(american_odds)
    return f"+{odds}" if odds > 0 else str(odds)


def format_prob(prob) -> str:
    """Format probability as percentage. 0.567 -> '56.7%'."""
    if prob is None:
        return "-"
    return f"{float(prob) * 100:.1f}%"


def format_pl(units) -> str:
    """Format P/L with sign. +2.50u, -1.00u."""
    if units is None:
        return "-"
    units = float(units)
    sign = "+" if units > 0 else ""
    return f"{sign}{units:.2f}u"


def format_edge(edge) -> str:
    """Format edge as percentage. 0.043 -> '4.3%'."""
    if edge is None:
        return "-"
    return f"{float(edge) * 100:.1f}%"


def format_clv(clv) -> str:
    """Format CLV as percentage. 0.021 -> '+2.1%'."""
    if clv is None:
        return "-"
    clv = float(clv)
    sign = "+" if clv > 0 else ""
    return f"{sign}{clv * 100:.1f}%"


def current_streak(results: list) -> str:
    """
    Calculate current W/L streak from a list of bools (most recent first).
    Returns e.g. 'W5' or 'L3'.
    """
    if not results:
        return "-"
    current = results[0]
    count = 0
    for r in results:
        if r == current:
            count += 1
        else:
            break
    prefix = "W" if current else "L"
    return f"{prefix}{count}"


# ---------------------------------------------------------------------------
# Confidence tier classification
# ---------------------------------------------------------------------------

TIER_STRONG = "Strong"
TIER_VALUE = "Value"
TIER_LEAN = "Lean"

# Thresholds
STRONG_EDGE = 0.05   # 5% edge
STRONG_PROB = 0.54   # 54% calibrated probability
BET_EDGE = 0.03      # 3% minimum edge for any bet recommendation

TIER_COLORS = {TIER_STRONG: "#FFD700", TIER_VALUE: "#00cc66", TIER_LEAN: "#4488ff"}
TIER_LABELS = {TIER_STRONG: "Strong Pick", TIER_VALUE: "Value Play", TIER_LEAN: "Lean"}
TIER_ORDER = {TIER_STRONG: 0, TIER_VALUE: 1, TIER_LEAN: 2, None: 3}


def classify_tier(edge, prob_calibrated, prob_raw=None):
    """
    Classify a prediction into a confidence tier.

    Strong: edge >= 5% AND calibrated prob >= 54%
    Value:  edge >= 5% OR calibrated prob >= 54% (but not both)
    Lean:   edge >= 3% but neither signal elevated
    None:   below minimum edge or no odds data
    """
    if edge is None:
        return None
    edge = float(edge)
    if edge < BET_EDGE:
        return None
    prob = float(prob_calibrated) if prob_calibrated is not None else (
        float(prob_raw) if prob_raw is not None else None
    )
    high_edge = edge >= STRONG_EDGE
    high_prob = prob is not None and prob >= STRONG_PROB
    if high_edge and high_prob:
        return TIER_STRONG
    if high_edge or high_prob:
        return TIER_VALUE
    return TIER_LEAN
