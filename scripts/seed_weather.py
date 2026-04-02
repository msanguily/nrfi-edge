#!/usr/bin/env python3
"""Seed weather_snapshots table from MLB Stats API game feed.

For each regular-season final game (2019-2025), fetches gameData.weather
from the live game feed. Parses temperature, wind speed/direction, and
condition. Skips dome games.

Resumable: skips game_pks that already have weather data.
"""

import re
import requests
import time

SUPABASE_URL = "https://cdomrqoslgewamcqhbal.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNkb21ycW9zbGdld2FtY3FoYmFsIiwi"
    "cm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTAxMDIwNSwiZXhwIjoyMDkw"
    "NTg2MjA1fQ._sYGKhDp5LL-8G7ZxZm2xsjfQBuUh-L4-0TEwKatvvk"
)

MLB_BASE = "https://statsapi.mlb.com/api/v1.1"

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

INSERT_HEADERS = {
    **SB_HEADERS,
    "Prefer": "resolution=ignore-duplicates,return=minimal",
}

API_DELAY = 0.3
FLUSH_EVERY = 100
PROGRESS_EVERY = 500


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def sb_fetch_all(table, params):
    rows = []
    offset = 0
    limit = 1000
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SB_HEADERS, "Range": f"{offset}-{offset + limit - 1}"},
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return rows


def sb_batch_post(table, rows):
    if not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    for i in range(0, len(rows), 200):
        batch = rows[i : i + 200]
        for attempt in range(3):
            try:
                r = requests.post(url, headers=INSERT_HEADERS, json=batch, timeout=30)
                if r.status_code in (200, 201):
                    break
                if r.status_code == 409:
                    for row in batch:
                        requests.post(url, headers=INSERT_HEADERS, json=row, timeout=10)
                    break
                print(
                    f"\n  WARN {table}[{i}]: HTTP {r.status_code} {r.text[:200]}",
                    flush=True,
                )
                if attempt < 2:
                    time.sleep(1)
            except Exception as e:
                print(f"\n  ERR {table}[{i}]: {e}", flush=True)
                if attempt < 2:
                    time.sleep(1)


# ---------------------------------------------------------------------------
# Wind parsing
# ---------------------------------------------------------------------------
def parse_wind(wind_str: str) -> tuple:
    """
    Parse MLB API wind string like "10 mph, Out To CF".

    Returns (speed_mph: float, relative: str).
    relative is one of: 'out', 'in', 'cross_l', 'cross_r', 'calm'.
    """
    if not wind_str:
        return 0.0, 'calm'

    # Extract speed
    speed_match = re.match(r'(\d+)\s*mph', wind_str, re.IGNORECASE)
    speed = float(speed_match.group(1)) if speed_match else 0.0

    if speed == 0:
        return 0.0, 'calm'

    wind_lower = wind_str.lower()

    # Parse direction
    if 'calm' in wind_lower:
        return 0.0, 'calm'
    elif 'out to' in wind_lower:
        return speed, 'out'
    elif 'in from' in wind_lower:
        return speed, 'in'
    elif 'l to r' in wind_lower or 'left to right' in wind_lower:
        return speed, 'cross_l'
    elif 'r to l' in wind_lower or 'right to left' in wind_lower:
        return speed, 'cross_r'
    elif 'varies' in wind_lower:
        return speed, 'calm'
    else:
        return speed, 'calm'


def is_dome_game(condition: str, park_is_dome: bool, park_is_retractable: bool) -> bool:
    """Determine if game was played with roof closed."""
    if park_is_dome:
        return True
    if not condition:
        return False
    c = condition.lower()
    if 'dome' in c or 'roof closed' in c:
        return True
    # Retractable roof parks sometimes report "Clear" when roof is open
    # Only mark as dome if condition explicitly says dome/roof closed
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # 1. Load all games
    print("Loading games from Supabase ...", end=" ", flush=True)
    games = sb_fetch_all(
        "games",
        {
            "select": "game_pk,home_team_id,game_time_utc,park_id",
            "game_type": "eq.regular",
            "status": "eq.final",
            "order": "game_pk",
        },
    )
    print(f"{len(games)} games", flush=True)

    # 2. Load parks for dome info
    print("Loading parks ...", end=" ", flush=True)
    parks = sb_fetch_all("parks", {"select": "park_id,mlb_team_id,is_dome,is_retractable_roof"})
    park_map = {p['park_id']: p for p in parks}
    print(f"{len(parks)} parks", flush=True)

    # 3. Check which games already have weather
    print("Checking existing weather data ...", end=" ", flush=True)
    existing = sb_fetch_all("weather_snapshots", {"select": "game_pk", "order": "game_pk"})
    done_pks = set(r['game_pk'] for r in existing)
    print(f"{len(done_pks)} already seeded", flush=True)

    # 4. Filter
    todo = [g for g in games if g['game_pk'] not in done_pks]
    print(f"\nGames to process: {len(todo)}\n", flush=True)

    if not todo:
        print("Nothing to do!")
        return

    # Counters
    weather_count = 0
    dome_count = 0
    no_weather_count = 0
    error_count = 0
    buf = []

    for idx, game in enumerate(todo):
        game_pk = game['game_pk']
        park_id = game.get('park_id')
        park = park_map.get(park_id, {})

        # Fetch game feed
        try:
            r = requests.get(
                f"{MLB_BASE}/game/{game_pk}/feed/live",
                timeout=20,
            )
            r.raise_for_status()
            feed = r.json()
        except Exception as e:
            print(f"  ERR feed {game_pk}: {e}", flush=True)
            error_count += 1
            time.sleep(1)
            continue

        # Extract weather
        weather = feed.get('gameData', {}).get('weather', {})

        if not weather:
            no_weather_count += 1
            time.sleep(API_DELAY)
            continue

        condition = weather.get('condition', '') or ''
        temp_str = weather.get('temp', '')
        wind_str = weather.get('wind', '')

        # Parse temperature
        try:
            temp_f = float(temp_str) if temp_str else None
        except (ValueError, TypeError):
            temp_f = None

        # Check dome
        dome_closed = is_dome_game(
            condition,
            bool(park.get('is_dome')),
            bool(park.get('is_retractable_roof')),
        )

        if dome_closed:
            # Store dome row with minimal data
            buf.append({
                'game_pk': game_pk,
                'temperature_f': temp_f,
                'wind_speed_mph': 0,
                'wind_relative': 'calm',
                'is_dome_closed': True,
                'captured_at': game.get('game_time_utc'),
            })
            dome_count += 1
        else:
            # Parse wind
            wind_speed, wind_relative = parse_wind(wind_str)

            buf.append({
                'game_pk': game_pk,
                'temperature_f': temp_f,
                'wind_speed_mph': wind_speed,
                'wind_relative': wind_relative,
                'is_dome_closed': False,
                'captured_at': game.get('game_time_utc'),
            })

        weather_count += 1

        # Flush
        if (idx + 1) % FLUSH_EVERY == 0:
            sb_batch_post("weather_snapshots", buf)
            buf = []

        # Progress
        if (idx + 1) % PROGRESS_EVERY == 0:
            print(
                f"  [{idx + 1:>5}/{len(todo)}] "
                f"weather={weather_count}  domes={dome_count}  "
                f"no_data={no_weather_count}  errors={error_count}",
                flush=True,
            )

        time.sleep(API_DELAY)

    # Final flush
    sb_batch_post("weather_snapshots", buf)

    print(f"\n{'=' * 50}")
    print(f"  COMPLETE")
    print(f"  Weather rows inserted: {weather_count}")
    print(f"  Dome games:            {dome_count}")
    print(f"  No weather data:       {no_weather_count}")
    print(f"  API errors:            {error_count}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
