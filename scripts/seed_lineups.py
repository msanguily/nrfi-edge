#!/usr/bin/env python3
"""Seed lineups table with historical batting orders from MLB Stats API boxscores.

For each regular-season final game (2019-2025), fetches the boxscore to get
the confirmed batting order (positions 1-9) for both teams. Ensures all
players exist in the players table before inserting lineup rows.

Resumable: skips game_pks that already have lineups.
Uses ON CONFLICT (game_pk, team_id, batting_order) DO NOTHING.
"""

import requests
import time
import sys

SUPABASE_URL = "https://cdomrqoslgewamcqhbal.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNkb21ycW9zbGdld2FtY3FoYmFsIiwi"
    "cm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTAxMDIwNSwiZXhwIjoyMDkw"
    "NTg2MjA1fQ._sYGKhDp5LL-8G7ZxZm2xsjfQBuUh-L4-0TEwKatvvk"
)

MLB_BASE = "https://statsapi.mlb.com/api/v1"

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

INSERT_HEADERS = {
    **SB_HEADERS,
    "Prefer": "resolution=ignore-duplicates,return=minimal",
}

UPSERT_HEADERS = {
    **SB_HEADERS,
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

API_DELAY = 0.3  # seconds between MLB API calls
FLUSH_EVERY = 100  # flush DB buffers every N games
PROGRESS_EVERY = 500  # print progress every N games


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def sb_fetch_all(table, params):
    """Paginate through Supabase REST API to fetch all rows."""
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


def sb_batch_post(table, rows, headers, on_conflict=None):
    """Batch-insert rows 200 at a time."""
    if not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    for i in range(0, len(rows), 200):
        batch = rows[i : i + 200]
        for attempt in range(3):
            try:
                r = requests.post(url, headers=headers, json=batch, timeout=30)
                if r.status_code in (200, 201):
                    break
                if r.status_code == 409:
                    for row in batch:
                        requests.post(url, headers=headers, json=row, timeout=10)
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
# MLB API helpers
# ---------------------------------------------------------------------------
def fetch_boxscore(game_pk):
    """Fetch boxscore from MLB Stats API."""
    r = requests.get(f"{MLB_BASE}/game/{game_pk}/boxscore", timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_player_info(player_id):
    """Fetch player info from MLB Stats API people endpoint."""
    r = requests.get(f"{MLB_BASE}/people/{player_id}", timeout=10)
    r.raise_for_status()
    people = r.json().get("people", [])
    if not people:
        return None
    p = people[0]
    return {
        "mlb_player_id": p["id"],
        "name": p.get("fullFMLName", p.get("fullName", "Unknown")),
        "position": p.get("primaryPosition", {}).get("abbreviation"),
        "bats": p.get("batSide", {}).get("code"),
        "throws": p.get("pitchHand", {}).get("code"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # 1. Load all games
    print("Loading games from Supabase ...", end=" ", flush=True)
    games = sb_fetch_all(
        "games",
        {
            "select": "game_pk,home_team_id,away_team_id,game_time_utc",
            "game_type": "eq.regular",
            "status": "eq.final",
            "order": "game_pk",
        },
    )
    print(f"{len(games)} games", flush=True)

    # 2. Load game_pks already in lineups (for resumability)
    print("Checking existing lineups ...", end=" ", flush=True)
    done_rows = sb_fetch_all("lineups", {"select": "game_pk", "order": "game_pk"})
    done_pks = set(row["game_pk"] for row in done_rows)
    print(f"{len(done_pks)} game_pks already seeded", flush=True)

    # 3. Load existing player IDs
    print("Loading existing player IDs ...", end=" ", flush=True)
    player_rows = sb_fetch_all("players", {"select": "mlb_player_id", "order": "mlb_player_id"})
    known_players = set(row["mlb_player_id"] for row in player_rows)
    print(f"{len(known_players)} players", flush=True)

    # 4. Filter to games that need processing
    todo = [g for g in games if g["game_pk"] not in done_pks]
    print(f"\nGames to process: {len(todo)}\n", flush=True)

    if not todo:
        print("Nothing to do!")
        return

    # Counters
    lineup_count = 0
    new_player_count = 0
    skipped_teams = 0
    error_count = 0

    # Buffers
    player_buf = []
    lineup_buf = []

    def flush_buffers():
        nonlocal player_buf, lineup_buf
        # Players MUST be flushed before lineups (FK constraint)
        if player_buf:
            sb_batch_post("players", player_buf, UPSERT_HEADERS)
            player_buf = []
        if lineup_buf:
            sb_batch_post(
                "lineups", lineup_buf, INSERT_HEADERS,
                on_conflict="game_pk,team_id,batting_order",
            )
            lineup_buf = []

    for idx, game in enumerate(todo):
        game_pk = game["game_pk"]

        # Fetch boxscore
        try:
            box = fetch_boxscore(game_pk)
        except Exception as e:
            print(f"  ERR boxscore {game_pk}: {e}", flush=True)
            error_count += 1
            time.sleep(1)
            continue

        # Process both teams
        for side, team_key in [("away", "away_team_id"), ("home", "home_team_id")]:
            team_id = game[team_key]
            team_data = box.get("teams", {}).get(side, {})
            batting_order = team_data.get("battingOrder", [])

            if not batting_order or len(batting_order) < 9:
                skipped_teams += 1
                if batting_order:
                    print(
                        f"  WARN {game_pk} {side}: {len(batting_order)} batters (< 9), skipping",
                        flush=True,
                    )
                continue

            # Ensure all 9 batters exist in players table
            team_players = team_data.get("players", {})
            for pid in batting_order[:9]:
                if pid in known_players:
                    continue

                # Try to extract info from boxscore player data first
                pkey = f"ID{pid}"
                pinfo = team_players.get(pkey, {})
                person = pinfo.get("person", {})

                if person:
                    player_buf.append({
                        "mlb_player_id": pid,
                        "name": person.get("fullName", "Unknown"),
                        "position": pinfo.get("position", {}).get("abbreviation"),
                        "bats": person.get("batSide", {}).get("code"),
                        "throws": person.get("pitchHand", {}).get("code"),
                    })
                else:
                    # Fall back to people endpoint
                    try:
                        fetched = fetch_player_info(pid)
                        if fetched:
                            player_buf.append(fetched)
                        else:
                            player_buf.append({
                                "mlb_player_id": pid,
                                "name": "Unknown",
                            })
                        time.sleep(API_DELAY)
                    except Exception as e:
                        print(f"  ERR player {pid}: {e}", flush=True)
                        player_buf.append({
                            "mlb_player_id": pid,
                            "name": "Unknown",
                        })

                known_players.add(pid)
                new_player_count += 1

            # Build lineup rows
            for pos, pid in enumerate(batting_order[:9], start=1):
                lineup_buf.append({
                    "game_pk": game_pk,
                    "team_id": team_id,
                    "batting_order": pos,
                    "mlb_player_id": pid,
                    "confirmed_at": game["game_time_utc"],
                })
            lineup_count += 9

        # Flush buffers periodically
        if (idx + 1) % FLUSH_EVERY == 0:
            flush_buffers()

        # Progress
        if (idx + 1) % PROGRESS_EVERY == 0:
            print(
                f"  [{idx + 1:>5}/{len(todo)}] "
                f"lineups={lineup_count}  new_players={new_player_count}  "
                f"skipped={skipped_teams}  errors={error_count}",
                flush=True,
            )

        time.sleep(API_DELAY)

    # Final flush
    flush_buffers()

    print(f"\n{'=' * 50}")
    print(f"  COMPLETE")
    print(f"  Lineup rows inserted:  {lineup_count}")
    print(f"  New players added:     {new_player_count}")
    print(f"  Skipped teams:         {skipped_teams}")
    print(f"  API errors:            {error_count}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
