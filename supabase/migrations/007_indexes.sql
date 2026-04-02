-- 007: Non-redundant indexes

CREATE INDEX idx_games_date ON games(game_date);
CREATE INDEX idx_games_home_team ON games(home_team_id);
CREATE INDEX idx_games_away_team ON games(away_team_id);
CREATE INDEX idx_games_type ON games(game_type);
CREATE INDEX idx_odds_game ON odds(game_pk, captured_at);
CREATE INDEX idx_weather_game ON weather_snapshots(game_pk);
CREATE INDEX idx_lineups_game ON lineups(game_pk, team_id);
