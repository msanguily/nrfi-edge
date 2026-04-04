-- 009: Add odds_history time-series table and closing odds columns
--
-- The existing odds table keeps the LATEST snapshot per (game_pk, book)
-- for fast lookups during prediction. odds_history stores EVERY captured
-- snapshot so we can reconstruct line movement and compute CLV accurately.

-- Time-series history of every odds snapshot
CREATE TABLE IF NOT EXISTS odds_history (
    id SERIAL PRIMARY KEY,
    game_pk INTEGER REFERENCES games(game_pk) NOT NULL,
    book TEXT NOT NULL,
    nrfi_price INTEGER,
    yrfi_price INTEGER,
    nrfi_decimal NUMERIC(6,3),
    yrfi_decimal NUMERIC(6,3),
    implied_nrfi_prob NUMERIC(5,4),
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_odds_history_game
    ON odds_history(game_pk, captured_at);

CREATE INDEX IF NOT EXISTS idx_odds_history_game_book
    ON odds_history(game_pk, book, captured_at);

-- Add closing odds columns to the main odds table (latest snapshot)
ALTER TABLE odds ADD COLUMN IF NOT EXISTS opening_nrfi_price INTEGER;
ALTER TABLE odds ADD COLUMN IF NOT EXISTS closing_nrfi_price INTEGER;
ALTER TABLE odds ADD COLUMN IF NOT EXISTS closing_implied_prob NUMERIC(5,4);

-- Add columns to predictions for richer CLV tracking
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS opening_nrfi_price INTEGER;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS closing_nrfi_price INTEGER;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS closing_implied_prob NUMERIC(5,4);
