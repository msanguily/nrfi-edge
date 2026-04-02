"""Tests for src/alerts/slack.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.alerts.slack import (
    format_american_odds,
    send_daily_summary,
    send_error_alert,
    send_health_check,
    send_no_plays_alert,
    send_nrfi_alert,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WEBHOOK = "https://hooks.slack.com/services/T00/B00/xxxx"


def _mock_prediction(**overrides):
    pred = {
        "game_pk": 718542,
        "game_date": "2024-06-15",
        "game_time_utc": "2024-06-15T23:05:00Z",
        "home_team_abbr": "NYY",
        "away_team_abbr": "BOS",
        "home_pitcher_name": "Gerrit Cole",
        "away_pitcher_name": "Brayan Bello",
        "home_pitcher_throws": "R",
        "away_pitcher_throws": "R",
        "p_nrfi_calibrated": 0.612,
        "best_book": "DraftKings",
        "best_nrfi_price": -135,
        "implied_prob_best": 0.558,
        "edge": 0.054,
        "bet_size_units": 0.8,
        "factor_details": {
            "park": "Yankee Stadium",
            "hr_factor": 1.12,
            "weather_summary": "72°F, 8 mph out",
            "outdoor": True,
        },
    }
    pred.update(overrides)
    return pred


def _mock_summary(**overrides):
    s = {
        "date": "2024-06-15",
        "games_analyzed": 15,
        "bets_recommended": 3,
        "wins": 2,
        "losses": 1,
        "pending": 0,
        "today_pl": 1.40,
        "season_pl": 12.50,
        "season_bets": 87,
        "season_roi": 0.064,
        "avg_clv": 0.018,
    }
    s.update(overrides)
    return s


# ---------------------------------------------------------------------------
# Test: format_american_odds
# ---------------------------------------------------------------------------


class TestFormatAmericanOdds:
    def test_positive(self):
        assert format_american_odds(250) == "+250"

    def test_negative(self):
        assert format_american_odds(-135) == "-135"

    def test_even(self):
        assert format_american_odds(100) == "+100"

    def test_zero(self):
        assert format_american_odds(0) == "+0"


# ---------------------------------------------------------------------------
# Test: send_nrfi_alert
# ---------------------------------------------------------------------------


class TestSendNrfiAlert:
    @patch("src.alerts.slack.requests.post")
    def test_complete_payload(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        pred = _mock_prediction()

        result = send_nrfi_alert(pred, WEBHOOK)

        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        text = payload["text"]

        # Verify key content in the message
        assert "NRFI PICK" in text
        assert "BOS @ NYY" in text
        assert "7:05 PM ET" in text
        assert "Brayan Bello (R) vs Gerrit Cole (R)" in text
        assert "61.2% NRFI" in text
        assert "-135" in text
        assert "DraftKings" in text
        assert "55.8%" in text
        assert "5.4%" in text
        assert "0.8 units" in text
        assert "Yankee Stadium" in text
        assert "1.12" in text
        assert "72°F, 8 mph out" in text

    @patch("src.alerts.slack.requests.post")
    def test_indoor_park_no_weather(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        pred = _mock_prediction(
            factor_details={
                "park": "Tropicana Field",
                "hr_factor": 0.88,
                "weather_summary": "",
                "outdoor": False,
            }
        )

        send_nrfi_alert(pred, WEBHOOK)

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        text = payload["text"]
        assert "Tropicana Field (HR factor: 0.88)" in text
        assert "72°F" not in text

    @patch("src.alerts.slack.requests.post")
    def test_positive_odds(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        pred = _mock_prediction(best_nrfi_price=115)

        send_nrfi_alert(pred, WEBHOOK)

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "+115" in payload["text"]


# ---------------------------------------------------------------------------
# Test: missing webhook URL
# ---------------------------------------------------------------------------


class TestMissingWebhook:
    @patch.dict("os.environ", {}, clear=True)
    def test_nrfi_alert_none(self):
        assert send_nrfi_alert(_mock_prediction(), None) is False

    @patch.dict("os.environ", {}, clear=True)
    def test_nrfi_alert_empty(self):
        assert send_nrfi_alert(_mock_prediction(), "") is False

    @patch.dict("os.environ", {}, clear=True)
    def test_daily_summary_none(self):
        assert send_daily_summary(_mock_summary(), None) is False

    @patch.dict("os.environ", {}, clear=True)
    def test_no_plays_none(self):
        assert send_no_plays_alert("2024-06-15", 15, 0.021, None) is False

    @patch.dict("os.environ", {}, clear=True)
    def test_error_alert_none(self):
        assert send_error_alert("DB timeout", None) is False

    @patch.dict("os.environ", {}, clear=True)
    def test_health_check_none(self):
        status = {"date": "2024-06-15", "num_games": 15, "pitchers_confirmed": 12, "total": 15, "pitcher_staleness": "2h", "batter_staleness": "2h"}
        assert send_health_check(status, None) is False


# ---------------------------------------------------------------------------
# Test: send_daily_summary
# ---------------------------------------------------------------------------


class TestDailySummary:
    @patch("src.alerts.slack.requests.post")
    def test_standard_summary(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        result = send_daily_summary(_mock_summary(), WEBHOOK)

        assert result is True
        text = mock_post.call_args.kwargs.get("json", mock_post.call_args[1]["json"])["text"]
        assert "NRFI Daily Summary" in text
        assert "Games analyzed: 15" in text
        assert "Bets: 3" in text
        assert "2W - 1L" in text
        assert "+1.40u" in text
        assert "+12.50u" in text
        assert "87 bets" in text
        assert "6.4% ROI" in text
        assert "+0.018" in text

    @patch("src.alerts.slack.requests.post")
    def test_zero_bets(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        s = _mock_summary(bets_recommended=0, wins=0, losses=0, today_pl=0.0)
        send_daily_summary(s, WEBHOOK)

        text = mock_post.call_args.kwargs.get("json", mock_post.call_args[1]["json"])["text"]
        assert "Bets: 0" in text
        assert "0W - 0L" in text
        assert "+0.00u" in text

    @patch("src.alerts.slack.requests.post")
    def test_negative_pl(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        s = _mock_summary(today_pl=-2.30, season_pl=-5.10, season_roi=-0.032)
        send_daily_summary(s, WEBHOOK)

        text = mock_post.call_args.kwargs.get("json", mock_post.call_args[1]["json"])["text"]
        assert "-2.30u" in text
        assert "-5.10u" in text
        assert "-3.2% ROI" in text

    @patch("src.alerts.slack.requests.post")
    def test_pending_games(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        s = _mock_summary(pending=2)
        send_daily_summary(s, WEBHOOK)

        text = mock_post.call_args.kwargs.get("json", mock_post.call_args[1]["json"])["text"]
        assert "(2 pending)" in text


# ---------------------------------------------------------------------------
# Test: send_no_plays_alert
# ---------------------------------------------------------------------------


class TestNoPlaysAlert:
    @patch("src.alerts.slack.requests.post")
    def test_format(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        result = send_no_plays_alert("2024-06-15", 15, 0.021, WEBHOOK)

        assert result is True
        text = mock_post.call_args.kwargs.get("json", mock_post.call_args[1]["json"])["text"]
        assert "No NRFI plays for 2024-06-15" in text
        assert "15 games analyzed" in text
        assert "2.1%" in text
        assert "below 3.0% threshold" in text


# ---------------------------------------------------------------------------
# Test: send_error_alert
# ---------------------------------------------------------------------------


class TestErrorAlert:
    @patch("src.alerts.slack.requests.post")
    def test_includes_timestamp(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        result = send_error_alert("Database connection timeout", WEBHOOK)

        assert result is True
        text = mock_post.call_args.kwargs.get("json", mock_post.call_args[1]["json"])["text"]
        assert "NRFI System Error" in text
        assert "Database connection timeout" in text
        assert "Timestamp:" in text
        assert "UTC" in text


# ---------------------------------------------------------------------------
# Test: UTC to ET conversion (DST handling)
# ---------------------------------------------------------------------------


class TestTimeConversion:
    @patch("src.alerts.slack.requests.post")
    def test_summer_edt(self, mock_post):
        """June = EDT (UTC-4). 23:05 UTC → 7:05 PM ET."""
        mock_post.return_value = MagicMock(status_code=200)
        pred = _mock_prediction(game_time_utc="2024-06-15T23:05:00Z")
        send_nrfi_alert(pred, WEBHOOK)

        text = mock_post.call_args.kwargs.get("json", mock_post.call_args[1]["json"])["text"]
        assert "7:05 PM ET" in text

    @patch("src.alerts.slack.requests.post")
    def test_winter_est(self, mock_post):
        """March 1 = EST (UTC-5). 23:05 UTC → 6:05 PM ET."""
        mock_post.return_value = MagicMock(status_code=200)
        pred = _mock_prediction(game_time_utc="2024-03-01T23:05:00Z")
        send_nrfi_alert(pred, WEBHOOK)

        text = mock_post.call_args.kwargs.get("json", mock_post.call_args[1]["json"])["text"]
        assert "6:05 PM ET" in text


# ---------------------------------------------------------------------------
# Test: retry behavior
# ---------------------------------------------------------------------------


class TestRetries:
    @patch("src.alerts.slack.time.sleep")
    @patch("src.alerts.slack.requests.post")
    def test_retries_on_failure(self, mock_post, mock_sleep):
        mock_post.return_value = MagicMock(status_code=500, text="Server Error")
        result = send_error_alert("test", WEBHOOK)

        assert result is False
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("src.alerts.slack.time.sleep")
    @patch("src.alerts.slack.requests.post")
    def test_succeeds_on_second_attempt(self, mock_post, mock_sleep):
        mock_post.side_effect = [
            MagicMock(status_code=500, text="err"),
            MagicMock(status_code=200),
        ]
        result = send_error_alert("test", WEBHOOK)

        assert result is True
        assert mock_post.call_count == 2
