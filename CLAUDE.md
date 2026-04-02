# NRFI Edge System

MLB NRFI (No Run First Inning) betting model that uses a 26-state absorbing Markov chain to compute the probability of a scoreless first inning, then identifies +EV bets by comparing model output against sportsbook implied odds.

## Architecture
- **Markov chain engine**: 24 transient states (8 baserunner configs × 3 outs) + 2 absorbing states (0 runs / ≥1 run). Computes P(0 runs) per half-inning.
- **Odds Ratio method**: Combines batter and pitcher outcome rates using Tango's formula: `Odds(matchup) = Odds(batter) × Odds(pitcher) / Odds(league)`. Applied independently per outcome type (K, BB, HBP, 1B, 2B, 3B, HR).
- **Marcel shrinkage**: `r = PA / (PA + 1200)`. Regresses observed rates toward league mean. Multi-year weighting: 5/4/3.
- **First-inning adjustments**: Data-driven multipliers on matchup rates before Markov chain. HR/hits +12%, HBP -13%. Asymmetric by half: top of 1st K+2%/BB-1% (home pitcher advantage), bottom K-2%/BB+1% (away pitcher disadvantage). Based on FanGraphs first-inning home field research.
- **Environmental adjustments**: Modify transition probabilities (not separate linear terms). Per-hit-type park factors (1B/2B/3B/HR from FanGraphs). Temperature and wind adjust HR, doubles, and triples (at 100%/40%/30% of HR coefficient). Umpire zone, catcher framing.
- **Platoon split shrinkage**: Split rates regressed toward player's overall rate using Marcel (constant=500 PA per The Book Ch. 6). Prevents noise from small split samples.
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
- [x] Historical weather — 15,431 snapshots seeded from MLB API (100% coverage)

### Phase 2: Core Engine
- [x] Odds Ratio module — code built, tests pass (Step 2.1)
- [x] Markov chain engine — code built with productive outs + GIDP, tests pass (Step 2.2)
- [x] Environmental adjustments — code built, tests pass (Step 2.3)
- [x] First-inning rate adjustments — data-driven multipliers applied (Step 2.5)
- [x] Prediction pipeline — code built with calibrator + first-inning adj, tests pass (Step 2.4)

### Phase 3: Backtesting
- [x] Backtest v0.2.0 — first-inning adjustments reduced raw bias from +5.4% to -0.3%
- [x] Backtest v0.3.0 — weather adjustments (temperature + wind) applied to all 15,431 games
- [x] Isotonic calibrator trained on 2019-2024, tested on 2025
- [x] Backtest v0.4.0 — Tier 1 improvements: platoon shrinkage, expanded park/weather/wind, home/away asymmetry

**Backtest Results (v0.4.0 — Tier 1 improvements):**

| Metric | v0.3.0 (before) | v0.4.0 Tier 1 (after) | Change |
|--------|--------------------|--------------------|--------|
| Mean Prediction (actual: 0.503) | 0.4984 | 0.4889 | -0.010 |
| Brier Score (all) | 0.2504 | 0.2479 | **-0.0025 (improved)** |
| Brier Skill (all) | -0.0016 | **+0.0082** | **+0.0098 (major improvement)** |
| ECE (all) | 0.0328 | 0.0140 | **-0.0188 (57% better)** |
| Prediction std (raw) | 0.0891 | 0.0543 | -0.035 (tighter, less noise) |
| Calibrated std | 0.048 | 0.0535 | **+0.006 (wider usable spread)** |
| 2025 Raw Brier Skill | +0.0052 | **+0.0065** | **+0.0013 (improved)** |

**v0.4.0 Tier 1+2 changes:**
1. Platoon split shrinkage (Marcel, constant=500 PA, regress toward player overall rate)
2. Per-hit-type park factors (1B/2B/3B from FanGraphs, seeded for all 30 parks)
3. Home/away asymmetric first-inning adjustments (K +/-2%, BB +/-1%)
4. Temperature/wind adjustments expanded to doubles (40% of HR coeff) and triples (30%)
5. Per-batter GIDP fraction (pitcher GB% × batter sprint speed, range 7-15%)
6. Sprint speed data seeded from Statcast (1,225 players, avg 27.0 ft/sec)
7. Security: removed hardcoded Supabase credentials from all scripts

**High-Confidence (calibrated, all games):**
- P(NRFI) > 0.54: 2,643 games → actual 58.2%
- P(NRFI) > 0.58: 961 games → actual 61.2%
- P(NRFI) > 0.60: 155 games → actual 65.8%

**High-Confidence (2025 out-of-sample):**
- P(NRFI) > 0.54: 424 games → actual 55.9%
- P(NRFI) > 0.58: 139 games → actual 63.3%

### Phase 4: Live Pipeline & Dashboard
- [x] Odds API client (SportsGameOdds) — fetches NRFI/YRFI lines, vig removal, tests pass
- [x] Weather forecast API (Tomorrow.io) — pre-game forecasts, dome detection, tests pass
- [x] Isotonic calibrator wired into prediction pipeline (loaded from config/calibrator.json)
- [x] First-inning adjustments wired into prediction pipeline (asymmetric top/bottom)
- [x] Streamlit dashboard — today's picks, model performance, bet history
- [x] Weather seeding script for historical data (scripts/seed_weather.py)
- [ ] Daily orchestration cron job
- [ ] Slack alerts for +EV picks

## Next Steps
1. Seed umpire/catcher framing data for zone and framing adjustments (code exists, data needed)
2. Build daily orchestration pipeline (fetch lineups → weather → odds → predict → alert)
3. Consider Venn-Abers calibration upgrade (probability intervals for bet sizing)
4. Investigate stolen base modeling (post-2023 rule changes increased SB ~50%)

## Key Files
- `docs/STRATEGY.md` — Full mathematical framework, formulas, corrections, and detailed reasoning. READ THIS before building any core engine component.
- `docs/PROJECT_GUIDE.md` — Step-by-step implementation guide with Claude Code prompts.
- `src/markov/` — Markov chain engine
- `src/data/odds_api.py` — SportsGameOdds API client for NRFI/YRFI lines
- `src/data/weather_api.py` — Tomorrow.io weather forecast API client
- `src/pipeline/predict.py` — Main prediction pipeline (Odds Ratio → first-inning adj → env adj → Markov → calibrate)
- `src/calibration/` — Isotonic regression calibration
- `src/betting/` — Vig removal, Kelly criterion, edge calculation
- `dashboard/` — Streamlit dashboard (app.py, queries.py, components.py, calculations.py)
- `scripts/` — Data seeding, backtesting, daily orchestration
- `config/calibrator.json` — Trained isotonic calibrator (2019-2024)

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
Python, Supabase (Postgres), pybaseball, MLB Stats API (free), SportsGameOdds API, Tomorrow.io, Streamlit, Slack webhooks. Deployed on Mac Mini via cron.
