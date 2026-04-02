"""Slack notification system for NRFI picks."""

import logging
import os
import time
from datetime import datetime, timezone

import requests
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def format_american_odds(price: int) -> str:
    """Format American odds with explicit sign. E.g. +115 or -135."""
    if price >= 0:
        return f"+{price}"
    return str(price)


def _utc_to_eastern(utc_str: str) -> str:
    """Convert a UTC datetime string to US/Eastern display string.

    Accepts ISO 8601 formats like '2024-06-15T23:05:00Z' or '2024-06-15 23:05:00'.
    Returns e.g. '7:05 PM'.
    """
    utc_str = utc_str.replace("Z", "+00:00")
    if "T" in utc_str:
        dt = datetime.fromisoformat(utc_str)
    else:
        dt = datetime.fromisoformat(utc_str)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    eastern = dt.astimezone(ZoneInfo("US/Eastern"))
    return eastern.strftime("%-I:%M %p")


def _post_to_slack(text: str, webhook_url: str) -> bool:
    """POST a mrkdwn text payload to Slack with up to 3 retries."""
    if not webhook_url:
        webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("No Slack webhook URL provided — skipping notification")
        return False

    payload = {"text": text}

    for attempt in range(1, 4):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("Slack message sent successfully")
                print(f"[Slack] Message sent: {text[:120]}...")
                return True
            logger.warning(
                "Slack returned %d on attempt %d: %s",
                resp.status_code,
                attempt,
                resp.text,
            )
        except requests.RequestException as exc:
            logger.warning("Slack request failed on attempt %d: %s", attempt, exc)

        if attempt < 3:
            time.sleep(2)

    logger.error("Failed to send Slack message after 3 attempts")
    return False


def send_nrfi_alert(prediction: dict, webhook_url: str = None) -> bool:
    """Send a formatted Slack message for an NRFI bet recommendation."""
    game_time_et = _utc_to_eastern(prediction["game_time_utc"])
    price_str = format_american_odds(prediction["best_nrfi_price"])

    fd = prediction.get("factor_details", {})
    park_name = fd.get("park", "Unknown Park")
    hr_factor = fd.get("hr_factor", "N/A")
    weather_summary = fd.get("weather_summary", "")
    is_outdoor = fd.get("outdoor", True)

    factors_str = f"{park_name} (HR factor: {hr_factor})"
    if is_outdoor and weather_summary:
        factors_str += f", {weather_summary}"

    text = (
        f"\U0001f7e2 *NRFI PICK* — {prediction['away_team_abbr']} @ "
        f"{prediction['home_team_abbr']} — {game_time_et} ET\n"
        f"Pitchers: {prediction['away_pitcher_name']} "
        f"({prediction['away_pitcher_throws']}) vs "
        f"{prediction['home_pitcher_name']} "
        f"({prediction['home_pitcher_throws']})\n"
        f"Model: {prediction['p_nrfi_calibrated']:.1%} NRFI | "
        f"Best line: {price_str} ({prediction['best_book']}) | "
        f"Implied: {prediction['implied_prob_best']:.1%}\n"
        f"Edge: {prediction['edge']:.1%} | "
        f"Bet: {prediction['bet_size_units']:.1f} units\n"
        f"Factors: {factors_str}"
    )

    return _post_to_slack(text, webhook_url)


def send_daily_summary(summary: dict, webhook_url: str = None) -> bool:
    """Send the daily NRFI summary."""
    pending = summary.get("pending", 0)
    pending_str = f" ({pending} pending)" if pending else ""

    text = (
        f"\U0001f4ca *NRFI Daily Summary* — {summary['date']}\n"
        f"Games analyzed: {summary['games_analyzed']} | "
        f"Bets: {summary['bets_recommended']}\n"
        f"Results: {summary['wins']}W - {summary['losses']}L{pending_str}\n"
        f"Today: {summary['today_pl']:+.2f}u | "
        f"Season: {summary['season_pl']:+.2f}u "
        f"({summary['season_bets']} bets, {summary['season_roi']:.1%} ROI)\n"
        f"Avg CLV: {summary['avg_clv']:+.3f}"
    )

    return _post_to_slack(text, webhook_url)


def send_no_plays_alert(
    date: str, games_analyzed: int, max_edge: float, webhook_url: str = None
) -> bool:
    """Send alert when no plays qualify."""
    text = (
        f"\u26aa No NRFI plays for {date}\n"
        f"{games_analyzed} games analyzed | "
        f"Best edge: {max_edge:.1%} (below 3.0% threshold)"
    )

    return _post_to_slack(text, webhook_url)


def send_error_alert(error_msg: str, webhook_url: str = None) -> bool:
    """Send system error alert."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        f"\U0001f534 *NRFI System Error*\n"
        f"{error_msg}\n"
        f"Timestamp: {now}"
    )

    return _post_to_slack(text, webhook_url)


def send_health_check(status: dict, webhook_url: str = None) -> bool:
    """Send daily morning health check."""
    text = (
        f"\u2705 NRFI system online — {status['date']}\n"
        f"Games today: {status['num_games']} | "
        f"Pitchers confirmed: {status['pitchers_confirmed']}/{status['total']}\n"
        f"Data freshness: pitcher_stats {status['pitcher_staleness']}, "
        f"batter_stats {status['batter_staleness']}"
    )

    return _post_to_slack(text, webhook_url)
