"""Client for the MLB Stats API (https://statsapi.mlb.com)."""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://statsapi.mlb.com/api/v1"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def _request(url: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """Make a GET request with retries. Returns parsed JSON or None on failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, OSError) as e:
            logger.warning("Request to %s failed (attempt %d/%d): %s", url, attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    logger.error("All %d attempts failed for %s", MAX_RETRIES, url)
    return None


def get_todays_games() -> Optional[List[Dict]]:
    """Fetch today's MLB schedule with probable pitchers and linescores.

    Returns list of dicts with: game_pk, game_date, game_time_utc,
    home_team_id, away_team_id, home_pitcher_id, away_pitcher_id,
    status, is_day_game. Returns None on API failure.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _request(
        f"{BASE_URL}/schedule",
        params={"sportId": 1, "date": today, "hydrate": "probablePitcher,linescore"},
    )
    if data is None:
        return None

    games = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})

            home_pitcher = home.get("probablePitcher", {})
            away_pitcher = away.get("probablePitcher", {})

            game_time = g.get("gameDate")  # ISO 8601 UTC
            # Day game heuristic: before 5 PM ET (21:00 UTC)
            is_day = False
            if game_time:
                try:
                    dt = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
                    is_day = dt.hour < 21
                except (ValueError, AttributeError):
                    pass

            games.append({
                "game_pk": g.get("gamePk"),
                "game_date": today,
                "game_time_utc": game_time,
                "home_team_id": home.get("team", {}).get("id"),
                "away_team_id": away.get("team", {}).get("id"),
                "home_pitcher_id": home_pitcher.get("id"),
                "away_pitcher_id": away_pitcher.get("id"),
                "status": g.get("status", {}).get("detailedState"),
                "is_day_game": is_day,
            })
    return games


def get_confirmed_lineups(game_pk: int) -> Optional[Dict]:
    """Fetch batting order lineups for a specific game.

    Returns {'home': [mlb_player_ids], 'away': [mlb_player_ids]}.
    A team's value is None if lineups aren't confirmed (empty battingOrder).
    Returns None on API failure.
    """
    data = _request(f"{BASE_URL}/game/{game_pk}/boxscore")
    if data is None:
        return None

    result = {}
    for side in ("home", "away"):
        team_data = data.get("teams", {}).get(side, {})
        players = team_data.get("players", {})
        batting_order = team_data.get("battingOrder", [])

        if not batting_order:
            result[side] = None
        else:
            result[side] = [int(pid) for pid in batting_order]

    return result


def get_game_linescore(game_pk: int) -> Optional[Dict]:
    """Fetch first-inning linescore for a game.

    Returns {'away_first_inning_runs': int, 'home_first_inning_runs': int, 'nrfi': bool}.
    Returns None if the first inning hasn't been played yet or on API failure.
    """
    data = _request(f"{BASE_URL}/game/{game_pk}/linescore")
    if data is None:
        return None

    innings = data.get("innings", [])
    if not innings:
        return None

    first = innings[0]
    away_runs = first.get("away", {}).get("runs")
    home_runs = first.get("home", {}).get("runs")

    if away_runs is None or home_runs is None:
        return None

    return {
        "away_first_inning_runs": int(away_runs),
        "home_first_inning_runs": int(home_runs),
        "nrfi": int(away_runs) == 0 and int(home_runs) == 0,
    }


def get_probable_pitchers(date: str) -> Optional[List[Dict]]:
    """Fetch probable pitchers for all games on a given date.

    Args:
        date: Date string in YYYY-MM-DD format.

    Returns list of dicts: game_pk, home_pitcher_id, home_pitcher_name,
    away_pitcher_id, away_pitcher_name. Returns None on API failure.
    """
    data = _request(
        f"{BASE_URL}/schedule",
        params={"sportId": 1, "date": date, "hydrate": "probablePitcher"},
    )
    if data is None:
        return None

    pitchers = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            home_p = home.get("probablePitcher", {})
            away_p = away.get("probablePitcher", {})

            pitchers.append({
                "game_pk": g.get("gamePk"),
                "home_pitcher_id": home_p.get("id"),
                "home_pitcher_name": home_p.get("fullName"),
                "away_pitcher_id": away_p.get("id"),
                "away_pitcher_name": away_p.get("fullName"),
            })
    return pitchers


def get_player_info(mlb_player_id: int) -> Optional[Dict]:
    """Fetch player biographical info.

    Returns: name, throws, bats, position, current_team_id.
    Returns None on API failure or if player not found.
    """
    data = _request(f"{BASE_URL}/people/{mlb_player_id}")
    if data is None:
        return None

    people = data.get("people", [])
    if not people:
        return None

    p = people[0]
    return {
        "name": p.get("fullName"),
        "throws": p.get("pitchHand", {}).get("code"),
        "bats": p.get("batSide", {}).get("code"),
        "position": p.get("primaryPosition", {}).get("abbreviation"),
        "current_team_id": p.get("currentTeam", {}).get("id"),
    }


def get_hp_umpire(game_pk: int) -> Optional[Dict]:
    """Fetch the home plate umpire for a game.

    Returns: mlb_umpire_id, name. Returns None if not found or on API failure.
    """
    data = _request(f"{BASE_URL}/game/{game_pk}/boxscore")
    if data is None:
        return None

    officials = data.get("officials", [])
    for official in officials:
        if official.get("officialType") == "Home Plate":
            off = official.get("official", {})
            return {
                "mlb_umpire_id": off.get("id"),
                "name": off.get("fullName"),
            }

    return None
