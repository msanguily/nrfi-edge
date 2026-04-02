#!/usr/bin/env python3
"""Seed sprint_speed into players table from Statcast sprint speed leaderboards.

For each player, stores their most recent season's sprint speed.
Uses pybaseball's statcast_sprint_speed function (Baseball Savant data).
Players without Statcast sprint speed data retain NULL (defaults to league avg in pipeline).
"""

import os
import sys
import warnings
import requests
import time

warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from pybaseball import statcast_sprint_speed

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def main():
    print("=" * 60)
    print("  Seed Sprint Speed Data")
    print("=" * 60)

    # Fetch sprint speed for each season, keep most recent per player
    player_speeds = {}  # mlb_player_id -> (sprint_speed, season)

    for season in range(2019, 2026):
        print(f"\n  Fetching {season} sprint speed data...")
        try:
            df = statcast_sprint_speed(season, min_opp=5)
            count = 0
            for _, row in df.iterrows():
                pid = int(row['player_id'])
                speed = float(row['sprint_speed'])
                # Keep most recent season's speed
                if pid not in player_speeds or season > player_speeds[pid][1]:
                    player_speeds[pid] = (speed, season)
                    count += 1
            print(f"  {season}: {len(df)} players, {count} updated")
        except Exception as e:
            print(f"  {season}: Error - {e}")
        time.sleep(1)  # Rate limiting

    print(f"\n  Total unique players with sprint speed: {len(player_speeds)}")

    # Update players table
    print("\n  Updating players table...")
    updated = 0
    skipped = 0

    for pid, (speed, season) in player_speeds.items():
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/players",
            headers=SB_HEADERS,
            params={"mlb_player_id": f"eq.{pid}"},
            json={"sprint_speed": round(speed, 1)},
            timeout=10,
        )
        if r.status_code in (200, 204):
            updated += 1
        else:
            skipped += 1

        if (updated + skipped) % 200 == 0:
            print(f"    Processed {updated + skipped}/{len(player_speeds)} "
                  f"({updated} updated, {skipped} skipped)")

    print(f"\n  Done: {updated} players updated, {skipped} skipped")

    # Verify
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/players",
        headers={**SB_HEADERS, "Prefer": ""},
        params={"select": "count", "sprint_speed": "not.is.null"},
        timeout=10,
    )
    print(f"  Players with sprint_speed in DB: {r.json()}")


if __name__ == '__main__':
    main()
