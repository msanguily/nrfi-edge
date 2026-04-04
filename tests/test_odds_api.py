"""Tests for src/data/odds_api.py."""

import pytest
from unittest.mock import patch, MagicMock

from src.betting.edge import american_to_decimal, decimal_to_implied
from src.data.odds_api import find_best_nrfi_line, fetch_nrfi_odds


# ---------------------------------------------------------------------------
# find_best_nrfi_line
# ---------------------------------------------------------------------------


class TestFindBestNrfiLine:
    def test_best_among_negatives(self):
        odds = {
            "draftkings": {"odds": -110, "deeplink": "https://dk.com/bet"},
            "fanduel": {"odds": -194, "deeplink": "https://fd.com/bet"},
            "betmgm": {"odds": -105, "deeplink": "https://mgm.com/bet"},
        }
        book, price, link = find_best_nrfi_line(odds)
        assert book == "betmgm"
        assert price == -105
        assert link == "https://mgm.com/bet"

    def test_best_with_positive_odds(self):
        odds = {
            "draftkings": {"odds": 100, "deeplink": None},
            "bovada": {"odds": 110, "deeplink": None},
        }
        book, price, link = find_best_nrfi_line(odds)
        assert book == "bovada"
        assert price == 110
        assert link is None


# ---------------------------------------------------------------------------
# american_to_implied_prob (composed from edge.py)
# ---------------------------------------------------------------------------


class TestAmericanToImpliedProb:
    def test_minus_110(self):
        dec = american_to_decimal(-110)
        prob = decimal_to_implied(dec)
        assert prob == pytest.approx(0.5238, abs=0.001)

    def test_plus_100(self):
        dec = american_to_decimal(100)
        prob = decimal_to_implied(dec)
        assert prob == pytest.approx(0.50, abs=0.001)

    def test_minus_200(self):
        dec = american_to_decimal(-200)
        prob = decimal_to_implied(dec)
        assert prob == pytest.approx(0.6667, abs=0.001)


# ---------------------------------------------------------------------------
# Mock API response matching SGO format
# ---------------------------------------------------------------------------

MOCK_SGO_RESPONSE = {
    "data": [
        {
            "eventID": "MLB-2026-04-02-NYY-BOS",
            "teams": {
                "home": {"names": {"long": "Boston Red Sox"}},
                "away": {"names": {"long": "New York Yankees"}},
            },
            "status": {"startsAt": "2026-04-02T17:10:00Z"},
            "odds": {
                "points-all-1i-ou-under": {
                    "fairOdds": "-109",
                    "closeBookOdds": "-112",
                    "byBookmaker": {
                        "draftkings": {
                            "odds": "-120",
                            "overUnder": "0.5",
                            "available": True,
                            "deeplink": "https://dk.com/nrfi-nyy-bos",
                        },
                        "fanduel": {
                            "odds": "-115",
                            "overUnder": "0.5",
                            "available": True,
                            "deeplink": "https://fd.com/nrfi-nyy-bos",
                        },
                        "pinnacle": {
                            "odds": "-112",
                            "overUnder": "0.5",
                            "available": True,
                        },
                        "betrivers": {
                            "odds": "-130",
                            "overUnder": "0.5",
                            "available": False,
                            "deeplink": "https://br.com/nrfi-nyy-bos",
                        },
                        "fanatics": {
                            "odds": "-250",
                            "overUnder": "1.5",
                            "available": True,
                            "deeplink": "https://fan.com/nrfi-nyy-bos",
                        },
                    },
                },
                "points-all-1i-ou-over": {
                    "fairOdds": "-109",
                    "byBookmaker": {
                        "draftkings": {
                            "odds": "100",
                            "overUnder": "0.5",
                            "available": True,
                            "deeplink": "https://dk.com/yrfi-nyy-bos",
                        },
                        "fanduel": {
                            "odds": "-105",
                            "overUnder": "0.5",
                            "available": True,
                        },
                        "pinnacle": {
                            "odds": "-108",
                            "overUnder": "0.5",
                            "available": True,
                        },
                        "betrivers": {
                            "odds": "110",
                            "overUnder": "0.5",
                            "available": False,
                        },
                    },
                },
            },
        }
    ],
    "nextCursor": None,
}


class TestFetchNrfiOdds:
    @patch("src.data.odds_api._request")
    @patch("src.data.odds_api._get_api_key", return_value="test-key")
    def test_parses_mock_response(self, _mock_key, mock_request):
        mock_request.return_value = MOCK_SGO_RESPONSE

        results = fetch_nrfi_odds("2026-04-02")

        assert len(results) == 1
        game = results[0]

        assert game["sgo_event_id"] == "MLB-2026-04-02-NYY-BOS"
        assert game["home_team_name"] == "Boston Red Sox"
        assert game["away_team_name"] == "New York Yankees"
        assert game["starts_at"] == "2026-04-02T17:10:00Z"

        # Best NRFI line should be pinnacle at -112 (least negative)
        assert game["best_nrfi_book"] == "pinnacle"
        assert game["best_nrfi_price"] == -112

        # Fair and close odds
        assert game["fair_odds"] == -109
        assert game["close_odds"] == -112

        # Pinnacle odds
        assert game["pinnacle_odds"] == -112

        # Deeplinks — only bookmakers with available=True and a deeplink field
        assert "draftkings" in game["all_deeplinks"]
        assert "fanduel" in game["all_deeplinks"]
        assert "pinnacle" not in game["all_deeplinks"]  # no deeplink field
        assert "betrivers" not in game["all_deeplinks"]  # available=False

    @patch("src.data.odds_api._request")
    @patch("src.data.odds_api._get_api_key", return_value="test-key")
    def test_available_true_only(self, _mock_key, mock_request):
        """Only bookmakers with available=True are included in odds dicts."""
        mock_request.return_value = MOCK_SGO_RESPONSE

        results = fetch_nrfi_odds("2026-04-02")
        game = results[0]

        # NRFI: draftkings, fanduel, pinnacle available; betrivers not
        assert "draftkings" in game["nrfi_odds"]
        assert "fanduel" in game["nrfi_odds"]
        assert "pinnacle" in game["nrfi_odds"]
        assert "betrivers" not in game["nrfi_odds"]
        assert len(game["nrfi_odds"]) == 3

        # YRFI: same pattern
        assert "draftkings" in game["yrfi_odds"]
        assert "fanduel" in game["yrfi_odds"]
        assert "pinnacle" in game["yrfi_odds"]
        assert "betrivers" not in game["yrfi_odds"]
        assert len(game["yrfi_odds"]) == 3

    @patch("src.data.odds_api._request")
    @patch("src.data.odds_api._get_api_key", return_value="test-key")
    def test_unavailable_excluded_despite_odds(self, _mock_key, mock_request):
        """Bookmakers with available=False are excluded even if they have odds."""
        mock_request.return_value = MOCK_SGO_RESPONSE

        results = fetch_nrfi_odds("2026-04-02")
        game = results[0]

        # betrivers has odds=-130 NRFI but available=False
        assert "betrivers" not in game["nrfi_odds"]
        # betrivers has odds=110 YRFI but available=False
        assert "betrivers" not in game["yrfi_odds"]

    @patch("src.data.odds_api._request")
    @patch("src.data.odds_api._get_api_key", return_value="test-key")
    def test_overunder_filter(self, _mock_key, mock_request):
        """Bookmakers with overUnder != 0.5 are excluded."""
        mock_request.return_value = MOCK_SGO_RESPONSE

        results = fetch_nrfi_odds("2026-04-02")
        game = results[0]

        # fanatics has overUnder=1.5, should be excluded
        assert "fanatics" not in game["nrfi_odds"]
        assert len(game["nrfi_odds"]) == 3  # dk, fd, pinnacle only

    @patch("src.data.odds_api._request")
    @patch("src.data.odds_api._get_api_key", return_value="test-key")
    def test_pagination(self, _mock_key, mock_request):
        """Handles pagination via nextCursor."""
        page1 = {
            "data": [MOCK_SGO_RESPONSE["data"][0]],
            "nextCursor": "cursor-abc",
        }
        page2_event = {
            **MOCK_SGO_RESPONSE["data"][0],
            "eventID": "MLB-2026-04-02-LAD-SF",
            "teams": {
                "home": {"names": {"long": "San Francisco Giants"}},
                "away": {"names": {"long": "Los Angeles Dodgers"}},
            },
        }
        page2 = {"data": [page2_event], "nextCursor": None}

        mock_request.side_effect = [page1, page2]

        results = fetch_nrfi_odds("2026-04-02")
        assert len(results) == 2
        assert results[0]["sgo_event_id"] == "MLB-2026-04-02-NYY-BOS"
        assert results[1]["sgo_event_id"] == "MLB-2026-04-02-LAD-SF"

        # Verify cursor was passed in second call
        second_call_params = mock_request.call_args_list[1][0][0]
        assert second_call_params["cursor"] == "cursor-abc"
