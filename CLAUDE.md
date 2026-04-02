# NRFI Edge System

MLB NRFI (No Run First Inning) betting model that uses a 26-state absorbing Markov chain to compute the probability of a scoreless first inning, then identifies +EV bets by comparing model output against sportsbook implied odds.

## Architecture
- **Markov chain engine**: 24 transient states (8 baserunner configs × 3 outs) + 2 absorbing states (0 runs / ≥1 run). Computes P(0 runs) per half-inning.
- **Odds Ratio method**: Combines batter and pitcher outcome rates using Tango's formula: `Odds(matchup) = Odds(batter) × Odds(pitcher) / Odds(league)`. Applied independently per outcome type (K, BB, HBP, 1B, 2B, 3B, HR).
- **Marcel shrinkage**: `r = PA / (PA + 1200)`. Regresses observed rates toward league mean. Multi-year weighting: 5/4/3.
- **First-inning adjustments**: Data-driven multipliers on matchup rates before Markov chain. HR/hits +12%, HBP -13%, K -1%, BB unchanged. Derived from 2019-2025 first-inning vs season-long rate comparison.
- **Environmental adjustments**: Modify transition probabilities (not separate linear terms). Park HR factor, temperature, wind, umpire zone, catcher framing.
- **Calibration**: Isotonic regression (not Platt scaling). Evaluate with ECE, Brier Score, calibration plot.
- **Betting**: Power method vig removal. 1/6 fractional Kelly capped at 2% bankroll. Minimum 3% edge to bet. Track CLV.

## Current Status
*Last updated: 2026-04-02*

### Phase 1: Data Foundation
- [x] Supabase schema (14 tables) migrated (Step 1.1)
- [x] Reference data seeded — teams, parks, league_averages, baserunner_advancement (Step 1.2)
- [x] Historical games 2019-2025 — 15,431 games seeded (Step 1.3)
- [x] Historical player stats — 1,770 pitcher-seasons, 3,519 batter-seasons (Step 1.4)
- [x] Platoon splits — 10,479 rows seeded across 2019-2025 (Step 1.5)
- [x] First-inning pitcher stats — 2,547 pitcher-season rows across 2019-2025 (Step 1.6)
- [x] Historical lineups — 277,758 rows seeded from boxscore API (Step 1.7)

### Phase 2: Core Engine
- [x] Odds Ratio module — code built, tests pass (Step 2.1)
- [x] Markov chain engine — code built with productive outs + GIDP, tests pass (Step 2.2)
- [x] Environmental adjustments — code built, tests pass (Step 2.3)
- [x] First-inning rate adjustments — data-driven multipliers applied (Step 2.5)
- [x] Prediction pipeline — code built, tests pass (Step 2.4)

### Phase 3: Backtesting
- [x] Backtest v0.2.0 — 15,409 games predicted (99.9% coverage)
- [x] First-inning adjustments reduced raw bias from +5.4% to -0.3%
- [x] Isotonic calibrator trained on 2019-2024, tested on 2025

**Backtest Results (v0.2.0-first-inning-adj):**

| Metric | v0.1.0 (old) | v0.2.0 (new) | Change |
|--------|-------------|-------------|--------|
| Mean Prediction (actual: 0.503) | 0.5572 | 0.5000 | -0.057 (bias eliminated) |
| Brier Score (all) | 0.2531 | 0.2505 | -0.003 (improved) |
| Brier Skill (all) | -0.0124 | -0.0021 | +0.010 (5.9x closer to positive) |
| ECE (all) | 0.0591 | 0.0329 | -0.026 (44% less miscalibration) |
| Prediction std | 0.0834 | 0.0884 | +0.005 (wider spread) |
| 2025 Calibrated Brier | 0.2487 | 0.2488 | ~same |
| 2025 Calibrated ECE | 0.0053 | 0.0082 | ~same |
| 2025 Calibrated Brier Skill | +0.0053 | +0.0047 | ~same |

**High-Confidence (calibrated, all games):**
- P(NRFI) > 0.54: 4,034 games → actual 56.6%
- P(NRFI) > 0.56: 1,609 games → actual 58.7%
- P(NRFI) > 0.58: 1,531 games → actual 58.9%
- P(NRFI) > 0.60: 134 games → actual 63.4%

### Phase 4
- [ ] Live pipeline

## Next Steps
1. Wire isotonic calibrator into live prediction pipeline (`predict.py:413` TODO)
2. Seed historical weather data and apply full adjustments in backtest
3. Apply Marcel shrinkage to platoon split rates (currently unshrunk)
4. Seed umpire data for umpire zone adjustments
5. Investigate further discrimination improvements

## Key Files
- `docs/STRATEGY.md` — Full mathematical framework, formulas, corrections, and detailed reasoning. READ THIS before building any core engine component.
- `docs/PROJECT_GUIDE.md` — Step-by-step implementation guide with Claude Code prompts.
- `src/markov/` — Markov chain engine
- `src/data/` — API clients (MLB Stats, Odds, Weather)
- `src/calibration/` — Isotonic regression calibration
- `src/betting/` — Vig removal, Kelly criterion, edge calculation
- `scripts/` — Data seeding, backtesting, daily orchestration

## Database
Supabase Postgres. 14 tables: teams, parks, players, pitcher_stats, batter_stats, platoon_splits, umpires, league_averages, baserunner_advancement, games, lineups, odds, weather_snapshots, predictions.

Connection via .env (SUPABASE_URL, SUPABASE_SERVICE_KEY). Also configured via Supabase MCP and direct Postgres connection string.

## Critical Constraints
1. Environmental factors adjust Markov chain transition probabilities — they are NOT separate terms in a linear weighted model.
2. Use disaggregated outcome rates (K%, BB%, 1B%, etc.) not composite metrics like xwOBA for the Markov chain.
3. First-inning-specific stats from one season have reliability of only ~9%. Always use multi-year data with Marcel shrinkage.
4. Humidity is neutral (air density decrease offset by ball moisture absorption). Do not treat as offense-boosting.
5. Never use full Kelly. 1/6 fractional Kelly, capped at 2% of bankroll.
6. Phase 3 backtesting must show +EV before proceeding to live betting.
7. **NRFI base rate is ~50%, not ~70%.** Per-half-inning scoreless rate is ~71% (away ~73%, home ~69%). Full-game NRFI (both halves scoreless) is ~50%. Many published sources report the per-team figure; do not confuse with the full-game rate.

## Tech Stack
Python, Supabase (Postgres), pybaseball, MLB Stats API (free), The Odds API, Tomorrow.io, Slack webhooks. Deployed on Mac Mini via cron.
