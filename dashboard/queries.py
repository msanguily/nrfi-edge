"""All Supabase queries for the NRFI dashboard. Every function returns empty list/dict on no data."""

import json
import os
from datetime import date, datetime
from pathlib import Path

import streamlit as st


@st.cache_resource
def get_supabase():
    from dotenv import load_dotenv
    load_dotenv()
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))
    return create_client(url, key)


def _paginated_select(query, page_size=1000):
    """Fetch all rows from a Supabase query, paginating past the 1000-row default limit."""
    all_rows = []
    offset = 0
    while True:
        res = query.range(offset, offset + page_size - 1).execute()
        batch = res.data or []
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return all_rows


def _batched_in(sb, table, select_cols, column, values, extra_filters=None, page_size=500):
    """Query with .in_() in batches to avoid URI-length limits, with full pagination per batch."""
    all_rows = []
    for i in range(0, len(values), page_size):
        batch = values[i:i + page_size]
        q = sb.table(table).select(select_cols).in_(column, batch)
        if extra_filters:
            for method, args in extra_filters:
                q = getattr(q, method)(*args)
        all_rows.extend(_paginated_select(q))
    return all_rows


def get_most_recent_prediction_date() -> date:
    """Get the most recent game_date that has predictions.

    Looks at the actual game date (not created_at) to find the latest day
    with predictions, so re-evaluations of old games don't skew the result.
    """
    try:
        sb = get_supabase()
        # Join predictions to games and find the max game_date
        res = (
            sb.table("games")
            .select("game_date")
            .order("game_date", desc=True)
            .limit(50)
            .execute()
        )
        if not res.data:
            return None
        # Check each date for predictions (most recent first)
        for row in res.data:
            gd = row["game_date"]
            games_on_date = (
                sb.table("games")
                .select("game_pk")
                .eq("game_date", gd)
                .execute()
            )
            if not games_on_date.data:
                continue
            pks = [g["game_pk"] for g in games_on_date.data]
            pred_check = (
                sb.table("predictions")
                .select("game_pk")
                .in_("game_pk", pks[:20])
                .limit(1)
                .execute()
            )
            if pred_check.data:
                return date.fromisoformat(gd)
        return None
    except Exception:
        return None


def get_data_status() -> dict:
    """Row counts and latest timestamps for all key tables."""
    try:
        sb = get_supabase()
        status = {}

        # Predictions
        res = sb.table("predictions").select("id", count="exact").limit(1).execute()
        status["predictions_count"] = res.count or 0

        # Latest prediction
        res = sb.table("predictions").select("created_at").order("created_at", desc=True).limit(1).execute()
        status["predictions_latest"] = res.data[0]["created_at"] if res.data else None

        # Games
        res = sb.table("games").select("game_pk", count="exact").limit(1).execute()
        status["games_count"] = res.count or 0

        # Odds
        res = sb.table("odds").select("id", count="exact").limit(1).execute()
        status["odds_count"] = res.count or 0

        # Latest odds
        res = sb.table("odds").select("captured_at").order("captured_at", desc=True).limit(1).execute()
        status["odds_latest"] = res.data[0]["captured_at"] if res.data else None

        # Weather
        res = sb.table("weather_snapshots").select("id", count="exact").limit(1).execute()
        status["weather_count"] = res.count or 0

        # Pitcher stats
        res = sb.table("pitcher_stats").select("id", count="exact").limit(1).execute()
        status["pitcher_stats_count"] = res.count or 0

        # Model version
        res = sb.table("predictions").select("model_version").order("created_at", desc=True).limit(1).execute()
        status["model_version"] = res.data[0]["model_version"] if res.data else "unknown"

        return status
    except Exception as e:
        return {"error": str(e)}


def get_todays_predictions(target_date: date) -> list:
    """Get predictions for a specific date, joined with game/team/pitcher info."""
    try:
        sb = get_supabase()
        date_str = target_date.isoformat()

        # Get games for the date (single day, always <20 rows)
        games_res = sb.table("games").select("*").eq("game_date", date_str).execute()
        if not games_res.data:
            return []

        game_pks = [g["game_pk"] for g in games_res.data]
        games_by_pk = {g["game_pk"]: g for g in games_res.data}

        # Get predictions for those games (may be empty if pipeline hasn't run yet)
        preds_data = _batched_in(sb, "predictions", "*", "game_pk", game_pks)
        preds_by_pk = {}
        for pred in preds_data:
            preds_by_pk[pred["game_pk"]] = pred

        # Get team names, player names, parks
        team_ids = set()
        pitcher_ids = set()
        park_ids = set()
        for g in games_res.data:
            team_ids.add(g["home_team_id"])
            team_ids.add(g["away_team_id"])
            if g.get("home_pitcher_id"):
                pitcher_ids.add(g["home_pitcher_id"])
            if g.get("away_pitcher_id"):
                pitcher_ids.add(g["away_pitcher_id"])
            if g.get("park_id"):
                park_ids.add(g["park_id"])

        teams = {}
        if team_ids:
            res = sb.table("teams").select("mlb_team_id, abbreviation, name").in_(
                "mlb_team_id", list(team_ids)
            ).execute()
            teams = {t["mlb_team_id"]: t for t in (res.data or [])}

        players = {}
        if pitcher_ids:
            res = sb.table("players").select("mlb_player_id, name").in_(
                "mlb_player_id", list(pitcher_ids)
            ).execute()
            players = {p["mlb_player_id"]: p for p in (res.data or [])}

        parks = {}
        if park_ids:
            res = sb.table("parks").select("*").in_("park_id", list(park_ids)).execute()
            parks = {p["park_id"]: p for p in (res.data or [])}

        # Combine — iterate over games so we always show the full slate,
        # even before the prediction pipeline has run.
        results = []
        for game in games_res.data:
            pred = preds_by_pk.get(game["game_pk"], {})
            away_team = teams.get(game.get("away_team_id"), {})
            home_team = teams.get(game.get("home_team_id"), {})
            away_pitcher = players.get(game.get("away_pitcher_id"), {})
            home_pitcher = players.get(game.get("home_pitcher_id"), {})
            park = parks.get(game.get("park_id"), {})

            results.append({
                **pred,
                "game_pk": game["game_pk"],
                "game_date": game.get("game_date"),
                "game_time_utc": game.get("game_time_utc"),
                "status": game.get("status"),
                "nrfi_result": game.get("nrfi_result"),
                "away_team": away_team.get("abbreviation", "???"),
                "away_team_name": away_team.get("name", ""),
                "home_team": home_team.get("abbreviation", "???"),
                "home_team_name": home_team.get("name", ""),
                "away_pitcher_name": away_pitcher.get("name", "TBD"),
                "away_pitcher_id": game.get("away_pitcher_id"),
                "home_pitcher_name": home_pitcher.get("name", "TBD"),
                "home_pitcher_id": game.get("home_pitcher_id"),
                "park_name": park.get("name", ""),
                "park_hr_factor": park.get("hr_factor"),
                "park_elevation": park.get("elevation_feet"),
                "game_total": game.get("game_total"),
                "first_inn_away_runs": game.get("first_inn_away_runs"),
                "first_inn_home_runs": game.get("first_inn_home_runs"),
            })

        results.sort(key=lambda x: x.get("game_time_utc") or "")
        return results
    except Exception as e:
        st.error(f"Query error (today's predictions): {e}")
        return []


def get_todays_odds(target_date: date) -> list:
    """Get odds for games on a specific date."""
    try:
        sb = get_supabase()
        date_str = target_date.isoformat()

        games_res = sb.table("games").select("game_pk").eq("game_date", date_str).execute()
        if not games_res.data:
            return []

        game_pks = [g["game_pk"] for g in games_res.data]
        return _batched_in(sb, "odds", "*", "game_pk", game_pks)
    except Exception:
        return []


def get_prediction_history(
    start_date: date = None,
    end_date: date = None,
    min_edge: float = None,
    result_filter: str = "All",
    prediction_type: str = None,
    limit: int = None,
) -> list:
    """Get historical predictions with game info, applying filters."""
    try:
        sb = get_supabase()

        # Build games query with pagination
        q = sb.table("games").select("game_pk, game_date, game_time_utc, status, nrfi_result, "
                                     "home_team_id, away_team_id, home_pitcher_id, away_pitcher_id")
        if start_date:
            q = q.gte("game_date", start_date.isoformat())
        if end_date:
            q = q.lte("game_date", end_date.isoformat())
        q = q.order("game_date", desc=True)

        games_data = _paginated_select(q)
        if not games_data:
            return []

        game_pks = [g["game_pk"] for g in games_data]
        games_by_pk = {g["game_pk"]: g for g in games_data}

        # Batch predictions with pagination
        extra_filters = []
        if prediction_type:
            extra_filters.append(("eq", ("prediction_type", prediction_type)))
        all_preds = _batched_in(sb, "predictions", "*", "game_pk", game_pks,
                                extra_filters=extra_filters or None)

        # Get teams and players (always <30 teams, <2000 pitchers)
        team_ids = set()
        pitcher_ids = set()
        for g in games_data:
            team_ids.add(g["home_team_id"])
            team_ids.add(g["away_team_id"])
            if g.get("home_pitcher_id"):
                pitcher_ids.add(g["home_pitcher_id"])
            if g.get("away_pitcher_id"):
                pitcher_ids.add(g["away_pitcher_id"])

        teams = {}
        if team_ids:
            res = sb.table("teams").select("mlb_team_id, abbreviation").in_(
                "mlb_team_id", list(team_ids)
            ).execute()
            teams = {t["mlb_team_id"]: t for t in (res.data or [])}

        players = {}
        if pitcher_ids:
            players_data = _batched_in(sb, "players", "mlb_player_id, name",
                                       "mlb_player_id", list(pitcher_ids))
            players = {p["mlb_player_id"]: p for p in players_data}

        results = []
        for pred in all_preds:
            game = games_by_pk.get(pred["game_pk"], {})
            edge = float(pred["edge"]) if pred.get("edge") is not None else None

            # Apply filters
            if min_edge is not None and (edge is None or edge < min_edge):
                continue
            if result_filter == "Wins" and pred.get("result") is not True:
                continue
            if result_filter == "Losses" and pred.get("result") is not False:
                continue
            if result_filter == "Pending" and pred.get("result") is not None:
                continue

            away_team = teams.get(game.get("away_team_id"), {})
            home_team = teams.get(game.get("home_team_id"), {})
            away_pitcher = players.get(game.get("away_pitcher_id"), {})
            home_pitcher = players.get(game.get("home_pitcher_id"), {})

            results.append({
                **pred,
                "game_date": game.get("game_date"),
                "game_time_utc": game.get("game_time_utc"),
                "status": game.get("status"),
                "nrfi_result": game.get("nrfi_result"),
                "away_team": away_team.get("abbreviation", "???"),
                "home_team": home_team.get("abbreviation", "???"),
                "away_pitcher_name": away_pitcher.get("name", "TBD"),
                "home_pitcher_name": home_pitcher.get("name", "TBD"),
            })

        results.sort(key=lambda x: x.get("game_date") or "", reverse=True)
        if limit:
            results = results[:limit]
        return results
    except Exception as e:
        st.error(f"Query error (history): {e}")
        return []


def get_season_stats(season: int = None) -> dict:
    """Aggregate stats for a season (or all time). Uses predictions with bet_recommended=True."""
    try:
        sb = get_supabase()

        if season:
            games_data = _paginated_select(
                sb.table("games").select("game_pk").gte(
                    "game_date", f"{season}-01-01"
                ).lte("game_date", f"{season}-12-31")
            )
            if not games_data:
                return {"total_bets": 0}
            game_pks = [g["game_pk"] for g in games_data]
            bets = _batched_in(sb, "predictions", "*", "game_pk", game_pks,
                               extra_filters=[("eq", ("bet_recommended", True))])
        else:
            bets = _paginated_select(
                sb.table("predictions").select("*").eq(
                    "bet_recommended", True
                ).order("created_at", desc=True)
            )

        if not bets:
            return {"total_bets": 0}

        from .calculations import calculate_profit

        wins = sum(1 for b in bets if b.get("result") is True)
        losses = sum(1 for b in bets if b.get("result") is False)
        pending = sum(1 for b in bets if b.get("result") is None)

        total_pl = 0.0
        total_wagered = 0.0
        edges = []
        clvs = []
        results_list = []

        for b in bets:
            units = float(b["bet_size_units"]) if b.get("bet_size_units") else 1.0
            if b.get("result") is not None and b.get("best_nrfi_price"):
                odds = int(b["best_nrfi_price"])
                pl = calculate_profit(odds, units, b["result"])
                total_pl += pl
                total_wagered += units
                results_list.append(b["result"])
            if b.get("edge") is not None:
                edges.append(float(b["edge"]))
            if b.get("clv") is not None:
                clvs.append(float(b["clv"]))

        return {
            "total_bets": len(bets),
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "total_pl": total_pl,
            "total_wagered": total_wagered,
            "roi": (total_pl / total_wagered * 100) if total_wagered > 0 else 0.0,
            "avg_edge": sum(edges) / len(edges) if edges else 0.0,
            "avg_clv": sum(clvs) / len(clvs) if clvs else 0.0,
            "clv_beat_rate": (sum(1 for c in clvs if c > 0) / len(clvs) * 100) if clvs else 0.0,
            "results_list": results_list,
        }
    except Exception as e:
        st.error(f"Query error (season stats): {e}")
        return {"total_bets": 0}


def get_pitcher_nrfi_rate(mlb_player_id: int, season: int = None) -> dict:
    """Get pitcher's first-inning NRFI rate. Aggregates across seasons if no season specified."""
    try:
        sb = get_supabase()
        q = sb.table("pitcher_stats").select(
            "season, first_inn_starts, first_inn_scoreless"
        ).eq("mlb_player_id", mlb_player_id)
        if season:
            q = q.eq("season", season)
        res = q.execute()
        if not res.data:
            return {}

        total_starts = sum(r["first_inn_starts"] or 0 for r in res.data)
        total_scoreless = sum(r["first_inn_scoreless"] or 0 for r in res.data)
        if total_starts == 0:
            return {}

        return {
            "first_inn_starts": total_starts,
            "first_inn_scoreless": total_scoreless,
            "nrfi_rate": total_scoreless / total_starts,
        }
    except Exception:
        return {}


def get_backtest_results() -> dict:
    """Load backtest results from config/backtest_results.json."""
    try:
        path = Path(__file__).parent.parent / "config" / "backtest_results.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}
    except Exception:
        return {}


def get_game_weather(game_pk: int) -> dict:
    """Get weather for a specific game."""
    try:
        sb = get_supabase()
        res = sb.table("weather_snapshots").select("*").eq("game_pk", game_pk).limit(1).execute()
        return res.data[0] if res.data else {}
    except Exception:
        return {}


def get_weather_batch(game_pks: list) -> dict:
    """Get weather for multiple games. Returns {game_pk: weather_dict}."""
    if not game_pks:
        return {}
    try:
        sb = get_supabase()
        rows = _batched_in(sb, "weather_snapshots", "*", "game_pk", game_pks)
        result = {}
        for r in rows:
            # Keep the most recent snapshot per game
            gpk = r["game_pk"]
            if gpk not in result or (r.get("captured_at") or "") > (result[gpk].get("captured_at") or ""):
                result[gpk] = r
        return result
    except Exception:
        return {}


def get_bookmaker_performance() -> list:
    """Performance breakdown by bookmaker from predictions where bet was placed."""
    try:
        sb = get_supabase()
        rows = _paginated_select(
            sb.table("predictions").select(
                "best_book, best_nrfi_price, result, bet_size_units, clv, edge"
            ).eq("bet_recommended", True).not_.is_("best_book", "null")
        )

        if not rows:
            return []

        from .calculations import calculate_profit

        books = {}
        for row in rows:
            book = row["best_book"]
            if book not in books:
                books[book] = {"book": book, "count": 0, "wins": 0, "losses": 0,
                               "pl": 0.0, "clvs": []}
            books[book]["count"] += 1
            if row.get("result") is not None:
                units = float(row["bet_size_units"]) if row.get("bet_size_units") else 1.0
                odds = int(row["best_nrfi_price"]) if row.get("best_nrfi_price") else -110
                pl = calculate_profit(odds, units, row["result"])
                books[book]["pl"] += pl
                if row["result"]:
                    books[book]["wins"] += 1
                else:
                    books[book]["losses"] += 1
            if row.get("clv") is not None:
                books[book]["clvs"].append(float(row["clv"]))

        result = []
        for b in books.values():
            total_decided = b["wins"] + b["losses"]
            avg_clv = sum(b["clvs"]) / len(b["clvs"]) if b["clvs"] else 0.0
            result.append({
                "book": b["book"],
                "times_best": b["count"],
                "win_rate": (b["wins"] / total_decided * 100) if total_decided > 0 else 0.0,
                "pl": b["pl"],
                "avg_clv": avg_clv,
            })

        result.sort(key=lambda x: x["pl"], reverse=True)
        return result
    except Exception:
        return []


def get_daily_pl(start_date: date = None, end_date: date = None) -> list:
    """Daily P/L for profit calendar. Returns [{date, pl, bets, wins, losses, expected_pl}]."""
    try:
        sb = get_supabase()
        bets = _paginated_select(
            sb.table("predictions").select(
                "game_pk, best_nrfi_price, bet_size_units, result, edge"
            ).eq("bet_recommended", True)
        )
        if not bets:
            return []

        # Get game dates for these predictions
        game_pks = list(set(r["game_pk"] for r in bets))
        games = {}
        games_data = _batched_in(sb, "games", "game_pk, game_date", "game_pk", game_pks)
        for g in games_data:
            games[g["game_pk"]] = g["game_date"]

        from .calculations import calculate_profit

        daily = {}
        for row in bets:
            gdate = games.get(row["game_pk"])
            if not gdate:
                continue
            if start_date and gdate < start_date.isoformat():
                continue
            if end_date and gdate > end_date.isoformat():
                continue

            if gdate not in daily:
                daily[gdate] = {"date": gdate, "pl": 0.0, "bets": 0, "wins": 0,
                                "losses": 0, "expected_pl": 0.0, "wagered": 0.0}
            daily[gdate]["bets"] += 1

            # Accumulate expected P/L from edge * units
            if row.get("edge") is not None and row.get("bet_size_units") is not None:
                daily[gdate]["expected_pl"] += float(row["edge"]) * float(row["bet_size_units"])

            if row.get("result") is not None:
                units = float(row["bet_size_units"]) if row.get("bet_size_units") else 1.0
                odds = int(row["best_nrfi_price"]) if row.get("best_nrfi_price") else -110
                pl = calculate_profit(odds, units, row["result"])
                daily[gdate]["pl"] += pl
                daily[gdate]["wagered"] += units
                if row["result"]:
                    daily[gdate]["wins"] += 1
                else:
                    daily[gdate]["losses"] += 1

        result = sorted(daily.values(), key=lambda x: x["date"])
        return result
    except Exception:
        return []


def get_all_backtest_predictions() -> list:
    """Get ALL backtest predictions for calibration analysis. Paginates fully.
    Includes game_date for time-series analysis (rolling accuracy)."""
    try:
        sb = get_supabase()
        preds = _paginated_select(
            sb.table("predictions").select(
                "game_pk, p_nrfi_combined, p_nrfi_calibrated, result"
            ).not_.is_("result", "null")
        )
        if not preds:
            return []

        # Fetch game dates in batches
        game_pks = list(set(p["game_pk"] for p in preds))
        games = {}
        games_data = _batched_in(sb, "games", "game_pk, game_date", "game_pk", game_pks)
        for g in games_data:
            games[g["game_pk"]] = g["game_date"]

        for p in preds:
            p["game_date"] = games.get(p["game_pk"])

        # Sort by date for time-series use
        preds.sort(key=lambda x: x.get("game_date") or "")
        return preds
    except Exception:
        return []
