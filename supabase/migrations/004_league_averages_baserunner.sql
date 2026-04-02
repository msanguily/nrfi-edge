-- 004: League averages and baserunner advancement lookup

CREATE TABLE league_averages (
    season INTEGER PRIMARY KEY,
    k_rate NUMERIC(5,3),
    bb_rate NUMERIC(5,3),
    hbp_rate NUMERIC(5,4),
    single_rate NUMERIC(5,3),
    double_rate NUMERIC(5,3),
    triple_rate NUMERIC(5,4),
    hr_rate NUMERIC(5,4),
    pa INTEGER,
    runs_per_game NUMERIC(4,2),
    nrfi_pct NUMERIC(5,3)
);

CREATE TABLE baserunner_advancement (
    id SERIAL PRIMARY KEY,
    base_out_state TEXT NOT NULL,
    event_type TEXT NOT NULL,
    result_state TEXT NOT NULL,
    runs_scored INTEGER DEFAULT 0,
    probability NUMERIC(5,4) NOT NULL,
    UNIQUE(base_out_state, event_type, result_state)
);
