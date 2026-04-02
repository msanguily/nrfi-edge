#!/usr/bin/env python3
"""Seed games table with MLB regular season games 2019-2025."""

import os
import requests
import time
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

MLB_BASE = "https://statsapi.mlb.com/api/v1"

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=ignore-duplicates,return=minimal",
}

SEASONS = [
    (2019, date(2019, 3, 20), date(2019, 9, 29)),
    (2020, date(2020, 7, 23), date(2020, 9, 27)),
    (2021, date(2021, 4, 1), date(2021, 10, 3)),
    (2022, date(2022, 4, 7), date(2022, 10, 5)),
    (2023, date(2023, 3, 30), date(2023, 10, 1)),
    (2024, date(2024, 3, 20), date(2024, 9, 29)),
    (2025, date(2025, 3, 27), date(2025, 9, 28)),
]


def get_park_map():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/parks?select=park_id,mlb_team_id",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    r.raise_for_status()
    return {p["mlb_team_id"]: p["park_id"] for p in r.json()}


def sb_post(table, rows):
    """Batch-insert rows via Supabase REST API, ignoring duplicates."""
    if not rows:
        return
    for i in range(0, len(rows), 200):
        batch = rows[i : i + 200]
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{SUPABASE_URL}/rest/v1/{table}",
                    headers=SB_HEADERS,
                    json=batch,
                    timeout=30,
                )
                if r.status_code in (200, 201):
                    break
                # 409 can happen if PostgREST doesn't support batch ignore;
                # fall back to row-by-row
                if r.status_code == 409:
                    for row in batch:
                        requests.post(
                            f"{SUPABASE_URL}/rest/v1/{table}",
                            headers=SB_HEADERS,
                            json=row,
                            timeout=10,
                        )
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


def fetch_schedule(start, end):
    """Fetch MLB schedule with linescore + probablePitcher for a date range."""
    r = requests.get(
        f"{MLB_BASE}/schedule",
        params={
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "linescore,probablePitcher",
            "gameType": "R",
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def main():
    park_map = get_park_map()
    print(f"Park mapping loaded for {len(park_map)} teams")

    known_pids = set()
    grand_total = 0
    season_summary = []

    for year, s_start, s_end in SEASONS:
        print(f"\n{'='*50}")
        print(f"  SEASON {year}  ({s_start} to {s_end})")
        print(f"{'='*50}")

        pitchers = []
        games = []

        cur = s_start
        while cur <= s_end:
            chunk_end = min(cur + timedelta(days=13), s_end)
            try:
                data = fetch_schedule(cur, chunk_end)
            except Exception as e:
                print(f"  Fetch error {cur}-{chunk_end}: {e}", flush=True)
                cur = chunk_end + timedelta(days=1)
                time.sleep(2)
                continue

            for d in data.get("dates", []):
                for g in d.get("games", []):
                    if g.get("gameType") != "R":
                        continue
                    if g.get("status", {}).get("detailedState") != "Final":
                        continue

                    ht = g["teams"]["home"]
                    at = g["teams"]["away"]
                    ht_id = ht["team"]["id"]
                    at_id = at["team"]["id"]

                    hp = ht.get("probablePitcher")
                    ap = at.get("probablePitcher")
                    hp_id = hp["id"] if hp else None
                    ap_id = ap["id"] if ap else None

                    # Collect new pitchers
                    for p, tid in [(hp, ht_id), (ap, at_id)]:
                        if p and p["id"] not in known_pids:
                            known_pids.add(p["id"])
                            pitchers.append(
                                {
                                    "mlb_player_id": p["id"],
                                    "name": p.get("fullName", "Unknown"),
                                    "position": "P",
                                    "current_team_id": tid,
                                }
                            )

                    # First-inning scoring
                    innings = g.get("linescore", {}).get("innings", [])
                    fi_a = fi_h = None
                    nrfi = None
                    if innings:
                        fi = innings[0]
                        fi_a = fi.get("away", {}).get("runs")
                        fi_h = fi.get("home", {}).get("runs")
                        if fi_a is not None and fi_h is not None:
                            nrfi = fi_a == 0 and fi_h == 0

                    games.append(
                        {
                            "game_pk": g["gamePk"],
                            "game_date": g.get("officialDate"),
                            "game_type": "regular",
                            "game_time_utc": g.get("gameDate"),
                            "status": "final",
                            "home_team_id": ht_id,
                            "away_team_id": at_id,
                            "home_pitcher_id": hp_id,
                            "away_pitcher_id": ap_id,
                            "park_id": park_map.get(ht_id),
                            "is_day_game": g.get("dayNight") == "day",
                            "first_inn_away_runs": fi_a,
                            "first_inn_home_runs": fi_h,
                            "nrfi_result": nrfi,
                        }
                    )

            print(
                f"  {chunk_end}  games={len(games):>5}  pitchers={len(pitchers):>4}",
                flush=True,
            )
            cur = chunk_end + timedelta(days=1)
            time.sleep(0.5)

        # --- Insert pitchers (FK dependency) ---
        print(f"  Inserting {len(pitchers)} pitchers ...", end=" ", flush=True)
        sb_post("players", pitchers)
        print("done", flush=True)

        # --- Insert games ---
        print(f"  Inserting {len(games)} games ...", end=" ", flush=True)
        sb_post("games", games)
        print("done", flush=True)

        # --- Season stats ---
        scored = [x for x in games if x["nrfi_result"] is not None]
        nrfis = sum(1 for x in scored if x["nrfi_result"])
        rate = nrfis / len(scored) if scored else 0
        print(f"  >> {year}: {len(games)} games | NRFI {nrfis}/{len(scored)} = {rate:.3f}")
        season_summary.append((year, len(games), nrfis, len(scored), rate))
        grand_total += len(games)

    # --- Final summary ---
    print(f"\n{'='*50}")
    print(f"  SUMMARY")
    print(f"{'='*50}")
    for yr, total, nrfi_ct, scored_ct, rate in season_summary:
        print(f"  {yr}: {total:>5} games | NRFI {rate:.3f} ({nrfi_ct}/{scored_ct})")
    print(f"  {'':->45}")
    print(f"  TOTAL: {grand_total} games")


if __name__ == "__main__":
    main()
