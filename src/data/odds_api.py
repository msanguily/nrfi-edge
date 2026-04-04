"""Client for the SportsGameOdds API — fetches live NRFI/YRFI odds."""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from src.betting.edge import american_to_decimal, decimal_to_implied

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sportsgameodds.com/v2/events"

# Cache: resolved DB team name -> mlb_team_id
_team_name_cache: Dict[str, int] = {}

# SGO long names that don't match our DB exactly
_TEAM_NAME_MAP = {
    "Oakland Athletics": "Athletics",
    "Sacramento Athletics": "Athletics",
}


def _parse_odds_str(val) -> Optional[int]:
    """Convert an odds value (string or int) to int, or None."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _get_api_key() -> str:
    key = os.getenv("SPORTSGAMEODDS_API_KEY")
    if not key:
        raise ValueError("SPORTSGAMEODDS_API_KEY not set in environment")
    return key


def _request(params: Dict) -> Optional[Dict]:
    """Make a GET request to the SGO events endpoint."""
    headers = {"x-api-key": _get_api_key()}
    try:
        resp = requests.get(BASE_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error("SGO API request failed: %s", e)
        return None


def find_best_nrfi_line(
    bookmaker_odds: Dict[str, Dict],
) -> Tuple[str, int, Optional[str]]:
    """Find the bookmaker with the best NRFI American odds.

    Args:
        bookmaker_odds: {bookmaker: {'odds': int, 'deeplink': str or None}}

    Returns:
        (best_bookmaker, best_odds, best_deeplink)
        Best = highest American odds (-105 > -110, +110 > +100).
    """
    best_book = max(bookmaker_odds, key=lambda b: bookmaker_odds[b]["odds"])
    entry = bookmaker_odds[best_book]
    return (best_book, entry["odds"], entry.get("deeplink"))


def fetch_nrfi_odds(game_date: str = None, include_completed: bool = False) -> List[Dict]:
    """Fetch NRFI/YRFI odds for all MLB games on a given date.

    Args:
        game_date: YYYY-MM-DD string. Defaults to today (UTC).
        include_completed: If True, include games where odds are no longer
            available (for fetching closing lines on completed games).

    Returns:
        List of dicts, one per game, with odds data including per-bookmaker
        prices, best line, fair odds, and deeplinks.
    """
    if game_date is None:
        game_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    params = {
        "leagueID": "MLB",
        "startsAfter": f"{game_date}T00:00:00Z",
        "startsBefore": f"{game_date}T23:59:59Z",
        "oddID": "points-all-1i-ou-over,points-all-1i-ou-under",
        "limit": 100,
    }
    if not include_completed:
        params["oddsAvailable"] = "true"

    all_events = []
    while True:
        data = _request(params)
        if data is None:
            logger.error("Failed to fetch odds for %s", game_date)
            return []

        events = data.get("data", [])
        all_events.extend(events)

        next_cursor = data.get("nextCursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor

    results = []
    for event in all_events:
        odds_data = event.get("odds", {})
        nrfi_raw = odds_data.get("points-all-1i-ou-under", {})
        yrfi_raw = odds_data.get("points-all-1i-ou-over", {})

        # Extract bookmaker odds, filtering to available=True only.
        # Filter to overUnder=="0.5" to exclude alternate lines (e.g. 1.5).
        nrfi_odds = {}
        nrfi_details = {}  # includes deeplink for find_best_nrfi_line
        for book, info in nrfi_raw.get("byBookmaker", {}).items():
            if info.get("available") is not True:
                continue
            if info.get("overUnder") is not None and str(info["overUnder"]) != "0.5":
                continue
            try:
                odds_val = int(info["odds"])
            except (ValueError, TypeError, KeyError):
                continue
            nrfi_odds[book] = odds_val
            nrfi_details[book] = {
                "odds": odds_val,
                "deeplink": info.get("deeplink"),
            }

        yrfi_odds = {}
        for book, info in yrfi_raw.get("byBookmaker", {}).items():
            if info.get("available") is not True:
                continue
            if info.get("overUnder") is not None and str(info["overUnder"]) != "0.5":
                continue
            try:
                odds_val = int(info["odds"])
            except (ValueError, TypeError, KeyError):
                continue
            yrfi_odds[book] = odds_val

        # Best NRFI line
        best_nrfi_book = None
        best_nrfi_price = None
        best_nrfi_deeplink = None
        if nrfi_details:
            best_nrfi_book, best_nrfi_price, best_nrfi_deeplink = find_best_nrfi_line(
                nrfi_details
            )

        # Deeplinks for all available bookmakers
        all_deeplinks = {}
        for book, info in nrfi_details.items():
            dl = info.get("deeplink")
            if dl:
                all_deeplinks[book] = dl

        # Fair odds (vig-removed) and close odds (for CLV)
        fair_odds = _parse_odds_str(nrfi_raw.get("fairOdds"))
        close_odds = _parse_odds_str(
            nrfi_raw.get("closeBookOdds") or nrfi_raw.get("closeFairOdds")
        )

        # Pinnacle as sharp benchmark
        pinnacle_info = nrfi_raw.get("byBookmaker", {}).get("pinnacle", {})
        pinnacle_odds = None
        if pinnacle_info.get("available") is True:
            pinnacle_odds = _parse_odds_str(pinnacle_info.get("odds"))

        teams = event.get("teams", {})
        results.append({
            "sgo_event_id": event.get("eventID"),
            "home_team_name": teams.get("home", {}).get("names", {}).get("long"),
            "away_team_name": teams.get("away", {}).get("names", {}).get("long"),
            "starts_at": event.get("status", {}).get("startsAt"),
            "nrfi_odds": nrfi_odds,
            "yrfi_odds": yrfi_odds,
            "best_nrfi_book": best_nrfi_book,
            "best_nrfi_price": best_nrfi_price,
            "best_nrfi_deeplink": best_nrfi_deeplink,
            "fair_odds": fair_odds,
            "close_odds": close_odds,
            "pinnacle_odds": pinnacle_odds,
            "all_deeplinks": all_deeplinks,
        })

    logger.info("Fetched odds for %d games on %s", len(results), game_date)
    return results


def match_to_game_pk(
    home_team_name: str, away_team_name: str, game_date: str, supabase_client
) -> Optional[int]:
    """Match an SGO event to our games table game_pk.

    Uses home team name to find mlb_team_id, then looks up the game by date.
    Caches team name -> mlb_team_id mappings to avoid repeated lookups.
    """
    db_name = _TEAM_NAME_MAP.get(home_team_name, home_team_name)

    if db_name not in _team_name_cache:
        result = (
            supabase_client.table("teams")
            .select("mlb_team_id")
            .eq("name", db_name)
            .execute()
        )
        if result.data:
            _team_name_cache[db_name] = result.data[0]["mlb_team_id"]
        else:
            logger.warning(
                "Team not found in DB: %s (SGO name: %s)", db_name, home_team_name
            )
            return None

    home_team_id = _team_name_cache[db_name]

    result = (
        supabase_client.table("games")
        .select("game_pk")
        .eq("game_date", game_date)
        .eq("home_team_id", home_team_id)
        .execute()
    )
    if result.data:
        return result.data[0]["game_pk"]

    logger.warning("No game found for %s at home on %s", home_team_name, game_date)
    return None


def store_odds(odds_list: List[Dict], supabase_client) -> int:
    """Store fetched odds in the odds table.

    Stores one row per (game_pk, bookmaker). Uses upsert with ON CONFLICT
    on (game_pk, book) to handle re-fetches gracefully.
    Returns count of rows inserted/updated. Skips unmatched games.
    """
    rows_stored = 0

    for game in odds_list:
        game_date = game["starts_at"][:10] if game.get("starts_at") else None
        if not game_date:
            logger.warning(
                "Skipping game with no starts_at: %s", game.get("sgo_event_id")
            )
            continue

        game_pk = match_to_game_pk(
            game["home_team_name"],
            game["away_team_name"],
            game_date,
            supabase_client,
        )
        if game_pk is None:
            logger.warning(
                "Skipping odds for unmatched game: %s vs %s on %s",
                game.get("away_team_name"),
                game.get("home_team_name"),
                game_date,
            )
            continue

        all_books = set(game.get("nrfi_odds", {}).keys()) | set(
            game.get("yrfi_odds", {}).keys()
        )

        for book in all_books:
            nrfi_price = game["nrfi_odds"].get(book)
            yrfi_price = game["yrfi_odds"].get(book)

            nrfi_dec = (
                round(american_to_decimal(nrfi_price), 3) if nrfi_price else None
            )
            yrfi_dec = (
                round(american_to_decimal(yrfi_price), 3) if yrfi_price else None
            )
            implied = (
                round(decimal_to_implied(nrfi_dec), 4) if nrfi_dec else None
            )

            row = {
                "game_pk": game_pk,
                "book": book,
                "nrfi_price": nrfi_price,
                "yrfi_price": yrfi_price,
                "nrfi_decimal": nrfi_dec,
                "yrfi_decimal": yrfi_dec,
                "implied_nrfi_prob": implied,
            }

            supabase_client.table("odds").upsert(
                row, on_conflict="game_pk,book"
            ).execute()
            rows_stored += 1

    logger.info("Stored %d odds rows", rows_stored)
    return rows_stored
