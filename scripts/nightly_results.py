#!/usr/bin/env python3
"""Grade yesterday's predictions, compute P/L, track CLV.

cron: 0 2 * * * (2:00 AM ET daily)
"""

import sys
import time
import traceback

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.utils import get_yesterday_et, setup_logging, get_supabase_client
from src.data.mlb_api import get_game_linescore
from src.data.odds_api import fetch_nrfi_odds, match_to_game_pk
from src.betting.edge import american_to_decimal, decimal_to_implied

logger = setup_logging("nightly_results").getChild("nightly_results")


def get_ungraded_games(db, yesterday_str):
    """Fetch games that need grading: yesterday's games + any older unfinished games."""
    # Yesterday's games
    yesterday_resp = (
        db.table("games")
        .select("game_pk, game_date, status")
        .eq("game_date", yesterday_str)
        .execute()
    )

    # Also find older games that aren't final yet (rain delays, suspended)
    older_resp = (
        db.table("games")
        .select("game_pk, game_date, status")
        .lt("game_date", yesterday_str)
        .neq("status", "final")
        .neq("status", "postponed")
        .neq("status", "cancelled")
        .execute()
    )

    all_games = (yesterday_resp.data or []) + (older_resp.data or [])
    # Only grade games that aren't already final
    return [g for g in all_games if g.get("status") != "final"]


def grade_game(db, game_pk):
    """Fetch first-inning results and update the games table.

    Returns (nrfi_result, away_runs, home_runs) or None if game not complete.
    """
    linescore = get_game_linescore(game_pk)
    if linescore is None:
        return None

    away_runs = linescore["away_first_inning_runs"]
    home_runs = linescore["home_first_inning_runs"]
    nrfi = linescore["nrfi"]

    db.table("games").update({
        "first_inn_away_runs": away_runs,
        "first_inn_home_runs": home_runs,
        "nrfi_result": nrfi,
        "status": "final",
    }).eq("game_pk", game_pk).execute()

    return nrfi, away_runs, home_runs


def grade_predictions(db, game_pk, nrfi_result):
    """Update all predictions for a game with the actual result."""
    resp = (
        db.table("predictions")
        .select("id, game_pk, prediction_type")
        .eq("game_pk", game_pk)
        .execute()
    )
    if not resp.data:
        return 0

    count = 0
    for pred in resp.data:
        db.table("predictions").update({
            "result": nrfi_result,
        }).eq("id", pred["id"]).execute()
        count += 1

    return count


def compute_clv(db, closing_odds_by_game):
    """Compute CLV for recommended bets using closing odds.

    CLV = closing_implied_prob - bet_implied_prob
    Positive CLV means we got a better price than the closing line.
    """
    updated = 0

    for game_pk, close_fair_odds in closing_odds_by_game.items():
        if close_fair_odds is None:
            continue

        # Get predictions with recommended bets for this game
        resp = (
            db.table("predictions")
            .select("id, best_nrfi_price, implied_prob_best, bet_recommended")
            .eq("game_pk", game_pk)
            .eq("bet_recommended", True)
            .execute()
        )
        if not resp.data:
            continue

        # Closing implied probability (from fair/vig-removed closing odds)
        try:
            close_dec = american_to_decimal(close_fair_odds)
            close_implied = decimal_to_implied(close_dec)
        except (ValueError, ZeroDivisionError):
            continue

        for pred in resp.data:
            bet_implied = float(pred["implied_prob_best"]) if pred.get("implied_prob_best") else None
            if bet_implied is None:
                continue

            clv = close_implied - bet_implied
            db.table("predictions").update({"clv": round(clv, 4)}).eq("id", pred["id"]).execute()
            updated += 1

    return updated


def calculate_daily_pl(db, game_pks):
    """Calculate P/L summary for recommended bets on given games."""
    if not game_pks:
        return {"bets": 0, "wins": 0, "losses": 0, "pl_units": 0.0, "avg_clv": None}

    resp = (
        db.table("predictions")
        .select("*")
        .in_("game_pk", game_pks)
        .eq("bet_recommended", True)
        .execute()
    )
    if not resp.data:
        return {"bets": 0, "wins": 0, "losses": 0, "pl_units": 0.0, "avg_clv": None}

    wins = 0
    losses = 0
    pl_units = 0.0
    clv_values = []
    best_win = None
    worst_loss = None

    for pred in resp.data:
        result = pred.get("result")
        if result is None:
            continue

        price = pred.get("best_nrfi_price")
        units = float(pred.get("bet_size_units") or pred.get("kelly_fraction") or 1.0)
        edge = float(pred["edge"]) if pred.get("edge") is not None else 0

        if result:
            wins += 1
            if price and price < 0:
                profit = units * (100 / abs(price))
            elif price:
                profit = units * (price / 100)
            else:
                profit = units
            pl_units += profit

            if best_win is None or edge > best_win["edge"]:
                best_win = {"game_pk": pred["game_pk"], "edge": edge, "profit": profit}
        else:
            losses += 1
            pl_units -= units

            if worst_loss is None or units > worst_loss["loss"]:
                worst_loss = {"game_pk": pred["game_pk"], "edge": edge, "loss": units}

        if pred.get("clv") is not None:
            clv_values.append(float(pred["clv"]))

    return {
        "bets": wins + losses,
        "wins": wins,
        "losses": losses,
        "pl_units": round(pl_units, 3),
        "avg_clv": round(sum(clv_values) / len(clv_values), 4) if clv_values else None,
        "best_win": best_win,
        "worst_loss": worst_loss,
    }


def run():
    yesterday = get_yesterday_et()
    yesterday_str = yesterday.isoformat()
    logger.info("=== Nightly Results: grading %s ===", yesterday_str)

    db = get_supabase_client()

    # Step 1: Get games to grade
    ungraded = get_ungraded_games(db, yesterday_str)
    if not ungraded:
        logger.info("No games to grade")
        return 0

    logger.info("Found %d games to grade", len(ungraded))

    # Step 2: Grade each game
    graded_pks = []
    predictions_graded = 0
    errors = 0

    for game in ungraded:
        game_pk = game["game_pk"]
        try:
            result = grade_game(db, game_pk)
            if result is None:
                logger.warning("Game %d: first inning data not available", game_pk)
                continue

            nrfi, away_runs, home_runs = result
            graded_pks.append(game_pk)

            # Step 3: Grade predictions
            count = grade_predictions(db, game_pk, nrfi)
            predictions_graded += count

            result_str = "NRFI" if nrfi else f"YRFI ({away_runs}-{home_runs})"
            logger.info("Game %d: %s (%d predictions graded)", game_pk, result_str, count)

        except Exception:
            logger.error("Error grading game %d:\n%s", game_pk, traceback.format_exc())
            errors += 1

    # Step 4: Fetch closing odds and compute CLV
    clv_updated = 0
    try:
        # Fetch odds for yesterday (closing lines)
        odds_list = fetch_nrfi_odds(yesterday_str)
        if odds_list:
            closing_odds = {}
            for od in odds_list:
                gpk = match_to_game_pk(
                    od["home_team_name"], od["away_team_name"], yesterday_str, db,
                )
                if gpk and gpk in graded_pks:
                    # Use fair_odds (vig-removed) or close_odds as closing line
                    closing_odds[gpk] = od.get("fair_odds") or od.get("close_odds")

            if closing_odds:
                clv_updated = compute_clv(db, closing_odds)
                logger.info("Updated CLV for %d bets", clv_updated)
    except Exception:
        logger.warning("CLV computation failed:\n%s", traceback.format_exc())

    # Step 5: Daily summary
    summary = calculate_daily_pl(db, graded_pks)
    logger.info("Games graded: %d", len(graded_pks))
    if summary["bets"] > 0:
        logger.info(
            "Bets: %dW-%dL, P/L: %+.2f units, Avg CLV: %s",
            summary["wins"],
            summary["losses"],
            summary["pl_units"],
            f'{summary["avg_clv"]:+.4f}' if summary["avg_clv"] is not None else "N/A",
        )
        if summary.get("best_win"):
            bw = summary["best_win"]
            logger.info("Best win: game %d (edge %.1f%%, +%.2f units)",
                        bw["game_pk"], bw["edge"] * 100, bw["profit"])
        if summary.get("worst_loss"):
            wl = summary["worst_loss"]
            logger.info("Worst loss: game %d (edge %.1f%%, -%.2f units)",
                        wl["game_pk"], wl["edge"] * 100, wl["loss"])
    else:
        logger.info("No recommended bets to grade")

    logger.info("Predictions graded: %d, CLV updated: %d", predictions_graded, clv_updated)

    return 1 if errors > 0 else 0


def main():
    start = time.time()
    try:
        exit_code = run()
    except Exception:
        logger.error("Fatal error:\n%s", traceback.format_exc())
        exit_code = 1
    elapsed = time.time() - start
    logger.info("Finished in %.1f seconds (exit code %d)", elapsed, exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
