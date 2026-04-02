-- 005: Games and lineups

CREATE TABLE games (
    game_pk INTEGER PRIMARY KEY,
    game_date DATE NOT NULL,
    game_type TEXT DEFAULT 'regular' CHECK (game_type IN ('spring', 'regular', 'postseason', 'allstar')),
    game_time TIME,
    game_time_utc TIMESTAMPTZ,
    status TEXT DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'live', 'final', 'postponed')),
    home_team_id INTEGER REFERENCES teams(mlb_team_id),
    away_team_id INTEGER REFERENCES teams(mlb_team_id),
    home_pitcher_id INTEGER REFERENCES players(mlb_player_id),
    away_pitcher_id INTEGER REFERENCES players(mlb_player_id),
    park_id INTEGER REFERENCES parks(park_id),
    hp_umpire_id INTEGER REFERENCES umpires(mlb_umpire_id),
    is_day_game BOOLEAN,
    game_total NUMERIC(3,1),
    first_inn_home_runs INTEGER,
    first_inn_away_runs INTEGER,
    nrfi_result BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE lineups (
    id SERIAL PRIMARY KEY,
    game_pk INTEGER REFERENCES games(game_pk) NOT NULL,
    team_id INTEGER REFERENCES teams(mlb_team_id) NOT NULL,
    batting_order INTEGER NOT NULL CHECK (batting_order BETWEEN 1 AND 9),
    mlb_player_id INTEGER REFERENCES players(mlb_player_id) NOT NULL,
    confirmed_at TIMESTAMPTZ,
    UNIQUE(game_pk, team_id, batting_order)
);
