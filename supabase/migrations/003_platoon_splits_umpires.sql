-- 003: Platoon splits and umpires

CREATE TABLE platoon_splits (
    id SERIAL PRIMARY KEY,
    mlb_player_id INTEGER REFERENCES players(mlb_player_id) NOT NULL,
    season INTEGER NOT NULL,
    player_type TEXT NOT NULL CHECK (player_type IN ('batter', 'pitcher')),
    split TEXT NOT NULL CHECK (split IN ('vs_LHP', 'vs_RHP', 'vs_LHB', 'vs_RHB')),
    pa INTEGER DEFAULT 0,
    k_rate NUMERIC(5,3),
    bb_rate NUMERIC(5,3),
    hr_rate NUMERIC(5,4),
    single_rate NUMERIC(5,3),
    double_rate NUMERIC(5,3),
    triple_rate NUMERIC(5,4),
    hbp_rate NUMERIC(5,4),
    woba NUMERIC(4,3),
    xwoba NUMERIC(4,3),
    UNIQUE(mlb_player_id, season, player_type, split)
);

CREATE TABLE umpires (
    mlb_umpire_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    games_called INTEGER DEFAULT 0,
    avg_zone_size_sqin NUMERIC(6,1),
    called_strike_rate_above_avg NUMERIC(5,3),
    walk_rate_impact NUMERIC(5,3),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
