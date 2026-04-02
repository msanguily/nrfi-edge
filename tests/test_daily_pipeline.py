"""Tests for the daily pipeline scripts.

Tests schedule parsing, lineup detection, result grading, CLV, and error handling
using mocked API responses and database interactions.
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import date


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Create a mock Supabase client."""
    db = MagicMock()
    # Default: table().select().eq().execute() returns empty
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
    db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    return db


SAMPLE_SCHEDULE_RESPONSE = {
    "dates": [{
        "games": [
            {
                "gamePk": 746001,
                "gameDate": "2026-04-02T23:10:00Z",
                "gameType": "R",
                "status": {"detailedState": "Scheduled"},
                "teams": {
                    "home": {
                        "team": {"id": 121},
                        "probablePitcher": {"id": 605135, "fullName": "Max Scherzer"},
                    },
                    "away": {
                        "team": {"id": 143},
                        "probablePitcher": {"id": 547973, "fullName": "Sandy Alcantara"},
                    },
                },
                "venue": {"id": 3289},
            },
            {
                "gamePk": 746002,
                "gameDate": "2026-04-02T23:40:00Z",
                "gameType": "R",
                "status": {"detailedState": "Scheduled"},
                "teams": {
                    "home": {
                        "team": {"id": 111},
                        "probablePitcher": {},
                    },
                    "away": {
                        "team": {"id": 147},
                        "probablePitcher": {"id": 543037, "fullName": "Gerrit Cole"},
                    },
                },
                "venue": {"id": 3},
            },
            {
                "gamePk": 746003,
                "gameDate": "2026-04-02T18:00:00Z",
                "gameType": "S",  # Spring training — should be skipped
                "status": {"detailedState": "Scheduled"},
                "teams": {
                    "home": {"team": {"id": 120}, "probablePitcher": {}},
                    "away": {"team": {"id": 110}, "probablePitcher": {}},
                },
                "venue": {"id": 2500},
            },
        ]
    }]
}


# ---------------------------------------------------------------------------
# Test 1: daily_schedule parses and maps games correctly
# ---------------------------------------------------------------------------

class TestDailySchedule:
    @patch("src.data.mlb_api._request")
    def test_parses_schedule(self, mock_request):
        """get_games_for_date should parse MLB API response into structured dicts."""
        mock_request.return_value = SAMPLE_SCHEDULE_RESPONSE
        from src.data.mlb_api import get_games_for_date

        games = get_games_for_date("2026-04-02")
        assert games is not None
        assert len(games) == 3  # all 3 returned, filtering is done by the caller

        # Regular season game
        g = games[0]
        assert g["game_pk"] == 746001
        assert g["home_team_id"] == 121
        assert g["away_team_id"] == 143
        assert g["home_pitcher_id"] == 605135
        assert g["away_pitcher_id"] == 547973
        assert g["game_type"] == "regular"

        # Game with missing home pitcher
        g2 = games[1]
        assert g2["home_pitcher_id"] is None
        assert g2["away_pitcher_id"] == 543037

        # Spring training game
        g3 = games[2]
        assert g3["game_type"] == "spring"

    @patch("src.data.mlb_api._request")
    def test_api_failure_returns_none(self, mock_request):
        """Should return None when API fails."""
        mock_request.return_value = None
        from src.data.mlb_api import get_games_for_date

        result = get_games_for_date("2026-04-02")
        assert result is None


# ---------------------------------------------------------------------------
# Test 2: lineup_monitor detects new vs existing lineups
# ---------------------------------------------------------------------------

class TestLineupMonitor:
    def test_lineup_changed_empty_old(self):
        """New lineup when no existing lineup stored."""
        from scripts.lineup_monitor import lineup_changed
        assert lineup_changed([], [1, 2, 3, 4, 5, 6, 7, 8, 9]) is True

    def test_lineup_changed_same(self):
        """Same lineup should return False."""
        from scripts.lineup_monitor import lineup_changed
        old = [{"mlb_player_id": i, "batting_order": i} for i in range(1, 10)]
        new = list(range(1, 10))
        assert lineup_changed(old, new) is False

    def test_lineup_changed_different_order(self):
        """Different batting order should be detected."""
        from scripts.lineup_monitor import lineup_changed
        old = [{"mlb_player_id": i, "batting_order": i} for i in range(1, 10)]
        new = [2, 1, 3, 4, 5, 6, 7, 8, 9]  # 1 and 2 swapped
        assert lineup_changed(old, new) is True

    def test_lineup_changed_different_player(self):
        """Substituted player should be detected."""
        from scripts.lineup_monitor import lineup_changed
        old = [{"mlb_player_id": i, "batting_order": i} for i in range(1, 10)]
        new = [1, 2, 3, 4, 5, 6, 7, 8, 99]  # player 9 replaced by 99
        assert lineup_changed(old, new) is True


# ---------------------------------------------------------------------------
# Test 3: nightly_results grades NRFI/YRFI correctly
# ---------------------------------------------------------------------------

class TestNightlyResults:
    @patch("src.data.mlb_api._request")
    def test_nrfi_detection(self, mock_request):
        """0 runs in first inning = NRFI."""
        mock_request.return_value = {
            "innings": [{
                "num": 1,
                "away": {"runs": 0, "hits": 1, "errors": 0},
                "home": {"runs": 0, "hits": 0, "errors": 0},
            }]
        }
        from src.data.mlb_api import get_game_linescore

        result = get_game_linescore(746001)
        assert result is not None
        assert result["nrfi"] is True
        assert result["away_first_inning_runs"] == 0
        assert result["home_first_inning_runs"] == 0

    @patch("src.data.mlb_api._request")
    def test_yrfi_detection(self, mock_request):
        """1+ runs in first inning = YRFI."""
        mock_request.return_value = {
            "innings": [{
                "num": 1,
                "away": {"runs": 2, "hits": 3, "errors": 0},
                "home": {"runs": 0, "hits": 1, "errors": 0},
            }]
        }
        from src.data.mlb_api import get_game_linescore

        result = get_game_linescore(746001)
        assert result is not None
        assert result["nrfi"] is False
        assert result["away_first_inning_runs"] == 2

    @patch("src.data.mlb_api._request")
    def test_yrfi_home_runs(self, mock_request):
        """Home runs in bottom of first = YRFI."""
        mock_request.return_value = {
            "innings": [{
                "num": 1,
                "away": {"runs": 0, "hits": 0, "errors": 0},
                "home": {"runs": 3, "hits": 2, "errors": 0},
            }]
        }
        from src.data.mlb_api import get_game_linescore

        result = get_game_linescore(746001)
        assert result is not None
        assert result["nrfi"] is False
        assert result["home_first_inning_runs"] == 3

    @patch("src.data.mlb_api._request")
    def test_no_innings_returns_none(self, mock_request):
        """Game not started yet returns None."""
        mock_request.return_value = {"innings": []}
        from src.data.mlb_api import get_game_linescore

        result = get_game_linescore(746001)
        assert result is None


# ---------------------------------------------------------------------------
# Test 4: CLV calculation
# ---------------------------------------------------------------------------

class TestCLV:
    def test_clv_positive(self):
        """When we get better odds than closing line, CLV should be positive."""
        from src.betting.edge import american_to_decimal, decimal_to_implied

        # We bet at -110 (implied 0.5238)
        bet_dec = american_to_decimal(-110)
        bet_implied = decimal_to_implied(bet_dec)

        # Market closed at -120 (implied 0.5455)
        close_dec = american_to_decimal(-120)
        close_implied = decimal_to_implied(close_dec)

        clv = close_implied - bet_implied
        assert clv > 0, f"CLV should be positive, got {clv}"
        assert abs(clv - (0.5455 - 0.5238)) < 0.01

    def test_clv_negative(self):
        """When we get worse odds than closing line, CLV is negative."""
        from src.betting.edge import american_to_decimal, decimal_to_implied

        # We bet at -130 (implied 0.5652)
        bet_implied = decimal_to_implied(american_to_decimal(-130))
        # Market closed at -110 (implied 0.5238)
        close_implied = decimal_to_implied(american_to_decimal(-110))

        clv = close_implied - bet_implied
        assert clv < 0


# ---------------------------------------------------------------------------
# Test 5: Error handling — API failure doesn't crash the script
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @patch("src.data.mlb_api._request")
    def test_linescore_api_failure(self, mock_request):
        """API failure should return None, not crash."""
        mock_request.return_value = None
        from src.data.mlb_api import get_game_linescore

        result = get_game_linescore(999999)
        assert result is None

    @patch("src.data.mlb_api._request")
    def test_lineups_api_failure(self, mock_request):
        """API failure should return None, not crash."""
        mock_request.return_value = None
        from src.data.mlb_api import get_confirmed_lineups

        result = get_confirmed_lineups(999999)
        assert result is None

    def test_ensure_player_exists_already_exists(self, mock_db):
        """Should not insert if player already exists."""
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"mlb_player_id": 12345}]
        )
        from scripts.daily_schedule import ensure_player_exists

        result = ensure_player_exists(12345, mock_db)
        assert result is False

    @patch("scripts.daily_schedule.get_player_info")
    def test_ensure_player_inserts_new(self, mock_info, mock_db):
        """Should insert when player doesn't exist."""
        # First call: player not found
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_info.return_value = {
            "name": "Test Player",
            "throws": "R",
            "bats": "L",
            "position": "P",
            "current_team_id": 121,
        }

        from scripts.daily_schedule import ensure_player_exists
        result = ensure_player_exists(12345, mock_db)
        assert result is True


# ---------------------------------------------------------------------------
# Test 6: Shared utilities
# ---------------------------------------------------------------------------

class TestUtils:
    def test_is_mlb_season_april(self):
        """April should be in-season."""
        from scripts.utils import is_mlb_season
        assert is_mlb_season(date(2026, 4, 15)) is True

    def test_is_mlb_season_december(self):
        """December should be off-season."""
        from scripts.utils import is_mlb_season
        assert is_mlb_season(date(2026, 12, 25)) is False

    def test_is_mlb_season_march_20(self):
        """March 20 should be the start of season."""
        from scripts.utils import is_mlb_season
        assert is_mlb_season(date(2026, 3, 20)) is True

    def test_is_mlb_season_march_19(self):
        """March 19 should be before season."""
        from scripts.utils import is_mlb_season
        assert is_mlb_season(date(2026, 3, 19)) is False

    def test_is_mlb_season_november_5(self):
        """November 5 should be the last day of season."""
        from scripts.utils import is_mlb_season
        assert is_mlb_season(date(2026, 11, 5)) is True

    def test_is_mlb_season_november_6(self):
        """November 6 should be after season."""
        from scripts.utils import is_mlb_season
        assert is_mlb_season(date(2026, 11, 6)) is False
