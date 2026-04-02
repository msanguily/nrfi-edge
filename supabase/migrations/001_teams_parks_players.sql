-- 001: Core entity tables: teams, parks, players

CREATE TABLE teams (
    mlb_team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    abbreviation TEXT NOT NULL,
    league TEXT,
    division TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE parks (
    park_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    mlb_team_id INTEGER REFERENCES teams(mlb_team_id),
    latitude NUMERIC(9,6),
    longitude NUMERIC(9,6),
    orientation_degrees NUMERIC(5,1),
    is_dome BOOLEAN DEFAULT FALSE,
    is_retractable_roof BOOLEAN DEFAULT FALSE,
    elevation_feet INTEGER DEFAULT 0,
    run_factor NUMERIC(5,2) DEFAULT 100,
    hr_factor NUMERIC(5,2) DEFAULT 100,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE players (
    mlb_player_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    throws TEXT,
    bats TEXT,
    position TEXT,
    current_team_id INTEGER REFERENCES teams(mlb_team_id),
    sprint_speed NUMERIC(4,1),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
