# nrfi-edge

MLB NRFI (No Run First Inning) betting model.

Uses a Markov chain engine to estimate first-inning run probabilities from pitcher/batter matchup data, calibrated with isotonic regression and integrated with live odds via the Odds API. Surfaces +EV bets through Slack alerts using Kelly criterion sizing.

## Project Structure

- `src/markov/` — Markov chain engine for modeling half-inning state transitions
- `src/data/` — API clients and data ingestion (pybaseball, Odds API, Tomorrow.io)
- `src/models/` — Player, pitcher, and batter data models
- `src/calibration/` — Model calibration (isotonic regression)
- `src/betting/` — Odds parsing, vig removal, Kelly criterion
- `src/alerts/` — Slack notification system
- `scripts/` — One-off data seeding and backtest scripts
- `supabase/migrations/` — SQL migration files
- `tests/` — Test suite
- `config/` — Environment configuration

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in your API keys in .env
```
