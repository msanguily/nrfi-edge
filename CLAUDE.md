# NRFI Edge System

MLB NRFI (No Run First Inning) betting model that uses a 26-state absorbing Markov chain to compute the probability of a scoreless first inning, then identifies +EV bets by comparing model output against sportsbook implied odds.

## Architecture
- **Markov chain engine**: 24 transient states (8 baserunner configs × 3 outs) + 2 absorbing states (0 runs / ≥1 run). Computes P(0 runs) per half-inning.
- **Odds Ratio method**: Combines batter and pitcher outcome rates using Tango's formula: `Odds(matchup) = Odds(batter) × Odds(pitcher) / Odds(league)`. Applied independently per outcome type (K, BB, HBP, 1B, 2B, 3B, HR).
- **Marcel shrinkage**: `r = PA / (PA + 1200)`. Regresses observed rates toward league mean. Multi-year weighting: 5/4/3.
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

### Phase 2: Core Engine
- [x] Odds Ratio module — code built, tests pass (Step 2.1)
- [x] Markov chain engine — code built with productive outs + GIDP, tests pass (Step 2.2)
- [x] Environmental adjustments — code built, tests pass (Step 2.3)
- [x] Prediction pipeline — code built, tests pass (Step 2.4)

### Phase 3–4
- [ ] Backtesting (Phase 3)
- [ ] Live pipeline (Phase 4)

## Next Steps
1. Update park factors with current data
2. Seed umpire data
3. Begin Phase 3 backtesting

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

## Tech Stack
Python, Supabase (Postgres), pybaseball, MLB Stats API (free), The Odds API, Tomorrow.io, Slack webhooks. Deployed on Mac Mini via cron.
