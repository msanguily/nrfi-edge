"""Shared utilities for daily pipeline scripts."""

import logging
import os
import sys
from datetime import date, datetime, timedelta

import pytz
from dotenv import load_dotenv

ET = pytz.timezone("US/Eastern")


def get_now_et():
    """Returns the current datetime in US/Eastern timezone."""
    return datetime.now(ET)


def get_today_et():
    """Returns today's date in US/Eastern timezone."""
    return get_now_et().date()


def get_yesterday_et():
    """Returns yesterday's date in US/Eastern."""
    return get_today_et() - timedelta(days=1)


def is_mlb_season(d=None):
    """True if date is March 20 - November 5 (covers spring training through postseason)."""
    if d is None:
        d = get_today_et()
    return date(d.year, 3, 20) <= d <= date(d.year, 11, 5)


def setup_logging(script_name):
    """Configure logging to both console and logs/{script_name}.log.

    Creates logs/ directory if needed. Returns the root logger.
    """
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{script_name}.log")

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers on re-import
    root.handlers = []
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return root


def get_supabase_client():
    """Load .env and return a supabase client."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(env_path)

    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)
