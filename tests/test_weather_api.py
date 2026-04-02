"""Tests for src/data/weather_api.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.data.weather_api import (
    batch_fetch_weather,
    classify_wind_direction,
    fetch_game_weather,
    get_game_weather_for_prediction,
)


# ── classify_wind_direction ─────────────────────────────────────────

class TestClassifyWindDirection:
    """Wind classification relative to outfield orientation."""

    def test_wind_from_south_at_wrigley_is_cross(self):
        """Wind from S (180°) at Wrigley (65° ENE).
        Wind toward = 0° (N). Angle diff from 65° = 65° → cross."""
        assert classify_wind_direction(180, 65) == "cross"

    def test_wind_from_west_at_wrigley_is_out(self):
        """Wind from W (270°) at Wrigley (65° ENE).
        Wind toward = 90° (E). Angle diff from 65° = 25° → out."""
        assert classify_wind_direction(270, 65) == "out"

    def test_wind_from_east_at_wrigley_is_in(self):
        """Wind from E (90°) at Wrigley (65° ENE).
        Wind toward = 270° (W). Angle diff from 65° = 155° → in."""
        assert classify_wind_direction(90, 65) == "in"

    def test_exact_out(self):
        """Wind blowing exactly toward outfield."""
        # Outfield at 180° (S). Wind from 0° (N) → toward 180° → diff 0 → out
        assert classify_wind_direction(0, 180) == "out"

    def test_exact_in(self):
        """Wind blowing exactly toward home plate."""
        # Outfield at 180° (S). Wind from 180° (S) → toward 0° (N) → diff 180 → in
        assert classify_wind_direction(180, 180) == "in"

    def test_boundary_45_is_out(self):
        """At exactly 45° difference, should be out."""
        # Outfield 0°, wind toward 45° → diff 45 → out
        assert classify_wind_direction(180 + 45, 0) == "out"
        # which is wind_from=225, toward=45, outfield=0, diff=45
        assert classify_wind_direction(225, 0) == "out"

    def test_boundary_135_is_in(self):
        """At exactly 135° difference, should be in."""
        # Outfield 0°, wind toward 135° → diff 135 → in
        assert classify_wind_direction(315, 0) == "in"

    def test_wraparound(self):
        """Test angle wraparound across 0°/360°."""
        # Outfield at 350° (just west of N). Wind from 170° → toward 350° → diff 0 → out
        assert classify_wind_direction(170, 350) == "out"


# ── fetch_game_weather ──────────────────────────────────────────────

class TestFetchGameWeather:
    """Mock Open-Meteo responses."""

    @patch("src.data.weather_api.requests.get")
    def test_parses_weather_response(self, mock_get):
        """Verify correct parsing of temperature and wind from Open-Meteo."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "utc_offset_seconds": -18000,  # CDT (UTC-5)
            "hourly": {
                "time": [
                    "2026-04-02T18:00",
                    "2026-04-02T19:00",
                    "2026-04-02T20:00",
                ],
                "temperature_2m": [68.2, 71.5, 69.8],
                "wind_speed_10m": [12.3, 14.7, 11.1],
                "wind_direction_10m": [225.0, 230.0, 220.0],
            },
        }
        mock_get.return_value = mock_resp

        # Game at 2026-04-03T00:10:00Z = 2026-04-02T19:10 CDT → closest to 19:00
        result = fetch_game_weather(41.948, -87.655, "2026-04-03T00:10:00Z")

        assert result is not None
        assert result["temp_f"] == 72  # rounded 71.5
        assert result["wind_speed_mph"] == 14.7
        assert result["wind_direction_deg"] == 230.0

    @patch("src.data.weather_api.requests.get")
    def test_returns_none_on_api_failure(self, mock_get):
        """API error should return None, not raise."""
        mock_get.side_effect = Exception("Network error")
        result = fetch_game_weather(41.948, -87.655, "2026-04-03T00:10:00Z")
        assert result is None


# ── get_game_weather_for_prediction ─────────────────────────────────

class TestGetGameWeatherForPrediction:
    """Integration-level tests with mocked Supabase and API."""

    def _make_supabase_mock(self, game_data, park_data):
        """Build a mock supabase client returning game and park data."""
        client = MagicMock()

        game_chain = MagicMock()
        game_chain.data = game_data
        client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = game_chain

        # Second call (park) needs a different chain
        def table_side_effect(name):
            mock_table = MagicMock()
            if name == "games":
                chain = MagicMock()
                chain.data = game_data
                mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = chain
            elif name == "parks":
                chain = MagicMock()
                chain.data = park_data
                mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = chain
            return mock_table

        client.table.side_effect = table_side_effect
        return client

    def test_dome_game_returns_none(self):
        """Tropicana Field (dome) should return None."""
        client = self._make_supabase_mock(
            game_data=[{"game_pk": 1, "game_time_utc": "2026-04-02T23:10:00+00:00", "park_id": 27}],
            park_data=[{
                "latitude": "27.7682", "longitude": "-82.6534",
                "is_dome": True, "is_retractable_roof": False,
                "orientation_degrees": "315",
            }],
        )
        result = get_game_weather_for_prediction(1, client)
        assert result is None

    @patch("src.data.weather_api.fetch_game_weather")
    def test_retractable_roof_returns_temp_only(self, mock_fetch):
        """Retractable roof parks return temp but no wind direction."""
        mock_fetch.return_value = {
            "temp_f": 82,
            "wind_speed_mph": 15.0,
            "wind_direction_deg": 180.0,
        }
        client = self._make_supabase_mock(
            game_data=[{"game_pk": 2, "game_time_utc": "2026-04-02T23:10:00+00:00", "park_id": 11}],
            park_data=[{
                "latitude": "29.7573", "longitude": "-95.3555",
                "is_dome": False, "is_retractable_roof": True,
                "orientation_degrees": "345",
            }],
        )
        result = get_game_weather_for_prediction(2, client)
        assert result is not None
        assert result["temp_f"] == 82
        assert result["wind_speed_mph"] == 0.0
        assert result["wind_direction"] == "calm"
        assert result["is_outdoor"] is False

    @patch("src.data.weather_api.fetch_game_weather")
    def test_outdoor_park_returns_full_weather(self, mock_fetch):
        """Outdoor park should return temp, wind speed, and classified direction."""
        mock_fetch.return_value = {
            "temp_f": 65,
            "wind_speed_mph": 18.0,
            "wind_direction_deg": 270.0,  # from W
        }
        client = self._make_supabase_mock(
            game_data=[{"game_pk": 3, "game_time_utc": "2026-04-02T23:10:00+00:00", "park_id": 5}],
            park_data=[{
                "latitude": "41.9484", "longitude": "-87.6553",
                "is_dome": False, "is_retractable_roof": False,
                "orientation_degrees": "65",  # Wrigley
            }],
        )
        result = get_game_weather_for_prediction(3, client)
        assert result is not None
        assert result["temp_f"] == 65
        assert result["wind_speed_mph"] == 18.0
        assert result["wind_direction"] == "out"  # W wind at Wrigley blows out
        assert result["is_outdoor"] is True


# ── batch_fetch_weather ─────────────────────────────────────────────

class TestBatchFetchWeather:
    """Test batch fetching with deduplication."""

    @patch("src.data.weather_api.time.sleep")
    @patch("src.data.weather_api.fetch_game_weather")
    def test_deduplicates_by_park(self, mock_fetch, mock_sleep):
        """Two games at the same park should make only one API call."""
        mock_fetch.return_value = {
            "temp_f": 70,
            "wind_speed_mph": 10.0,
            "wind_direction_deg": 180.0,
        }
        games = [
            {
                "game_pk": 100, "latitude": 41.948, "longitude": -87.655,
                "game_time_utc": "2026-04-02T19:00:00Z",
                "is_dome": False, "is_retractable_roof": False,
                "orientation_degrees": 65,
            },
            {
                "game_pk": 101, "latitude": 41.948, "longitude": -87.655,
                "game_time_utc": "2026-04-02T23:00:00Z",
                "is_dome": False, "is_retractable_roof": False,
                "orientation_degrees": 65,
            },
        ]
        results = batch_fetch_weather(games)

        assert mock_fetch.call_count == 1  # Only one API call
        assert 100 in results
        assert 101 in results
        assert results[100]["temp_f"] == 70
        assert results[101]["temp_f"] == 70

    def test_dome_game_skipped(self):
        """Dome games should return None without API call."""
        games = [
            {
                "game_pk": 200, "latitude": 27.768, "longitude": -82.653,
                "game_time_utc": "2026-04-02T23:10:00Z",
                "is_dome": True, "is_retractable_roof": False,
                "orientation_degrees": 315,
            },
        ]
        with patch("src.data.weather_api.fetch_game_weather") as mock_fetch:
            results = batch_fetch_weather(games)
            mock_fetch.assert_not_called()
            assert results[200] is None
