-- 006: Odds, weather snapshots, and predictions

CREATE TABLE odds (
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

CREATE TABLE weather_snapshots (
    id SERIAL PRIMARY KEY,
    game_pk INTEGER REFERENCES games(game_pk) NOT NULL,
    temperature_f NUMERIC(4,1),
    humidity_pct NUMERIC(4,1),
    wind_speed_mph NUMERIC(4,1),
    wind_direction_degrees INTEGER,
    wind_relative TEXT CHECK (wind_relative IN ('out', 'in', 'cross_l', 'cross_r', 'calm')),
    cloud_cover_pct NUMERIC(4,1),
    barometric_pressure_mb NUMERIC(6,1),
    is_dome_closed BOOLEAN DEFAULT FALSE,
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE predictions (
    id SERIAL PRIMARY KEY,
    game_pk INTEGER REFERENCES games(game_pk) NOT NULL,
    prediction_type TEXT NOT NULL CHECK (prediction_type IN ('preliminary', 'confirmed')),
    model_version TEXT NOT NULL,
    p_nrfi_top NUMERIC(5,4),
    p_nrfi_bottom NUMERIC(5,4),
    p_nrfi_combined NUMERIC(5,4),
    p_nrfi_calibrated NUMERIC(5,4),
    best_book TEXT,
    best_nrfi_price INTEGER,
    implied_prob_best NUMERIC(5,4),
    edge NUMERIC(5,4),
    bet_recommended BOOLEAN DEFAULT FALSE,
    kelly_fraction NUMERIC(6,4),
    bet_size_units NUMERIC(6,3),
    result BOOLEAN,
    clv NUMERIC(5,4),
    factor_details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(game_pk, prediction_type)
);
