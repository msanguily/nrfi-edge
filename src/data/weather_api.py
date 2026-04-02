"""Client for Open-Meteo weather API (free, no API key required)."""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def classify_wind_direction(wind_from_degrees: float, outfield_orientation_deg: float) -> str:
    """
    Determine if wind is blowing OUT (toward outfield), IN (toward home plate), or CROSS.

    wind_from_degrees: meteorological convention — where wind is coming FROM (0=N, 90=E).
    outfield_orientation_deg: direction outfield faces from home plate (0=N, 90=E).

    Wind blows TOWARD = (wind_from + 180) % 360. If that direction is close to the
    outfield orientation, wind is blowing out toward the fences.
    """
    wind_toward = (wind_from_degrees + 180) % 360
    angle_diff = abs(wind_toward - outfield_orientation_deg)
    if angle_diff > 180:
        angle_diff = 360 - angle_diff

    if angle_diff <= 45:
        return "out"
    elif angle_diff >= 135:
        return "in"
    else:
        return "cross"


def fetch_game_weather(
    latitude: float, longitude: float, game_time_utc: str
) -> Optional[Dict]:
    """
    Fetch weather forecast for a specific location and time from Open-Meteo.

    Parameters
    ----------
    latitude, longitude : float
        Park coordinates.
    game_time_utc : str
        ISO 8601 timestamp in UTC (e.g. "2026-04-02T23:10:00Z").

    Returns
    -------
    dict with keys 'temp_f', 'wind_speed_mph', 'wind_direction_deg', or None on failure.
    """
    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "auto",
                "forecast_days": 3,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Open-Meteo request failed: %s", e)
        return None

    hourly = data.get("hourly")
    if not hourly or "time" not in hourly:
        logger.warning("Open-Meteo response missing hourly data")
        return None

    # Parse game time and find closest hour
    if isinstance(game_time_utc, str):
        gt = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
    else:
        gt = game_time_utc

    utc_offset = data.get("utc_offset_seconds", 0)
    # Convert game time to local naive datetime for comparison
    game_local_dt = (gt + timedelta(seconds=utc_offset)).replace(tzinfo=None)

    best_idx = None
    best_diff = float("inf")
    for i, t_str in enumerate(hourly["time"]):
        # Open-Meteo returns local times like "2026-04-02T19:00"
        local_dt = datetime.strptime(t_str, "%Y-%m-%dT%H:%M")
        diff = abs((local_dt - game_local_dt).total_seconds())
        if diff < best_diff:
            best_diff = diff
            best_idx = i

    if best_idx is None:
        return None

    temp = hourly.get("temperature_2m", [None])[best_idx]
    wind_speed = hourly.get("wind_speed_10m", [None])[best_idx]
    wind_dir = hourly.get("wind_direction_10m", [None])[best_idx]

    if temp is None or wind_speed is None or wind_dir is None:
        return None

    return {
        "temp_f": round(temp),
        "wind_speed_mph": round(wind_speed, 1),
        "wind_direction_deg": round(wind_dir, 1),
    }


def get_game_weather_for_prediction(
    game_pk: int, supabase_client
) -> Optional[Dict]:
    """
    High-level: look up a game, fetch weather, classify wind direction.

    Returns dict with keys: temp_f, wind_speed_mph, wind_direction, is_outdoor.
    Returns None for dome games.
    For retractable-roof parks, returns temp only (wind unknown if roof closed).
    """
    # Look up game
    game_resp = (
        supabase_client.table("games")
        .select("game_pk, game_time_utc, park_id")
        .eq("game_pk", game_pk)
        .limit(1)
        .execute()
    )
    if not game_resp.data:
        logger.warning("Game %d not found", game_pk)
        return None
    game = game_resp.data[0]

    # Look up park
    park_resp = (
        supabase_client.table("parks")
        .select("latitude, longitude, is_dome, is_retractable_roof, orientation_degrees")
        .eq("park_id", game["park_id"])
        .limit(1)
        .execute()
    )
    if not park_resp.data:
        logger.warning("Park %d not found", game["park_id"])
        return None
    park = park_resp.data[0]

    # Dome = no weather adjustment
    if park.get("is_dome"):
        return None

    game_time = game.get("game_time_utc")
    if not game_time:
        logger.warning("Game %d has no game_time_utc", game_pk)
        return None

    lat = float(park["latitude"])
    lon = float(park["longitude"])
    weather = fetch_game_weather(lat, lon, game_time)
    if weather is None:
        return None

    # Retractable roof: use temperature only, skip wind (roof may be closed)
    if park.get("is_retractable_roof"):
        return {
            "temp_f": weather["temp_f"],
            "wind_speed_mph": 0.0,
            "wind_direction": "calm",
            "is_outdoor": False,
        }

    # Outdoor park: classify wind direction
    orientation = float(park.get("orientation_degrees", 0))
    wind_dir_label = classify_wind_direction(
        weather["wind_direction_deg"], orientation
    )

    return {
        "temp_f": weather["temp_f"],
        "wind_speed_mph": weather["wind_speed_mph"],
        "wind_direction": wind_dir_label,
        "is_outdoor": True,
    }


def batch_fetch_weather(games: List[Dict]) -> Dict[int, Optional[Dict]]:
    """
    Fetch weather for multiple games efficiently.

    Groups games by park to avoid duplicate API calls for the same park/day.
    Expects each game dict to have: game_pk, latitude, longitude,
    game_time_utc, is_dome, is_retractable_roof, orientation_degrees.

    Returns dict mapping game_pk -> weather dict (or None).
    """
    results: Dict[int, Optional[Dict]] = {}

    # Group by (lat, lon) to deduplicate park calls
    park_cache: Dict[tuple, Optional[Dict]] = {}

    for game in games:
        game_pk = game["game_pk"]

        if game.get("is_dome"):
            results[game_pk] = None
            continue

        lat = float(game["latitude"])
        lon = float(game["longitude"])
        cache_key = (lat, lon)

        if cache_key not in park_cache:
            # Respectful delay between API calls
            if park_cache:
                time.sleep(0.5)
            park_cache[cache_key] = fetch_game_weather(
                lat, lon, game["game_time_utc"]
            )

        raw = park_cache[cache_key]
        if raw is None:
            results[game_pk] = None
            continue

        if game.get("is_retractable_roof"):
            results[game_pk] = {
                "temp_f": raw["temp_f"],
                "wind_speed_mph": 0.0,
                "wind_direction": "calm",
                "is_outdoor": False,
            }
        else:
            orientation = float(game.get("orientation_degrees", 0))
            wind_label = classify_wind_direction(
                raw["wind_direction_deg"], orientation
            )
            results[game_pk] = {
                "temp_f": raw["temp_f"],
                "wind_speed_mph": raw["wind_speed_mph"],
                "wind_direction": wind_label,
                "is_outdoor": True,
            }

    return results
