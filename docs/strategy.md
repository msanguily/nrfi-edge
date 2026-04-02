Create a CLAUDE.md file in the project root that serves as the master context document for this project. Claude Code reads this automatically at the start of every session.

This document must contain EVERYTHING needed to understand and continue building this system without any prior conversation context. Include the following sections:

## 1. Project Overview

- NRFI (No Run First Inning) betting edge system
- Uses a 26-state absorbing Markov chain to compute P(0 runs) for each half-inning
- Combines batter-vs-pitcher matchup probabilities using the Tango Odds Ratio method
- Adjusts for environmental factors (park, weather, umpire, catcher framing)
- Calibrates output probabilities using isotonic regression
- Compares calibrated probabilities against sportsbook implied odds to find +EV bets
- Uses fractional Kelly criterion for bet sizing

## 2. Mathematical Framework

### 2.1 Markov Chain (26-state absorbing chain)

- 24 transient states: 8 baserunner configurations × 3 out states
- 2 absorbing states: "3 outs, 0 runs scored" and "3 outs, ≥1 run scored"
- The 8 baserunner configs: empty, runner_1st, runner_2nd, runner_3rd, runners_1st_2nd, runners_1st_3rd, runners_2nd_3rd, loaded
- Each plate appearance is a state transition with probabilities specific to the batter-pitcher matchup
- P(NRFI for one half-inning) = probability of absorption into the "0 runs" state starting from state 1 (empty, 0 outs)
- P(NRFI for game) = P(0 runs top 1st) × P(0 runs bottom 1st)
- The two half-innings are approximately independent but share environmental factors (weather, umpire, park)

### 2.2 Tango Odds Ratio Method

This is the gold standard for combining batter and pitcher rates, created by Tom Tango (Senior Database Architect at MLB Advanced Media, inventor of wOBA and FIP).

Formula: Given batter rate b, pitcher rate p, league average rate L:

```
Odds(x) = x / (1 - x)

Odds(matchup) = Odds(b) × Odds(p) / Odds(L)

P(matchup) = Odds(matchup) / (1 + Odds(matchup))
```

Worked example: Batter with .400 OBP in a .300 league facing a pitcher allowing .250 OBP:

```
Odds(matchup) = (.400/.600) × (.250/.750) / (.300/.700)
             = 0.667 × 0.333 / 0.429
             = 0.518
P(matchup) = 0.518 / 1.518 = .341
```

Apply this independently for each outcome type: K, BB, HBP, 1B, 2B, 3B, HR.
After computing all matchup rates, the residual probability is "out in play" (groundout, flyout, lineout).

For rates far from .500 (like HR rate at ~.030), use the log-odds formulation for numerical stability:

```
log_odds(x) = ln(x / (1-x))
log_odds(matchup) = log_odds(b) + log_odds(p) - log_odds(L)
P(matchup) = 1 / (1 + exp(-log_odds(matchup)))
```

### 2.3 Marcel Shrinkage (Regression Toward the Mean)

Raw observed rates are noisy, especially with small samples. Tom Tango's Marcel system provides the standard approach.

Reliability formula:

```
r = PA / (PA + 1200)    [for batters]
r = BF / (BF + 1200)    [for pitchers, BF = batters faced]

adjusted_rate = r × observed_rate + (1 - r) × league_rate
```

For multi-year estimates, weight seasons 5/4/3 (most recent = 5):

```
weighted_rate = (5 × rate_year1 + 4 × rate_year2 + 3 × rate_year3) / (5 + 4 + 3)
weighted_PA = 5 × PA_year1 + 4 × PA_year2 + 3 × PA_year3
r = weighted_PA / (weighted_PA + 1200)
```

CRITICAL INSIGHT: For first-inning-specific stats, a pitcher has ~30 starts/season = ~120 batters faced in the first inning. That gives r = 120/(120+1200) = 0.091, meaning 91% of the estimate comes from the league average. Single-season first-inning ERA is almost pure noise. Always use multi-year data or full-season outcome rates with the Odds Ratio method rather than first-inning-specific rates alone.

### 2.4 Baserunner Advancement

The Markov chain needs to know what happens to existing runners on each hit type. Probabilities are stored in the baserunner_advancement table. Key examples:

- Single with runner on 1st: 68% runners on 1st & 2nd, 29% runners on 1st & 3rd, 3% run scores
- Single with runner on 2nd: 60% run scores (runner to 1st), 40% runners on 1st & 3rd
- These are league averages from Retrosheet. Adjust for runner sprint speed when available.

### 2.5 GIDP (Ground Into Double Play)

With runner on 1st and less than 2 outs, there's a ~10-12% probability of a double play (outs +2, runner removed). Adjust based on:

- Pitcher GB rate (ground ball %, from Statcast)
- Infield defense quality (OAA at SS and 2B)
- Batter sprint speed (fast runners avoid GIDP more)

## 3. Environmental Adjustments

These modify the transition probabilities BEFORE they enter the Markov chain. They are NOT separate terms in a linear model.

### 3.1 Park Factor

- Multiply HR probability by (park_hr_factor / 100) for each batter
- Coors Field (factor ~115) increases HR prob by 15%
- Oracle Park (factor ~85) decreases HR prob by 15%
- Source: FanGraphs 3-year rolling park factors

### 3.2 Temperature

- +3 feet of fly ball carry per 10°F above 75°F (from physicist Alan Nathan's research)
- Translates to approximately +1.5% HR probability per 10°F above 75°F
- Cold April games in northern parks significantly suppress HR probability
- Source: Published research by Dr. Alan Nathan, University of Illinois

### 3.3 Wind

- Wind blowing out: +18 feet carry per 5mph → approximately +4% HR probability per 5mph
- Wind blowing in: reverse effect
- Cross winds: minimal impact on HR, slight effect on fly ball catches
- Wind direction must be computed relative to park orientation (stored in parks table)
- Source: Dr. Alan Nathan research, FanGraphs analysis

### 3.4 Humidity — CORRECTED

Original assumption was wrong. The physics and baseball reality:

- PHYSICS: Humid air IS less dense than dry air (water molecules lighter than N₂/O₂), so balls travel slightly farther
- BASEBALL: Baseballs absorb moisture, becoming heavier and softer, reducing exit velocity off the bat
- NET EFFECT: These roughly cancel. Treat humidity as neutral in the model
- MLB uses humidors at Coors and Chase Field specifically to add moisture to balls and reduce home runs
- Source: MLB humidor data, Dr. Alan Nathan, weatherology.com

### 3.5 Altitude

- Already captured by park factor (Coors at 5,200ft is the extreme outlier)
- Reduced air density = less drag = farther fly balls, but also reduced Magnus force
- Net effect at Coors: ~5% farther fly ball distance vs sea level
- Source: Dr. Alan Nathan, baseball.physics.illinois.edu

### 3.6 Umpire Strike Zone

- Umpires with larger zones → fewer walks → favors NRFI
- Adjust BB probability by umpire's historical walk_rate_impact (deviation from league average)
- Apply heavy shrinkage — umpire zone data has small sample sizes and drifts over time
- Source: Baseball Savant, UmpScorecards.com

### 3.7 Catcher Framing

- Elite framers effectively expand the strike zone by 1-2 inches
- Reduces called ball rate → reduces walk rate → favors NRFI
- Adjust BB probability by catcher's framing runs above average (from Baseball Savant)
- Source: Baseball Savant catcher framing leaderboard

### 3.8 Day Game / Sun Angle

- Clear-sky daytime games show reduced offensive production vs cloudy/night games
- The effect is because sun glare affects batter visibility and fielder tracking
- Away teams are more affected (not accustomed to park's light patterns)
- Effect is small (1-2% reduction in contact rates) and only applies to outdoor day games
- Parks with non-standard orientation (deviating from Rule 1.04's NE recommendation) are more affected
- Source: Weather, Climate, and Society journal (2011 study of 35,000+ games)

### 3.9 Season Phase

- April NRFI rates run 3-5% higher than mid-season (pitchers fresh, hitters still finding timing, cold weather in northern parks)
- Apply a seasonal adjustment factor, calibrated from historical data

## 4. Features That Matter (Ranked by Signal Strength)

Based on research and existing model literature:

1. **Starting pitcher quality** (K rate, BB rate, HR rate allowed, FIP) — strongest signal but mostly priced into the line
2. **Game total (over/under)** — composite signal that captures pitcher + lineup + park. A 6.5 total game has dramatically different NRFI probability than a 10 total game. CRITICAL: this is what the books use, so your edge must come from factors BEYOND what the total captures
3. **Top-of-lineup batter quality** (OBP, xwOBA, platoon splits for the specific 3-4 batters who will bat)
4. **Platoon matchups** — LHB vs RHP, RHB vs LHP in the first 3 batters. The first inning is where platoon matchups matter most because lineup order is known
5. **Park factor** — persistent, well-measured, partially priced in
6. **Wind direction and speed** — underpriced by books, especially at Wrigley and other exposed parks
7. **Temperature** — underpriced early/late season
8. **Sprint speed of leadoff hitter** — fast leadoff men create runs from singles + stolen bases, not just HRs
9. **Pitcher workload** — cumulative pitch count over last 5-10 starts has small but measurable effect on ERA (+0.007 ERA per pitch in preceding game)
10. **Catcher framing** — small edge, rarely priced in
11. **Umpire zone** — very small edge with high noise
12. **Opener detection** — bullpen games/opener games change the calculus entirely

## 5. Calibration Strategy

### Why Calibrate

The Markov chain produces raw probabilities that may be systematically biased (e.g., consistently 2% too high). Calibration corrects this so that when the model says 72%, NRFI actually hits ~72% of the time.

### Method: Isotonic Regression (not Platt Scaling)

- Platt scaling assumes sigmoidal distortion — no reason to believe the Markov chain has this pattern
- Isotonic regression is non-parametric, corrects any monotonic distortion
- We have 12,000+ data points from backtesting, which is plenty for isotonic regression
- Use scikit-learn's IsotonicRegression with out_of_bounds='clip'
- Train on held-out calibration set (e.g., 2019-2023 train, 2024 calibrate, 2025 test)

### Evaluation Metrics

- **ECE (Expected Calibration Error)**: Primary metric. Measures average gap between predicted probability and actual outcome frequency across bins. Lower is better.

```
  ECE = Σ (|B_k| / n) × |accuracy(B_k) - confidence(B_k)|
```

- **Brier Score**: Mean squared error between predicted probabilities and actual outcomes. Lower is better.

```
  Brier = (1/n) × Σ (predicted_i - actual_i)²
```

- **Log Loss**: Penalizes confident wrong predictions severely. Standard for probabilistic models.
- **Calibration Plot**: Bin predictions into deciles, plot predicted vs actual. Should fall on the 45-degree line.

## 6. Betting Math

### 6.1 Vig Removal — Power Method

The power method is the most accurate way to remove the vig from a two-outcome market.

Given decimal odds d_NRFI and d_YRFI:

```
Find z such that: (1/d_NRFI)^z + (1/d_YRFI)^z = 1
P_true_NRFI = (1/d_NRFI)^z
```

z is solved iteratively (e.g., scipy.optimize.brentq). This distributes the overround proportionally rather than evenly.

Example: NRFI -135 (decimal 1.741), YRFI +115 (decimal 2.15):

- Simple method: NRFI implied ≈ 55.3%
- Power method: NRFI implied ≈ 55.8%
- The difference compounds over hundreds of bets

### 6.2 Edge Calculation

```
edge = calibrated_model_probability - true_implied_probability_from_best_book
```

Only bet when edge > 0.03 (3%). Below this, model uncertainty and execution slippage eat the edge.

### 6.3 Kelly Criterion

Full Kelly formula for a bet at decimal odds d with model probability p:

```
f* = (p × d - 1) / (d - 1)
```

CRITICAL: Never use full Kelly. Full Kelly assumes perfect probability estimates, which we never have. Use fractional Kelly at 1/6 of full Kelly, capped at 2% of bankroll per bet.

Example: Model says 72% NRFI, best line is -125 (decimal 1.80):

```
Full Kelly = (0.72 × 1.80 - 1) / (1.80 - 1) = (1.296 - 1) / 0.80 = 0.370 (37% of bankroll!)
1/6 Kelly = 0.370 / 6 = 0.062 (6.2% — still aggressive)
Capped at 2% = bet 2% of bankroll
```

### 6.4 Closing Line Value (CLV)

CLV is the single best predictor of long-term profitability. Track it for every bet.

```
CLV = closing_implied_probability - opening_implied_probability_when_you_bet
```

If you consistently bet at -120 and the line closes at -135, the market is confirming your edge. If you bet at -120 and it closes at -110, the market moved against you — your model may be miscalibrated.

## 7. Errors We Caught and Corrected

These corrections are critical context for anyone continuing this build:

1. **Humidity effect was wrong**: Originally said humid air helps balls fly farther. True for air density, but baseballs absorb moisture and lose exit velocity. Net effect is neutral. Treat humidity as neutral in the model.

2. **Factor weights were arbitrary**: Originally assigned 35%/25%/15% etc. Wrong approach entirely. Environmental factors should modify transition probabilities within the Markov chain, not be added as linear terms.

3. **Odds Ratio formula was wrong**: Initially provided an incorrect formula. The correct formula is Tango's: Odds(matchup) = Odds(batter) × Odds(pitcher) / Odds(league).

4. **Shrinkage constant was wrong**: Said 50-100 PA. Marcel uses 1,200 PA. First-inning specific stats from one season give reliability of only 9% — nearly useless alone.

5. **Monte Carlo was the wrong approach**: Replaced with absorbing Markov chain, which gives exact deterministic results without simulation variance.

6. **Markov state space was incomplete**: Need 26 states (24 transient + 2 absorbing that distinguish 0 runs vs ≥1 run), not 25.

7. **xwOBA as primary metric was wrong for this use case**: xwOBA is a composite that hides the outcome distribution. The Markov chain needs disaggregated rates (K%, BB%, 1B%, 2B%, 3B%, HR%, HBP%).

8. **Platt scaling was wrong for calibration**: Isotonic regression is better because the Markov chain's distortion pattern is unlikely to be sigmoidal.

9. **Missing game total as a feature**: The sportsbook's over/under total is one of the strongest public NRFI indicators and was completely omitted initially.

10. **Missing sprint speed**: Leadoff hitters with elite speed create first-inning runs through infield hits and stolen bases, not just extra-base hits.

11. **Missing pitcher workload**: Cumulative pitch count over recent starts has a small but measurable effect on performance (+0.007 ERA per pitch in preceding game).

12. **Missing GIDP probability**: Double plays erase baserunners — a critical NRFI-favorable event that depends on pitcher GB rate and infield defense.

13. **Pitcher stats table was missing outcome rates**: Originally only had K%, BB%, HR% — missing single_rate, double_rate, triple_rate, hbp_rate needed for Odds Ratio.

14. **Kelly Criterion was oversimplified**: Must use fractional Kelly (1/6) capped at 2% to avoid ruin from model uncertainty.

## 8. Database Schema Summary

14 tables in Supabase:

- **teams** (30 rows) — MLB team reference data
- **parks** (30 rows) — Stadium location, orientation, dome status, elevation, park factors
- **players** — All MLB players (inserted dynamically)
- **pitcher_stats** — Season-level pitching stats with first-inning-specific columns
- **batter_stats** — Season-level batting stats with all outcome rates
- **platoon_splits** — Batter/pitcher performance by opponent handedness
- **umpires** — Home plate umpire zone tendencies
- **league_averages** (7 rows, 2019-2025) — League-wide rates for Odds Ratio denominators
- **baserunner_advancement** (~75 rows) — Lookup table for Markov chain transitions
- **games** — Every regular season game 2019-2025 with first-inning results
- **lineups** — Confirmed batting orders per game
- **odds** — NRFI/YRFI price snapshots from sportsbooks
- **weather_snapshots** — Game-time weather at each stadium
- **predictions** — Model output with calibrated probabilities, edge, and bet recommendations

## 9. Implementation Phases

### Phase 1: Foundation (No API keys needed) — CURRENT PHASE

- Seed reference data (teams, parks, league averages, baserunner advancement) ✅ DONE
- Seed historical games 2019-2025 from MLB Stats API ⬅️ IN PROGRESS
- Seed historical player stats from pybaseball
- Seed platoon splits
- Compute first-inning-specific pitcher stats

### Phase 2: Core Engine (No API keys needed)

- Build Odds Ratio module with unit tests
- Build 26-state Markov chain engine
- Build environmental adjustment module
- Build NRFI prediction pipeline (orchestrates everything)

### Phase 3: Backtesting (No API keys needed) — CRITICAL GATE

- Run predictions on all 15,000+ historical games
- Evaluate with Brier Score, ECE, calibration plot
- Train isotonic regression calibrator
- Simulate betting performance with historical lines
- ⚠️ DO NOT proceed to Phase 4 unless backtesting shows consistent +EV

### Phase 4: Live Data Pipeline (API keys needed)

- The Odds API client (live NRFI lines) — ~$80/month
- Tomorrow.io weather client — free tier
- MLB Stats API lineup monitor — free
- Daily orchestrator script
- Slack alerts for bet recommendations

### Phase 5: Deployment

- Clone to Mac Mini via Tailscale
- Set up cron schedules
- Health monitoring via Slack

### Phase 6: Ongoing Iteration

- Weekly model review
- Monthly recalibration
- Feature experiments
- Pre-season refresh

## 10. Key Principles

1. **Never bet before backtesting.** Phase 3 must show +EV before going live.
2. **Calibration > accuracy.** A model saying 72% that hits 72% is more valuable than one saying 80% that hits 75%.
3. **Track CLV religiously.** Closing Line Value is the #1 predictor of long-term profit.
4. **1/6 Kelly, capped at 2%.** Never overbet. One pitch ruins any NRFI.
5. **3% minimum edge.** Don't bet thin edges — vig and model uncertainty eat them.
6. **The edge comes from combining factors the books underweight.** Park-adjusted weather, first-inning-specific platoon matchups, catcher framing, sprint speed.
7. **Disaggregate everything.** The Markov chain needs individual outcome rates, not composite metrics.
8. **Shrink aggressively.** Small samples are the enemy. Marcel regression with 1,200 PA constant.
9. **Environmental factors modify probabilities, not scores.** They adjust the Markov chain inputs, not a linear model.
10. **Update weekly, recalibrate monthly.** The model evolves with the season.

## 11. Tech Stack

- **Database**: Supabase (Postgres) — Small compute, ~$15/month
- **Engine**: Python (numpy, scipy for linear algebra, scikit-learn for calibration)
- **Data**: pybaseball (free), MLB Stats API (free), The Odds API (~$80/month), Tomorrow.io (free)
- **Alerts**: Slack incoming webhook
- **Deployment**: Mac Mini via Tailscale, cron jobs
- **Version Control**: GitHub (nrfi-edge repo)

## 12. File Structure

```
nrfi-edge/
├── CLAUDE.md              ← This file (auto-read by Claude Code)
├── README.md
├── requirements.txt
├── .env                   ← API keys (gitignored)
├── .env.example
├── src/
│   ├── markov/            ← 26-state Markov chain engine
│   ├── data/              ← API clients (MLB, Odds, Weather)
│   ├── models/            ← Player/pitcher/batter data models
│   ├── calibration/       ← Isotonic regression calibration
│   ├── betting/           ← Vig removal, Kelly criterion, edge calc
│   └── alerts/            ← Slack notifications
├── scripts/               ← Data seeding, backtesting, daily jobs
├── supabase/
│   └── migrations/        ← SQL migration files
├── tests/
└── config/
```

</parameter>
<parameter name="path">/mnt/user-data/outputs/CLAUDE.md</parameter>
