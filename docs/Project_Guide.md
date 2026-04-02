# NRFI Edge System — Complete Build Guide

## Where you are now
- ✅ Supabase project created (Small compute)
- ✅ Project scaffolded on MacBook (nrfi-edge repo on GitHub)
- ✅ Python venv with dependencies installed
- ✅ .env file with Supabase credentials
- ✅ Database schema migrated (14 tables)
- ⬜ Reference data seeding (next step)

---

## PHASE 1: Foundation (No API keys needed)

### Step 1.1 — Seed reference data ⬅️ YOU ARE HERE
**What:** Insert 30 teams, 30 parks, and league averages (2019–2025) into Supabase.
**Preliminary:** None — Supabase credentials are enough.
**Claude Code prompt:** Already provided to you. Run it.
**Verify:** 30 teams, 30 parks, 7 league_averages rows in the database.

### Step 1.2 — Seed baserunner advancement probabilities
**What:** Populate the baserunner_advancement table with historical probabilities for runner advancement on each hit type from each base-out state. This is the lookup table the Markov chain uses.
**Preliminary:** None — this uses hardcoded historical averages from Retrosheet research.
**Claude Code prompt:** Will be provided after 1.1.
**Verify:** ~60–80 rows covering all meaningful base-out-state + event-type combinations.

### Step 1.3 — Seed historical game data (2019–2025)
**What:** Pull every regular season game from 2019–2025 using the MLB Stats API. For each game, store the game_pk, date, teams, starting pitchers, park, whether a run scored in the first inning, and the first-inning linescore.
**Preliminary:** None — MLB Stats API is free, no key needed.
**Claude Code prompt:** Will be provided after 1.2.
**Time estimate:** 15–30 minutes (API rate limits on ~15,000 games).
**Verify:** ~15,000 rows in the games table. Spot-check 5 games against Baseball Reference to confirm nrfi_result accuracy.

### Step 1.4 — Seed historical player stats
**What:** Use pybaseball to pull season-level pitching and batting stats for every MLB player from 2019–2025. Populate pitcher_stats, batter_stats, and players tables.
**Preliminary:** pybaseball is already installed. It scrapes FanGraphs and Baseball Savant — no API key needed, but it's rate-limited.
**Claude Code prompt:** Will be provided after 1.3.
**Time estimate:** 30–60 minutes per season. ~4–6 hours total for 7 seasons. Can be run overnight.
**Verify:** ~1,200 pitcher-season rows, ~3,500 batter-season rows. Spot-check a known player's stats against FanGraphs.

### Step 1.5 — Seed platoon splits
**What:** Pull batter and pitcher platoon splits (vs LHP/RHP, vs LHB/RHB) from FanGraphs via pybaseball.
**Preliminary:** Same as 1.4.
**Claude Code prompt:** Will be provided after 1.4.
**Time estimate:** 2–4 hours (one pull per season per split type).
**Verify:** ~8,000–12,000 rows in platoon_splits.

### Step 1.6 — Seed first-inning-specific pitcher data
**What:** For each pitcher-season, compute first-inning stats (runs, hits, walks, K, HR, HBP, batters faced, pitches) by parsing play-by-play data from the MLB Stats API or Statcast.
**Preliminary:** Games table must be populated (step 1.3).
**Claude Code prompt:** Will be provided after 1.5.
**Time estimate:** 2–4 hours.
**Verify:** first_inn_starts, first_inn_scoreless, etc. populated for every pitcher-season row. Spot-check against known NRFI rates (e.g., Garrett Crochet 2024 should have a very high NRFI rate).

---

## PHASE 2: Core Engine (No API keys needed)

### Step 2.1 — Build the Odds Ratio module
**What:** Implement the Tango Odds Ratio method in Python. Given batter rates, pitcher rates, and league averages, compute matchup-specific probabilities for each outcome type (K, BB, HBP, 1B, 2B, 3B, HR).
**Preliminary:** league_averages table must be populated.
**Claude Code prompt:** Will be provided.
**Verify:** Unit tests comparing output against manual calculations from Tango's published examples.

### Step 2.2 — Build the Markov chain engine
**What:** Implement the 26-state absorbing Markov chain that computes P(0 runs) for a half-inning given a specific lineup of 3-4 batters versus a specific pitcher.
**Preliminary:** Odds Ratio module (2.1) and baserunner_advancement table must be ready.
**How it works:**
  1. Takes a lineup (list of batter IDs) and a pitcher ID
  2. For each batter, computes matchup probabilities using Odds Ratio
  3. Builds a 26×26 transition matrix reflecting those probabilities
  4. Since the lineup is sequential (batter 1, then 2, then 3...), it chains the transition matrices
  5. Computes absorption probability into the "0 runs" state
  6. Returns P(0 runs scored in this half-inning)
**Claude Code prompt:** Will be provided.
**Verify:** Test against known simple cases. A lineup of three .000 OBP batters should give P(0 runs) ≈ 1.0. A lineup of three .500 OBP batters should give P(0 runs) ≈ 0.15–0.25.

### Step 2.3 — Build the environmental adjustment module
**What:** Functions that modify the transition matrix probabilities based on park factor, temperature, wind, altitude, umpire, catcher framing, and day/night sun.
**Preliminary:** Parks table with factors must be populated.
**Key adjustments:**
  - Park HR factor: multiply HR probability by (park_hr_factor / 100)
  - Temperature: +1.5% HR probability per 10°F above 75°F
  - Wind out: +18 feet carry per 5mph → translates to ~4% HR probability increase per 5mph
  - Wind in: reverse of above
  - Umpire zone: adjust BB probability by umpire's walk_rate_impact
  - Catcher framing: adjust BB probability by catcher's framing runs (converted to rate)
  - Day game clear sky: reduce batting contact rate by 1–2% at parks with non-standard orientation
**Claude Code prompt:** Will be provided.
**Verify:** Run the engine on the same matchup with and without adjustments. Coors Field should significantly increase scoring probability vs. Oracle Park.

### Step 2.4 — Build the NRFI prediction pipeline
**What:** Orchestration function that, given a game_pk:
  1. Looks up confirmed lineups (top 3-4 batters for each team)
  2. Looks up starting pitchers
  3. Looks up park, weather, umpire
  4. Runs Markov chain for top of 1st (away batters vs home pitcher)
  5. Runs Markov chain for bottom of 1st (home batters vs away pitcher)
  6. Multiplies the two P(0 runs) values for combined P(NRFI)
  7. Stores the result in the predictions table
**Preliminary:** Steps 2.1–2.3 must be complete.
**Claude Code prompt:** Will be provided.
**Verify:** Run on 10 known historical games and compare predicted probabilities to actual outcomes.

---

## PHASE 3: Backtesting (No API keys needed)

### Step 3.1 — Run full historical backtest
**What:** Run the prediction pipeline against every regular season game from 2019–2025 where you have complete data (starting pitchers, lineups). Store predictions. This is ~15,000 games.
**Preliminary:** All Phase 1 data and Phase 2 engine must be complete.
**Time estimate:** 1–3 hours depending on machine.
**Key output:**
  - Raw P(NRFI) for every game
  - Compare against actual nrfi_result
  - Compute Brier Score, log loss, ECE (Expected Calibration Error)
  - Generate calibration plot (predicted probability bins vs actual frequency)

### Step 3.2 — Calibrate the model
**What:** Train isotonic regression on a calibration holdout set (e.g., 2019–2023 train, 2024 calibrate, 2025 test).
**Preliminary:** Backtest results from 3.1.
**Claude Code prompt:** Will be provided.
**Key output:**
  - Calibrated P(NRFI) values
  - Before/after Brier Score and ECE comparison
  - Calibration plot showing improvement

### Step 3.3 — Simulate betting performance
**What:** Using calibrated probabilities and historical NRFI lines (if available) or synthetic lines based on actual NRFI rates, simulate:
  - Flat betting all games where edge > 3%
  - Fractional Kelly (1/6) bet sizing
  - Track simulated ROI, max drawdown, win rate, CLV
**Preliminary:** Calibrated model from 3.2. Historical odds data (The Odds API historical, or synthetic).
**Key output:**
  - Total units profit/loss
  - ROI percentage
  - Max drawdown
  - Sharpe ratio of daily returns
  - Decision on whether the model has a real edge before risking money

**⚠️ CRITICAL CHECKPOINT:** If the backtest shows no consistent edge over implied odds after calibration, stop here. Refine the model before going live. Do NOT skip to Phase 4 with a model that doesn't show +EV in backtesting.

---

## PHASE 4: Live Data Pipeline (API keys needed)

### Preliminary — Obtain API keys
Before starting Phase 4, you need:
- [ ] **The Odds API key** — Sign up at the-odds-api.com (~$80/month for Pro tier)
- [ ] **Tomorrow.io API key** — Sign up at tomorrow.io/weather-api (free tier is sufficient)
- [ ] **Slack webhook URL** — Create #nrfi-picks channel, set up incoming webhook

Add all three to your .env file.

### Step 4.1 — Build the Odds API client
**What:** Python module that fetches live NRFI/YRFI odds for today's MLB games across multiple sportsbooks.
**Preliminary:** Odds API key in .env.
**Key features:**
  - Fetch odds for all today's games
  - Parse NRFI prices from each book
  - Implement power method vig removal to get true implied probability
  - Store snapshots in the odds table
  - Identify best available NRFI price across books

### Step 4.2 — Build the weather client
**What:** Python module that fetches game-time weather forecast for each outdoor stadium.
**Preliminary:** Tomorrow.io API key in .env. Parks table with lat/lon populated.
**Key features:**
  - Fetch forecast for game start time at each stadium's coordinates
  - Compute wind direction relative to field orientation
  - Classify as "out", "in", "cross_l", "cross_r", "calm"
  - Store in weather_snapshots table
  - Skip dome stadiums

### Step 4.3 — Build the lineup monitor
**What:** Python module that polls the MLB Stats API for confirmed lineups.
**Preliminary:** None — MLB API is free.
**Key features:**
  - Poll schedule endpoint for today's games
  - Detect when lineups are confirmed (usually 1–2 hours pre-game)
  - Insert confirmed lineups into the lineups table
  - Trigger prediction pipeline when lineups arrive

### Step 4.4 — Build the daily orchestrator
**What:** Main script that runs the full daily workflow:
  1. Morning (~9am ET): Pull today's schedule and probable pitchers
  2. Insert games into games table
  3. Run preliminary predictions (using projected lineups based on recent batting orders)
  4. Afternoon (rolling, ~1–2 hours before each game): Detect lineup confirmations
  5. Pull weather for confirmed games
  6. Pull latest odds
  7. Run confirmed predictions with real lineups
  8. Compare calibrated P(NRFI) against best available implied probability
  9. If edge > 3%: flag as bet recommendation
  10. Push to Slack
**Preliminary:** Steps 4.1–4.3 must be complete.

### Step 4.5 — Build the Slack alert system
**What:** Format and send bet recommendations to #nrfi-picks.
**Preliminary:** Slack webhook URL in .env.
**Alert format:**
```
🟢 NRFI PICK — NYY @ BOS — 7:10pm ET
Pitchers: Cole (NYY) vs. Whitlock (BOS)
Model: 74.2% NRFI | Best line: -125 (DraftKings) | Implied: 55.6%
Edge: 18.6% | Kelly: 1.8 units
Factors: Elite pitcher matchup, wind blowing in 12mph, 58°F
```

### Step 4.6 — Build the results tracker
**What:** After each game completes, pull the first-inning result and update:
  - games.nrfi_result
  - games.first_inn_home_runs / first_inn_away_runs
  - predictions.result
  - Compute CLV by comparing bet price to closing line
**Preliminary:** Games and predictions tables populated.
**Schedule:** Run nightly after all games complete (~1am ET).

---

## PHASE 5: Deployment (Mac Mini)

### Step 5.1 — Clone to Mac Mini
**What:** SSH into Mac Mini via Tailscale. Clone the repo, create venv, install dependencies, populate .env.
**Preliminary:** All code tested and working on MacBook.

### Step 5.2 — Set up cron schedules
**What:** Configure crontab on Mac Mini:
```
# Morning: Pull schedule and probable pitchers
0 9 * * * cd /path/to/nrfi-edge && /path/to/venv/bin/python scripts/daily_setup.py

# Every 15 min from 11am–8pm ET: Check lineups, odds, run predictions
*/15 15-24 * * * cd /path/to/nrfi-edge && /path/to/venv/bin/python scripts/lineup_monitor.py

# Nightly: Update results
0 5 * * * cd /path/to/nrfi-edge && /path/to/venv/bin/python scripts/update_results.py

# Weekly: Refresh pitcher/batter stats
0 6 * * 1 cd /path/to/nrfi-edge && /path/to/venv/bin/python scripts/refresh_stats.py
```
**Preliminary:** Tested each script manually first.

### Step 5.3 — Set up monitoring
**What:** Simple health checks to make sure the system is running:
  - Slack alert if no predictions are generated by 5pm ET on a game day
  - Slack alert if any API call fails 3 times consecutively
  - Weekly summary Slack message: total bets, win rate, units profit/loss, CLV average

---

## PHASE 6: Model Iteration (Ongoing)

### Step 6.1 — Weekly model review
Every Monday, review:
  - Last week's predictions vs results
  - Calibration drift (is the model still calibrated?)
  - CLV trend (are you consistently beating closing lines?)
  - Any pitchers or teams where the model is systematically wrong

### Step 6.2 — Monthly recalibration
Retrain isotonic regression calibration on the growing dataset of this season's predictions + outcomes.

### Step 6.3 — Feature experiments
Test adding new features one at a time and measure impact on Brier Score:
  - Catcher framing data
  - Pitcher velocity trends (Statcast recent vs season)
  - Umpire-specific zone data
  - Opener/bullpen game detection
  - Batter vs pitcher head-to-head data (only when sample > 50 PA)

### Step 6.4 — Update park factors
After each season, update run_factor and hr_factor in the parks table with new FanGraphs 3-year rolling averages.

### Step 6.5 — Pre-season refresh
Before each new season:
  - Seed new season's league_averages (initially from projections, updated after April)
  - Update player rosters and team changes
  - Re-run calibration on all available historical data
  - Test pipeline end-to-end with spring training games (no betting, just prediction accuracy)

---

## Budget Summary

| Item | Monthly Cost | Purpose |
|------|-------------|---------|
| Supabase Small | $15 | Database |
| The Odds API Pro | ~$80 | Live NRFI odds |
| Tomorrow.io | Free | Weather data |
| MLB Stats API | Free | Games, lineups, stats |
| pybaseball | Free | Historical Statcast/FanGraphs data |
| **Total** | **~$95/month** | |

### Optional upgrades (if budget allows):
| Item | Monthly Cost | Purpose |
|------|-------------|---------|
| OddsJam API | $200–500 | Fastest real-time odds, 100+ books |
| Sportradar MLB v8 | $500–2000 | Official MLB data, lowest latency |
| SportsDataIO | $100–300 | Projections, historical odds for backtesting |
| Supabase Medium | $60 | More RAM if queries slow down |

---

## Key Principles

1. **Never bet before backtesting.** Phase 3 must show positive expected value before you enter Phase 4.
2. **Calibration > accuracy.** A model that says 72% and hits 72% of the time is more valuable than one that says 80% and hits 75%.
3. **Track CLV religiously.** Closing Line Value is the single best predictor of long-term profitability. If you're consistently beating the closing line, the profits will follow.
4. **1/6 Kelly, capped at 2% bankroll.** Never overbet. NRFI is inherently volatile — one bad pitch erases any edge.
5. **3% minimum edge threshold.** Don't bet games where your model edge is marginal. Vig, model uncertainty, and execution slippage eat thin edges.
6. **Update weekly, recalibrate monthly.** The model should evolve with the season, not stay static.
