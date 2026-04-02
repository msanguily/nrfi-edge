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

GIDP_FRACTION = 0.12  # ~12% of out-in-play with runner on 1st and < 2 outs


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
    # Batter goes to 1st; forced runners advance
    new_1st = True
    if on_1st:
        new_2nd = True
        if on_2nd:
            new_3rd = True
            if on_3rd:
                runs = 1  # bases loaded walk
            else:
                pass  # 3rd now occupied
        else:
            new_2nd = True
            new_3rd = on_3rd  # 3rd stays as is
    else:
        new_2nd = on_2nd
        new_3rd = on_3rd

    # Recalculate properly with forced-runner logic
    # A walk forces: batter to 1st, runners advance only if forced
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
            _apply_out_event(
                rates['k'], config, outs, 0,
                prob_z, prob_s,
                new_zero, new_scored,
                new_absorb_zero, new_absorb_scored,
            )
            new_absorb_zero, new_absorb_scored = (
                _apply_out_event_ret(
                    rates['k'], config, outs, 0,
                    prob_z, prob_s,
                    new_zero, new_scored,
                    new_absorb_zero, new_absorb_scored,
                )
            )

            # --- Out in play (with GIDP split) ---
            oip_rate = rates['out_in_play']
            if _can_gidp(config, outs):
                gidp_rate = oip_rate * GIDP_FRACTION
                regular_out_rate = oip_rate * (1 - GIDP_FRACTION)

                # Regular out portion
                new_absorb_zero, new_absorb_scored = (
                    _apply_out_event_ret(
                        regular_out_rate, config, outs, 0,
                        prob_z, prob_s,
                        new_zero, new_scored,
                        new_absorb_zero, new_absorb_scored,
                    )
                )

                # GIDP portion: 2 outs, remove lead runner
                gdp_config, gdp_outs = _gidp_result(config, outs)
                if gdp_outs >= 3:
                    new_absorb_zero += prob_z * gidp_rate
                    new_absorb_scored += prob_s * gidp_rate
                else:
                    dest = state_index(gdp_config, gdp_outs)
                    new_zero[dest] += prob_z * gidp_rate
                    new_scored[dest] += prob_s * gidp_rate
            else:
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


def _apply_out_event(*args):
    """Compatibility shim — use _apply_out_event_ret instead."""
    pass


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
