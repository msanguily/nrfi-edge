"""Tests for the MLB Stats API client."""

import pytest
import requests
from unittest.mock import patch, MagicMock

from src.data.mlb_api import (
    get_todays_games,
    get_player_info,
    get_probable_pitchers,
    get_confirmed_lineups,
    get_game_linescore,
    get_hp_umpire,
)


# ---------------------------------------------------------------------------
# Live integration tests (hit the real API)
# ---------------------------------------------------------------------------


class TestGetTodaysGames:
    def test_returns_list(self):
        """get_todays_games should return a list (may be empty if no games)."""
        result = get_todays_games()
        assert isinstance(result, list)

    def test_game_keys(self):
        """Each game dict should contain the expected keys."""
        result = get_todays_games()
        if not result:
            pytest.skip("No games scheduled today")
        expected_keys = {
            "game_pk", "game_date", "game_time_utc",
            "home_team_id", "away_team_id",
            "home_pitcher_id", "away_pitcher_id",
            "status", "is_day_game",
        }
        for game in result:
            assert expected_keys.issubset(game.keys())


class TestGetPlayerInfo:
    def test_known_player(self):
        """Mike Trout (545361) should return correct info."""
        result = get_player_info(545361)
        assert result is not None
        assert result["name"] == "Mike Trout"
        assert result["bats"] == "R"
        assert result["throws"] == "R"

    def test_nonexistent_player(self):
        """A bogus player ID should return None gracefully."""
        result = get_player_info(9999999)
        assert result is None


class TestGetProbablePitchers:
    def test_known_past_date(self):
        """A date during the 2024 season should return results."""
        result = get_probable_pitchers("2024-06-15")
        assert result is not None
        assert isinstance(result, list)
        assert len(result) > 0

    def test_each_entry_has_keys(self):
        result = get_probable_pitchers("2024-06-15")
        if not result:
            pytest.skip("No data returned")
        expected_keys = {
            "game_pk", "home_pitcher_id", "home_pitcher_name",
            "away_pitcher_id", "away_pitcher_name",
        }
        for entry in result:
            assert expected_keys.issubset(entry.keys())


# ---------------------------------------------------------------------------
# Error handling tests (mocked network failures)
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @patch("src.data.mlb_api.requests.get")
    def test_network_error_returns_none(self, mock_get):
        """All functions should return None on network failure, not crash."""
        mock_get.side_effect = ConnectionError("network down")

        assert get_todays_games() is None
        assert get_player_info(545361) is None
        assert get_probable_pitchers("2024-06-15") is None
        assert get_confirmed_lineups(12345) is None
        assert get_game_linescore(12345) is None
        assert get_hp_umpire(12345) is None

    @patch("src.data.mlb_api.time.sleep")
    @patch("src.data.mlb_api.requests.get")
    def test_http_500_returns_none(self, mock_get, mock_sleep):
        """500 errors should be retried and ultimately return None."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_get.return_value = mock_resp

        assert get_todays_games() is None

    @patch("src.data.mlb_api.time.sleep")  # skip actual delays in tests
    @patch("src.data.mlb_api.requests.get")
    def test_retries_three_times(self, mock_get, mock_sleep):
        """Should retry MAX_RETRIES times before giving up."""
        mock_get.side_effect = ConnectionError("fail")
        get_todays_games()
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2  # sleeps between attempts, not after last
