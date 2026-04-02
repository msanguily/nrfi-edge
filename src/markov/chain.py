"""
26-state absorbing Markov chain for computing P(0 runs) in a half-inning.

States 0-23: 24 transient states (8 baserunner configs × 3 out counts)
State 24: absorbing — 3 outs with 0 runs scored
State 25: absorbing — 3 outs with ≥1 run scored
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_CONFIGS = [
    'empty',            # 0
    'runner_1st',       # 1
    'runner_2nd',       # 2
    'runner_3rd',       # 3
    'runners_1st_2nd',  # 4
    'runners_1st_3rd',  # 5
    'runners_2nd_3rd',  # 6
    'loaded',           # 7
]

_BASE_NAME_TO_INDEX = {name: i for i, name in enumerate(BASE_CONFIGS)}

NUM_TRANSIENT = 24      # 8 base configs × 3 outs
STATE_ABSORB_ZERO = 24  # 3 outs, 0 runs
STATE_ABSORB_SCORED = 25  # 3 outs, ≥1 run

DEFAULT_GIDP_FRACTION = 0.12  # ~12% of out-in-play with runner on 1st and < 2 outs
PRODUCTIVE_OUT_FRACTION = 0.20  # ~20% of out-in-play with runner on 3rd and < 2 outs
LEAGUE_AVG_GB_RATE = 0.44  # league average ground ball rate
LEAGUE_AVG_SPRINT_SPEED = 27.0  # ft/sec, Statcast league average


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def state_index(base_config: int, outs: int) -> int:
    """Convert (base_config, outs) to a state index 0-23."""
    return base_config * 3 + outs


def base_config_name(config: int) -> str:
    """Return the name for a base configuration index."""
    return BASE_CONFIGS[config]


def parse_base_config(name: str) -> int:
    """Return the index for a base configuration name."""
    return _BASE_NAME_TO_INDEX[name]


def _runners_on(config: int) -> tuple:
    """Return (on_1st, on_2nd, on_3rd) booleans for a base config."""
    return (
        config in (1, 4, 5, 7),  # 1st
        config in (2, 4, 6, 7),  # 2nd
        config in (3, 5, 6, 7),  # 3rd
    )


def _config_from_runners(on_1st: bool, on_2nd: bool, on_3rd: bool) -> int:
    """Return base config index from runner booleans."""
    key = (on_1st, on_2nd, on_3rd)
    mapping = {
        (False, False, False): 0,
        (True,  False, False): 1,
        (False, True,  False): 2,
        (False, False, True):  3,
        (True,  True,  False): 4,
        (True,  False, True):  5,
        (False, True,  True):  6,
        (True,  True,  True):  7,
    }
    return mapping[key]


# ---------------------------------------------------------------------------
# Default advancement probabilities (Retrosheet league averages)
# ---------------------------------------------------------------------------

def default_advancement_probs() -> dict:
    """
    Return default baserunner advancement probabilities from Retrosheet data.

    Key: (base_state_name, event_type)
    Value: list of {result_state, runs_scored, probability}
    """
    return {
        # ---- SINGLE ----
        ('empty', 'single'): [
            {'result_state': 'runner_1st', 'runs_scored': 0, 'probability': 1.0},
        ],
        ('runner_1st', 'single'): [
            {'result_state': 'runners_1st_2nd', 'runs_scored': 0, 'probability': 0.68},
            {'result_state': 'runners_1st_3rd', 'runs_scored': 0, 'probability': 0.29},
            {'result_state': 'runner_1st', 'runs_scored': 1, 'probability': 0.03},
        ],
        ('runner_2nd', 'single'): [
            {'result_state': 'runner_1st', 'runs_scored': 1, 'probability': 0.60},
            {'result_state': 'runners_1st_3rd', 'runs_scored': 0, 'probability': 0.40},
        ],
        ('runner_3rd', 'single'): [
            {'result_state': 'runner_1st', 'runs_scored': 1, 'probability': 0.95},
            {'result_state': 'runners_1st_3rd', 'runs_scored': 0, 'probability': 0.05},
        ],
        ('runners_1st_2nd', 'single'): [
            {'result_state': 'runners_1st_2nd', 'runs_scored': 1, 'probability': 0.42},
            {'result_state': 'runners_1st_3rd', 'runs_scored': 1, 'probability': 0.18},
            {'result_state': 'loaded', 'runs_scored': 0, 'probability': 0.30},
            {'result_state': 'runners_1st_2nd', 'runs_scored': 0, 'probability': 0.10},
        ],
        ('runners_1st_3rd', 'single'): [
            {'result_state': 'runners_1st_2nd', 'runs_scored': 1, 'probability': 0.55},
            {'result_state': 'runners_1st_3rd', 'runs_scored': 1, 'probability': 0.25},
            {'result_state': 'runner_1st', 'runs_scored': 2, 'probability': 0.05},
            {'result_state': 'runners_1st_2nd', 'runs_scored': 0, 'probability': 0.15},
        ],
        ('runners_2nd_3rd', 'single'): [
            {'result_state': 'runner_1st', 'runs_scored': 2, 'probability': 0.55},
            {'result_state': 'runners_1st_3rd', 'runs_scored': 1, 'probability': 0.30},
            {'result_state': 'runners_1st_2nd', 'runs_scored': 1, 'probability': 0.15},
        ],
        ('loaded', 'single'): [
            {'result_state': 'runners_1st_2nd', 'runs_scored': 2, 'probability': 0.35},
            {'result_state': 'loaded', 'runs_scored': 1, 'probability': 0.30},
            {'result_state': 'runners_1st_3rd', 'runs_scored': 2, 'probability': 0.15},
            {'result_state': 'runners_1st_2nd', 'runs_scored': 1, 'probability': 0.20},
        ],

        # ---- DOUBLE ----
        ('empty', 'double'): [
            {'result_state': 'runner_2nd', 'runs_scored': 0, 'probability': 1.0},
        ],
        ('runner_1st', 'double'): [
            {'result_state': 'runners_2nd_3rd', 'runs_scored': 0, 'probability': 0.55},
            {'result_state': 'runner_2nd', 'runs_scored': 1, 'probability': 0.45},
        ],
        ('runner_2nd', 'double'): [
            {'result_state': 'runner_2nd', 'runs_scored': 1, 'probability': 1.0},
        ],
        ('runner_3rd', 'double'): [
            {'result_state': 'runner_2nd', 'runs_scored': 1, 'probability': 1.0},
        ],
        ('runners_1st_2nd', 'double'): [
            {'result_state': 'runners_2nd_3rd', 'runs_scored': 1, 'probability': 0.45},
            {'result_state': 'runner_2nd', 'runs_scored': 2, 'probability': 0.55},
        ],
        ('runners_1st_3rd', 'double'): [
            {'result_state': 'runners_2nd_3rd', 'runs_scored': 1, 'probability': 0.40},
            {'result_state': 'runner_2nd', 'runs_scored': 2, 'probability': 0.60},
        ],
        ('runners_2nd_3rd', 'double'): [
            {'result_state': 'runner_2nd', 'runs_scored': 2, 'probability': 1.0},
        ],
        ('loaded', 'double'): [
            {'result_state': 'runners_2nd_3rd', 'runs_scored': 2, 'probability': 0.40},
            {'result_state': 'runner_2nd', 'runs_scored': 3, 'probability': 0.60},
        ],

        # ---- TRIPLE ----
        ('empty', 'triple'): [
            {'result_state': 'runner_3rd', 'runs_scored': 0, 'probability': 1.0},
        ],
        ('runner_1st', 'triple'): [
            {'result_state': 'runner_3rd', 'runs_scored': 1, 'probability': 1.0},
        ],
        ('runner_2nd', 'triple'): [
            {'result_state': 'runner_3rd', 'runs_scored': 1, 'probability': 1.0},
        ],
        ('runner_3rd', 'triple'): [
            {'result_state': 'runner_3rd', 'runs_scored': 1, 'probability': 1.0},
        ],
        ('runners_1st_2nd', 'triple'): [
            {'result_state': 'runner_3rd', 'runs_scored': 2, 'probability': 1.0},
        ],
        ('runners_1st_3rd', 'triple'): [
            {'result_state': 'runner_3rd', 'runs_scored': 2, 'probability': 1.0},
        ],
        ('runners_2nd_3rd', 'triple'): [
            {'result_state': 'runner_3rd', 'runs_scored': 2, 'probability': 1.0},
        ],
        ('loaded', 'triple'): [
            {'result_state': 'runner_3rd', 'runs_scored': 3, 'probability': 1.0},
        ],
    }


# ---------------------------------------------------------------------------
# Walk / HBP advancement (deterministic forced-runner logic)
# ---------------------------------------------------------------------------

def _advance_walk(config: int) -> tuple:
    """
    Advance runners for a walk/HBP. Only forced runners move.
    Returns (new_base_config, runs_scored).
    """
    on_1st, on_2nd, on_3rd = _runners_on(config)

    runs = 0
    # Batter goes to 1st; forced runners advance only if forced
    new_1st = True
    if on_1st:
        new_2nd = True
        if on_2nd:
            new_3rd = True
            if on_3rd:
                runs = 1
            # else new_3rd already True
        else:
            new_2nd = True
            new_3rd = on_3rd
    else:
        new_2nd = on_2nd
        new_3rd = on_3rd

    new_config = _config_from_runners(new_1st, new_2nd, new_3rd)
    return new_config, runs


# ---------------------------------------------------------------------------
# Home run (all runners + batter score)
# ---------------------------------------------------------------------------

def _advance_hr(config: int) -> int:
    """Return runs scored on a HR (all runners + batter)."""
    on_1st, on_2nd, on_3rd = _runners_on(config)
    return 1 + int(on_1st) + int(on_2nd) + int(on_3rd)


# ---------------------------------------------------------------------------
# GIDP logic
# ---------------------------------------------------------------------------

def _can_gidp(config: int, outs: int) -> bool:
    """GIDP possible when runner on 1st and fewer than 2 outs."""
    on_1st, _, _ = _runners_on(config)
    return on_1st and outs < 2


def _gidp_result(config: int, outs: int) -> tuple:
    """
    Result of a GIDP: lead runner and batter out, other runners stay.
    Returns (new_base_config, new_outs).
    """
    on_1st, on_2nd, on_3rd = _runners_on(config)
    # Remove runner on 1st (force out), batter out too
    new_outs = outs + 2
    # Runner on 1st is removed; other runners don't advance
    new_config = _config_from_runners(False, on_2nd, on_3rd)
    return new_config, new_outs


def _productive_out_result(config: int) -> tuple:
    """
    Result of a productive out (sac fly/groundout scoring runner from 3rd).
    Returns (new_base_config, runs_scored).
    Runner on 3rd scores; other runners stay. Outs handled by caller.
    """
    on_1st, on_2nd, _ = _runners_on(config)
    new_config = _config_from_runners(on_1st, on_2nd, False)
    return new_config, 1


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def compute_p_zero_runs(
    batter_matchup_rates: list,
    advancement_probs: dict = None,
    max_batters: int = 5,
) -> float:
    """
    Compute P(0 runs scored) for a half-inning using a 26-state absorbing
    Markov chain.

    Args:
        batter_matchup_rates: List of up to 9 dicts, each with keys:
            k, bb, hbp, single, double, triple, hr, out_in_play
        advancement_probs: Dict keyed by (base_state_name, event_type) with
            list of {result_state, runs_scored, probability} outcomes.
            If None, uses default Retrosheet averages.
        max_batters: Maximum batters to simulate (default 5).

    Returns:
        Probability that 0 runs score in the half-inning.
    """
    if advancement_probs is None:
        advancement_probs = default_advancement_probs()

    # Two parallel 24-element vectors tracking probability distribution
    # over transient states, split by whether runs have scored yet.
    zero_runs = [0.0] * NUM_TRANSIENT
    scored = [0.0] * NUM_TRANSIENT
    zero_runs[0] = 1.0  # start: empty bases, 0 outs, 0 runs

    absorb_zero = 0.0
    absorb_scored = 0.0

    num_batters = min(max_batters, len(batter_matchup_rates))

    for batter_idx in range(num_batters):
        rates = batter_matchup_rates[batter_idx]

        new_zero = [0.0] * NUM_TRANSIENT
        new_scored = [0.0] * NUM_TRANSIENT
        new_absorb_zero = absorb_zero
        new_absorb_scored = absorb_scored

        for si in range(NUM_TRANSIENT):
            prob_z = zero_runs[si]
            prob_s = scored[si]
            total_prob = prob_z + prob_s
            if total_prob < 1e-15:
                continue

            config = si // 3
            outs = si % 3
            config_name = BASE_CONFIGS[config]

            # --- Strikeout ---
            new_absorb_zero, new_absorb_scored = (
                _apply_out_event_ret(
                    rates['k'], config, outs, 0,
                    prob_z, prob_s,
                    new_zero, new_scored,
                    new_absorb_zero, new_absorb_scored,
                )
            )

            # --- Out in play (with productive out + GIDP splits) ---
            oip_rate = rates['out_in_play']
            on_1st, _, on_3rd = _runners_on(config)
            apply_sac = on_3rd and outs < 2
            # Loaded (config 7) excluded from GIDP — DP usually scores a run
            apply_gidp = on_1st and outs < 2 and config != 7

            # Per-batter GIDP fraction (from pitcher GB% and batter speed)
            batter_gidp = rates.get('gidp_fraction', DEFAULT_GIDP_FRACTION)

            if apply_sac and apply_gidp:
                # runners_1st_3rd (config 5): sac fly 20%, then GIDP
                # within remaining 80%
                sac_rate = oip_rate * PRODUCTIVE_OUT_FRACTION
                remaining = oip_rate * (1 - PRODUCTIVE_OUT_FRACTION)
                gidp_rate = remaining * batter_gidp
                regular_rate = remaining * (1 - batter_gidp)

                # Regular out: runners stay, outs += 1
                new_absorb_zero, new_absorb_scored = (
                    _apply_out_event_ret(
                        regular_rate, config, outs, 0,
                        prob_z, prob_s,
                        new_zero, new_scored,
                        new_absorb_zero, new_absorb_scored,
                    )
                )

                # Productive out: runner on 3rd scores, outs += 1
                prod_config, prod_runs = _productive_out_result(config)
                new_absorb_zero, new_absorb_scored = (
                    _apply_out_event_ret(
                        sac_rate, prod_config, outs, prod_runs,
                        prob_z, prob_s,
                        new_zero, new_scored,
                        new_absorb_zero, new_absorb_scored,
                    )
                )

                # GIDP: outs += 2, runner on 1st removed
                gdp_config, gdp_outs = _gidp_result(config, outs)
                if gdp_outs >= 3:
                    new_absorb_zero += prob_z * gidp_rate
                    new_absorb_scored += prob_s * gidp_rate
                else:
                    dest = state_index(gdp_config, gdp_outs)
                    new_zero[dest] += prob_z * gidp_rate
                    new_scored[dest] += prob_s * gidp_rate

            elif apply_sac:
                # Runner on 3rd, no GIDP (configs 3, 6, 7)
                sac_rate = oip_rate * PRODUCTIVE_OUT_FRACTION
                regular_rate = oip_rate * (1 - PRODUCTIVE_OUT_FRACTION)

                # Regular out
                new_absorb_zero, new_absorb_scored = (
                    _apply_out_event_ret(
                        regular_rate, config, outs, 0,
                        prob_z, prob_s,
                        new_zero, new_scored,
                        new_absorb_zero, new_absorb_scored,
                    )
                )

                # Productive out
                prod_config, prod_runs = _productive_out_result(config)
                new_absorb_zero, new_absorb_scored = (
                    _apply_out_event_ret(
                        sac_rate, prod_config, outs, prod_runs,
                        prob_z, prob_s,
                        new_zero, new_scored,
                        new_absorb_zero, new_absorb_scored,
                    )
                )

            elif apply_gidp:
                # Runner on 1st, no sac fly (configs 1, 4)
                gidp_rate = oip_rate * batter_gidp
                regular_rate = oip_rate * (1 - batter_gidp)

                # Regular out
                new_absorb_zero, new_absorb_scored = (
                    _apply_out_event_ret(
                        regular_rate, config, outs, 0,
                        prob_z, prob_s,
                        new_zero, new_scored,
                        new_absorb_zero, new_absorb_scored,
                    )
                )

                # GIDP: 2 outs, remove lead runner
                gdp_config, gdp_outs = _gidp_result(config, outs)
                if gdp_outs >= 3:
                    new_absorb_zero += prob_z * gidp_rate
                    new_absorb_scored += prob_s * gidp_rate
                else:
                    dest = state_index(gdp_config, gdp_outs)
                    new_zero[dest] += prob_z * gidp_rate
                    new_scored[dest] += prob_s * gidp_rate

            else:
                # No special splits (empty, runner_2nd, or outs == 2)
                new_absorb_zero, new_absorb_scored = (
                    _apply_out_event_ret(
                        oip_rate, config, outs, 0,
                        prob_z, prob_s,
                        new_zero, new_scored,
                        new_absorb_zero, new_absorb_scored,
                    )
                )

            # --- Walk / HBP ---
            for event in ('bb', 'hbp'):
                rate = rates[event]
                if rate < 1e-15:
                    continue
                new_config, runs = _advance_walk(config)
                dest = state_index(new_config, outs)
                if runs > 0:
                    # All zero_runs mass moves to scored
                    new_scored[dest] += prob_z * rate
                    new_scored[dest] += prob_s * rate
                else:
                    new_zero[dest] += prob_z * rate
                    new_scored[dest] += prob_s * rate

            # --- Home run ---
            hr_rate = rates['hr']
            if hr_rate > 1e-15:
                # All runners + batter score; bases empty, same outs
                dest = state_index(0, outs)  # empty bases
                # HR always scores at least 1 run
                new_scored[dest] += prob_z * hr_rate
                new_scored[dest] += prob_s * hr_rate

            # --- Single / Double / Triple ---
            for event in ('single', 'double', 'triple'):
                rate = rates[event]
                if rate < 1e-15:
                    continue

                adv_key = (config_name, event)
                outcomes = advancement_probs.get(adv_key)
                if outcomes is None:
                    # Fallback: treat as a generic single-type advance
                    outcomes = _fallback_advancement(config_name, event)

                for outcome in outcomes:
                    res_config = parse_base_config(outcome['result_state'])
                    runs = outcome['runs_scored']
                    p = outcome['probability']
                    dest = state_index(res_config, outs)

                    mass_z = prob_z * rate * p
                    mass_s = prob_s * rate * p

                    if runs > 0:
                        new_scored[dest] += mass_z + mass_s
                    else:
                        new_zero[dest] += mass_z
                        new_scored[dest] += mass_s

        zero_runs = new_zero
        scored = new_scored
        absorb_zero = new_absorb_zero
        absorb_scored = new_absorb_scored

    # Any remaining transient probability: runners left on base when we stop
    # simulating batters. Treat as still in transient states — in a real
    # half-inning, more batters would come up. For max_batters large enough
    # (≥9), virtually all mass is absorbed.
    # Conservative: remaining zero_runs mass counts toward zero runs,
    # remaining scored mass counts toward scored.
    absorb_zero += sum(zero_runs)
    absorb_scored += sum(scored)

    total = absorb_zero + absorb_scored
    if total < 1e-15:
        return 1.0
    return absorb_zero / total


def _apply_out_event_ret(
    rate: float, config: int, outs: int, runs_on_event: int,
    prob_z: float, prob_s: float,
    new_zero: list, new_scored: list,
    absorb_zero: float, absorb_scored: float,
) -> tuple:
    """
    Apply an out event (K or out_in_play) and return updated absorb values.
    Runners stay; outs += 1. If outs reaches 3, absorb.
    """
    if rate < 1e-15:
        return absorb_zero, absorb_scored

    new_outs = outs + 1
    if new_outs >= 3:
        if runs_on_event > 0:
            absorb_scored += (prob_z + prob_s) * rate
        else:
            absorb_zero += prob_z * rate
            absorb_scored += prob_s * rate
    else:
        dest = state_index(config, new_outs)
        if runs_on_event > 0:
            new_scored[dest] += (prob_z + prob_s) * rate
        else:
            new_zero[dest] += prob_z * rate
            new_scored[dest] += prob_s * rate

    return absorb_zero, absorb_scored


def compute_gidp_fraction(pitcher_gb_rate: float = None,
                          batter_sprint_speed: float = None) -> float:
    """
    Compute per-matchup GIDP fraction based on pitcher GB% and batter speed.

    Research (CBS Sports, FanGraphs, Hardball Times):
    - League avg GIDP per DP opportunity: ~11-12%
    - Extreme GB pitchers (65%+ GB): ~13-15%
    - Extreme FB pitchers (<42% GB): ~7-9%
    - Fast runners reduce GIDP, slow runners increase it

    Formula: base * (pitcher_gb / league_gb) * speed_factor
    """
    base = DEFAULT_GIDP_FRACTION

    # Pitcher GB% adjustment
    if pitcher_gb_rate is not None and pitcher_gb_rate > 0:
        gb_multiplier = pitcher_gb_rate / LEAGUE_AVG_GB_RATE
        # Clamp to reasonable range (0.5x to 1.8x)
        gb_multiplier = max(0.5, min(1.8, gb_multiplier))
    else:
        gb_multiplier = 1.0

    # Batter sprint speed adjustment
    # Fast runners (30+ ft/sec) avoid ~20% more GIDP
    # Slow runners (24- ft/sec) hit into ~20% more GIDP
    if batter_sprint_speed is not None and batter_sprint_speed > 0:
        speed_diff = batter_sprint_speed - LEAGUE_AVG_SPRINT_SPEED
        # ~6.7% change per ft/sec from average
        speed_factor = 1.0 - speed_diff * 0.067
        speed_factor = max(0.7, min(1.3, speed_factor))
    else:
        speed_factor = 1.0

    return base * gb_multiplier * speed_factor


def speed_adjusted_advancement(base_probs: dict,
                               runner_speeds: dict = None) -> dict:
    """
    Adjust baserunner advancement probabilities based on runner sprint speed.

    For fast runners (30+ ft/sec):
    - 1st-to-3rd on single: 29% → ~40%
    - Scoring from 2nd on single: 60% → ~75%
    For slow runners (24- ft/sec): opposite adjustments.

    runner_speeds: dict of {base_position: sprint_speed} for current runners
    Returns: modified copy of advancement_probs
    """
    if runner_speeds is None:
        return base_probs

    adjusted = {}
    for key, outcomes in base_probs.items():
        config_name, event = key
        if event != 'single':
            adjusted[key] = outcomes
            continue

        # Check if any runner on base has speed data
        has_speed_adj = False
        for base_pos, speed in runner_speeds.items():
            if speed is not None and speed != LEAGUE_AVG_SPRINT_SPEED:
                has_speed_adj = True
                break

        if not has_speed_adj:
            adjusted[key] = outcomes
            continue

        # Adjust single advancement for runner speed
        # This is a simplified model: apply speed adjustment to the
        # most common advancement scenarios
        new_outcomes = []
        for outcome in outcomes:
            new_outcome = dict(outcome)
            new_outcomes.append(new_outcome)

        # For runner on 1st singles: adjust 1st-to-3rd probability
        if config_name == 'runner_1st' and '1st' in str(runner_speeds.get('1st', '')):
            speed = runner_speeds.get('1st', LEAGUE_AVG_SPRINT_SPEED) or LEAGUE_AVG_SPRINT_SPEED
            speed_diff = speed - LEAGUE_AVG_SPRINT_SPEED
            # Shift probability from 1st&2nd to 1st&3rd
            shift = speed_diff * 0.04  # ~4% shift per ft/sec
            shift = max(-0.15, min(0.15, shift))
            new_outcomes = _shift_advancement(outcomes, shift,
                                              from_state='runners_1st_2nd',
                                              to_state='runners_1st_3rd')

        # For runner on 2nd singles: adjust scoring probability
        elif config_name == 'runner_2nd' and runner_speeds.get('2nd'):
            speed = runner_speeds.get('2nd', LEAGUE_AVG_SPRINT_SPEED) or LEAGUE_AVG_SPRINT_SPEED
            speed_diff = speed - LEAGUE_AVG_SPRINT_SPEED
            # Shift probability from staying at 3rd to scoring
            shift = speed_diff * 0.05  # ~5% shift per ft/sec
            shift = max(-0.20, min(0.20, shift))
            # Find scoring outcome (runs_scored > 0) and non-scoring
            scoring_idx = None
            non_scoring_idx = None
            for i, o in enumerate(outcomes):
                if o['runs_scored'] > 0 and scoring_idx is None:
                    scoring_idx = i
                elif o['runs_scored'] == 0 and non_scoring_idx is None:
                    non_scoring_idx = i
            if scoring_idx is not None and non_scoring_idx is not None:
                new_outcomes = [dict(o) for o in outcomes]
                actual_shift = min(shift, new_outcomes[non_scoring_idx]['probability'])
                actual_shift = max(actual_shift, -new_outcomes[scoring_idx]['probability'])
                new_outcomes[scoring_idx]['probability'] += actual_shift
                new_outcomes[non_scoring_idx]['probability'] -= actual_shift

        adjusted[key] = new_outcomes

    return adjusted


def _shift_advancement(outcomes, shift, from_state, to_state):
    """Shift probability mass between two advancement outcomes."""
    new_outcomes = [dict(o) for o in outcomes]
    from_idx = None
    to_idx = None
    for i, o in enumerate(new_outcomes):
        if o['result_state'] == from_state and from_idx is None:
            from_idx = i
        if o['result_state'] == to_state and to_idx is None:
            to_idx = i
    if from_idx is not None and to_idx is not None:
        actual_shift = min(shift, new_outcomes[from_idx]['probability'])
        actual_shift = max(actual_shift, -new_outcomes[to_idx]['probability'])
        new_outcomes[from_idx]['probability'] -= actual_shift
        new_outcomes[to_idx]['probability'] += actual_shift
    return new_outcomes


def _fallback_advancement(config_name: str, event: str) -> list:
    """
    Minimal fallback when advancement_probs is missing a key.
    Uses simplified deterministic runner advancement.
    """
    config = parse_base_config(config_name)
    on_1st, on_2nd, on_3rd = _runners_on(config)

    if event == 'single':
        # Batter to 1st, each runner advances 1 base, runner from 3rd scores
        runs = int(on_3rd)
        new_3rd = on_2nd
        new_2nd = on_1st
        new_1st = True
        res = _config_from_runners(new_1st, new_2nd, new_3rd)
        return [{'result_state': base_config_name(res), 'runs_scored': runs, 'probability': 1.0}]
    elif event == 'double':
        # Batter to 2nd, runners on 2nd/3rd score, runner on 1st to 3rd
        runs = int(on_2nd) + int(on_3rd)
        new_3rd = on_1st
        new_2nd = True
        new_1st = False
        res = _config_from_runners(new_1st, new_2nd, new_3rd)
        return [{'result_state': base_config_name(res), 'runs_scored': runs, 'probability': 1.0}]
    elif event == 'triple':
        # Batter to 3rd, all runners score
        runs = int(on_1st) + int(on_2nd) + int(on_3rd)
        res = parse_base_config('runner_3rd')
        return [{'result_state': 'runner_3rd', 'runs_scored': runs, 'probability': 1.0}]
    return []
