"""NRFI Edge System — Streamlit Dashboard."""

import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytz
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# Ensure project root is on path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.queries import (
    get_data_status, get_todays_predictions, get_todays_odds,
    get_prediction_history, get_season_stats, get_pitcher_nrfi_rate,
    get_backtest_results, get_bookmaker_performance,
    get_daily_pl, get_all_backtest_predictions, get_weather_batch,
    get_most_recent_prediction_date,
)
from dashboard.calculations import (
    format_prob, format_pl, format_edge, format_clv, format_odds,
    current_streak, calculate_roi, calculate_profit,
    classify_tier, TIER_STRONG, TIER_VALUE, TIER_LEAN, TIER_LABELS, BET_EDGE,
)
from dashboard.components import (
    render_bet_card, render_games_table, render_cumulative_pl_chart,
    render_profit_calendar, render_monthly_pl_bars, render_accuracy_chart,
    render_model_vs_pinnacle, render_clv_histogram, render_edge_histogram,
    render_bookmaker_table, render_backtest_accuracy,
    render_backtest_season_chart, render_prediction_distribution,
    render_high_confidence_table, render_rolling_accuracy,
    render_tier_performance,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="NRFI Edge System",
    page_icon="⚾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Auto-refresh every 60s
st_autorefresh(interval=60000, key="refresh")

# ---------------------------------------------------------------------------
# Data status
# ---------------------------------------------------------------------------
EASTERN = pytz.timezone("US/Eastern")


@st.cache_data(ttl=30)  # Fast refresh: status indicators should update quickly
def load_status():
    return get_data_status()


@st.cache_data(ttl=300)  # Slow refresh: backtest results change only on retrain (weekly)
def load_backtest():
    return get_backtest_results()


status = load_status()
backtest = load_backtest()

has_error = "error" in status
predictions_count = status.get("predictions_count", 0) if not has_error else 0
odds_count = status.get("odds_count", 0) if not has_error else 0
model_version = status.get("model_version", backtest.get("model_version", "unknown"))

# Derive year range from backtest data for display strings
_per_season = backtest.get("per_season", {})
if _per_season:
    _years = sorted(_per_season.keys())
    year_range = f"{_years[0]}-{_years[-1]}"
else:
    year_range = "historical"

today = date.today()


@st.cache_data(ttl=30)  # Fast refresh: live predictions update throughout the day
def load_today():
    return get_todays_predictions(today)


@st.cache_data(ttl=30)  # Fast refresh: odds move frequently during game day
def load_today_odds():
    return get_todays_odds(today)


@st.cache_data(ttl=60)  # Medium refresh: season aggregates change only when games finish
def load_season_stats():
    return get_season_stats()


today_preds = load_today()
today_odds = load_today_odds()

has_live_today = any(
    "backtest" not in (p.get("model_version") or "")
    for p in today_preds
) if today_preds else False

state = "live" if has_live_today else "backtest"

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
header_cols = st.columns([4, 1, 1, 1])
with header_cols[0]:
    st.title("NRFI Edge System")
with header_cols[1]:
    st.caption(f"Version: **{model_version}**")
with header_cols[2]:
    def _dot(ok):
        return "🟢" if ok else "🔴"
    st.caption(f"Predictions {_dot(predictions_count > 0)} | Odds {_dot(odds_count > 0)}")
with header_cols[3]:
    now_et = datetime.now(EASTERN)
    st.caption(f"Updated: {now_et.strftime('%-I:%M %p ET')}")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Page",
    ["Today's Picks", "Performance", "Model Accuracy", "Bet History"],
    label_visibility="collapsed",
)

st.sidebar.divider()

if state == "backtest":
    st.sidebar.markdown("### Historical Test Mode")
    st.sidebar.caption(f"No live bets yet. Showing analysis of {year_range} data.")
    st.sidebar.metric("Games Analyzed", f"{predictions_count:,}")
    st.sidebar.metric("Version", model_version)

    if backtest:
        cal = backtest.get("test_2025_calibrated", {})
        # Explain Brier Skill in plain terms
        bss = cal.get("brier_skill", 0)
        if bss > 0:
            st.sidebar.metric("Beats Coin Flip?", "Yes",
                               delta=f"+{bss:.4f} accuracy", delta_color="normal")
        else:
            st.sidebar.metric("Beats Coin Flip?", "Marginal",
                               delta=f"{bss:.4f}", delta_color="normal")

        actual_rate = backtest.get("overall_nrfi_rate")
        if actual_rate:
            st.sidebar.metric("Actual NRFI Rate", f"{actual_rate:.1%}")

    st.sidebar.divider()
    st.sidebar.markdown("### Data Sources")
    checks = {
        "predictions": ("Predictions", predictions_count > 0),
        "games": ("Games", status.get("games_count", 0) > 0 if not has_error else False),
        "weather": ("Weather", status.get("weather_count", 0) > 0 if not has_error else False),
        "pitcher_stats": ("Pitcher Stats", status.get("pitcher_stats_count", 0) > 0 if not has_error else False),
        "odds": ("Odds", odds_count > 0),
    }
    for key, (label, ok) in checks.items():
        count = status.get(f"{key}_count", 0) if not has_error else 0
        st.sidebar.write(f"{'🟢' if ok else '🔴'} {label}: {count:,}")

else:
    # Live mode sidebar
    st.sidebar.markdown("### Today's Summary")
    season_stats = load_season_stats()
    today_bets = [p for p in today_preds if p.get("bet_recommended")]
    today_wins = sum(1 for p in today_bets if p.get("result") is True)
    today_losses = sum(1 for p in today_bets if p.get("result") is False)

    st.sidebar.metric("Bets Placed Today", len(today_bets))
    st.sidebar.metric("Record", f"{today_wins}W - {today_losses}L")

    today_pl = 0.0
    for b in today_bets:
        if b.get("result") is not None and b.get("best_nrfi_price"):
            units = float(b["bet_size_units"]) if b.get("bet_size_units") else 1.0
            today_pl += calculate_profit(int(b["best_nrfi_price"]), units, b["result"])
    pl_color = "normal" if today_pl >= 0 else "inverse"
    st.sidebar.metric("Today's Profit/Loss", format_pl(today_pl),
                       delta=f"{today_wins}W {today_losses}L", delta_color=pl_color)

    if season_stats.get("total_bets", 0) > 0:
        st.sidebar.divider()
        st.sidebar.markdown("### Season Totals")
        st.sidebar.metric("Season Profit/Loss", format_pl(season_stats.get("total_pl", 0)),
                           delta=f"{season_stats.get('roi', 0):.1f}% return", delta_color="normal")
        clv_rate = season_stats.get("clv_beat_rate", 0)
        st.sidebar.metric("Beating the Market?",
                           f"{clv_rate:.0f}% of the time" if clv_rate else "No data yet")
        st.sidebar.caption("Above 50% = consistently finding value before odds move")
        streak = current_streak(season_stats.get("results_list", []))
        st.sidebar.metric("Current Streak", streak)


# =====================================================================
# PAGE: TODAY'S PICKS
# =====================================================================
if page == "Today's Picks":
    display_preds = today_preds
    display_date = today

    if not today_preds:
        st.warning("No predictions for today. Showing the most recent day with data as a demo.")
        demo_date = get_most_recent_prediction_date()
        if demo_date:
            display_preds = get_todays_predictions(demo_date)
            if display_preds:
                display_date = demo_date
        if not display_preds:
            st.error("Could not find any day with predictions to display.")
    elif state == "backtest":
        st.info(f"Showing historical predictions for today ({today}). "
                "Live predictions will appear once the daily pipeline runs.")

    if display_preds:
        # Build odds lookup
        odds_by_game = {}
        for o in today_odds:
            odds_by_game.setdefault(o["game_pk"], []).append(o)

        # Get pitcher NRFI rates
        pitcher_ids = set()
        for p in display_preds:
            if p.get("away_pitcher_id"):
                pitcher_ids.add(p["away_pitcher_id"])
            if p.get("home_pitcher_id"):
                pitcher_ids.add(p["home_pitcher_id"])

        pitcher_rates = {}
        for pid in pitcher_ids:
            rate = get_pitcher_nrfi_rate(pid)
            if rate:
                pitcher_rates[pid] = rate

        # Get weather data
        game_pks = [p["game_pk"] for p in display_preds]
        weather_by_game = get_weather_batch(game_pks)

        # ---- Classify picks into confidence tiers ----
        from dashboard.components import _safe_prob
        for p in display_preds:
            prob = _safe_prob(p.get("p_nrfi_calibrated"), p.get("p_nrfi_combined"))
            edge = float(p["edge"]) if p.get("edge") is not None else None
            p["_tier"] = classify_tier(edge, prob)

        strong = [p for p in display_preds if p["_tier"] == TIER_STRONG]
        value = [p for p in display_preds if p["_tier"] == TIER_VALUE]
        lean = [p for p in display_preds if p["_tier"] == TIER_LEAN]

        for group in (strong, value, lean):
            group.sort(key=lambda x: float(x.get("edge") or 0), reverse=True)

        recommended = strong + value + lean

        if recommended:
            st.markdown(f"## Today's Picks \u2014 {display_date.strftime('%B %-d, %Y')}")
            parts = []
            if strong:
                parts.append(f"**{len(strong)} Strong**")
            if value:
                parts.append(f"**{len(value)} Value**")
            if lean:
                parts.append(f"**{len(lean)} Lean**")
            st.caption(" \u00b7 ".join(parts) if parts else "No picks meet minimum threshold")

            top_picks = (strong + value)[:4]
            if not top_picks:
                top_picks = lean[:4]
            n_cols = min(len(top_picks), 4)
            cols = st.columns(n_cols)
            for i, pred in enumerate(top_picks):
                with cols[i]:
                    render_bet_card(pred, odds_by_game, pitcher_rates, tier=pred["_tier"])
        else:
            st.markdown(f"## Today's Games \u2014 {display_date.strftime('%B %-d, %Y')}")
            if display_preds:
                st.caption("No high-value bets found today (need 3%+ advantage). Full slate below.")

        # ---- Full Games Table ----
        st.markdown("### Full Slate")
        render_games_table(display_preds, odds_by_game, pitcher_rates, weather_by_game)


# =====================================================================
# PAGE: PERFORMANCE (was "Season Tracker")
# =====================================================================
elif page == "Performance":
    st.markdown("## Performance")

    season_stats = load_season_stats()
    daily_pl = get_daily_pl()

    if season_stats.get("total_bets", 0) > 0:
        # Key metrics row
        m1, m2, m3, m4, m5, m6, m7, m8 = st.columns(8)
        m1.metric("Total Bets", season_stats["total_bets"])
        total_decided = season_stats["wins"] + season_stats["losses"]
        wr = (season_stats["wins"] / total_decided * 100) if total_decided > 0 else 0
        m2.metric("Win Rate", f"{wr:.1f}%")
        m3.metric("Profit/Loss", format_pl(season_stats["total_pl"]))
        m4.metric("Return on Investment", f"{season_stats['roi']:.1f}%")
        m5.metric("Avg Advantage", f"{season_stats['avg_edge'] * 100:.1f}%")
        m6.metric("Avg Line Value", f"{season_stats['avg_clv'] * 100:+.1f}%")
        m7.metric("Beat Market %", f"{season_stats['clv_beat_rate']:.1f}%")
        m8.metric("Streak", current_streak(season_stats.get("results_list", [])))

        st.divider()

        # Charts
        render_cumulative_pl_chart(daily_pl)

        col1, col2 = st.columns(2)
        with col1:
            render_monthly_pl_bars(daily_pl)
        with col2:
            if daily_pl:
                render_profit_calendar(daily_pl, today.year, today.month)

        bets = get_prediction_history(min_edge=BET_EDGE)

        col3, col4 = st.columns(2)
        with col3:
            render_clv_histogram(bets)
        with col4:
            render_edge_histogram(bets)

        render_tier_performance(bets)

        render_bookmaker_table(get_bookmaker_performance())

        # Accuracy chart from all data
        @st.cache_data(ttl=300)
        def _load_all_preds_for_cal():
            return get_all_backtest_predictions()

        all_preds = _load_all_preds_for_cal()
        render_accuracy_chart(all_preds)

        # Model vs Pinnacle
        all_odds = []
        try:
            from dashboard.queries import get_supabase
            sb = get_supabase()
            res = sb.table("odds").select("game_pk, book, implied_nrfi_prob").ilike(
                "book", "%pinnacle%"
            ).execute()
            all_odds = res.data or []
        except Exception as e:
            st.caption(f"Pinnacle comparison unavailable: {e}")
        if all_odds:
            render_model_vs_pinnacle(all_preds, all_odds)

    else:
        # Historical test mode — show model analysis
        st.info(f"No live bets placed yet. Showing how the model performed on historical data ({year_range}).")
        st.divider()

        if backtest:
            render_backtest_season_chart(backtest)

        st.markdown("### How Accurate Are the Predictions?")
        st.caption("We group all predictions into 10 buckets from lowest to highest confidence, "
                   "then check if the actual NRFI rate matches what the model predicted.")

        @st.cache_data(ttl=300)
        def _load_backtest_all():
            return get_all_backtest_predictions()

        sample = _load_backtest_all()
        if sample:
            render_backtest_accuracy(sample)
            st.divider()
            st.markdown("### Is Accuracy Stable Over Time?")
            st.caption("A rolling window shows whether the model stays accurate across "
                       "different seasons and changing conditions.")
            render_rolling_accuracy(sample)
            st.divider()
            render_prediction_distribution(sample)
            render_high_confidence_table(sample)
        else:
            st.warning("Could not load historical predictions from database.")


# =====================================================================
# PAGE: MODEL ACCURACY (was "Model Health")
# =====================================================================
elif page == "Model Accuracy":
    st.markdown("## Model Accuracy")
    st.caption("How well does the model predict NRFI outcomes? "
               f"Tested on {backtest.get('games_predicted', 15000):,}+ historical games from {year_range}.")

    if backtest:
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(f"### Overall ({year_range})")
            st.metric("Version", backtest.get("model_version", "unknown"))
            st.metric("Games Tested", f"{backtest.get('games_predicted', 0):,}")

            raw = backtest.get("raw_metrics", {})
            st.markdown("**Before Correction**")
            st.caption("Raw model output, no adjustments applied")
            r1, r2, r3 = st.columns(3)
            r1.metric("Accuracy", f"{raw.get('brier', 0):.4f}",
                       help="Lower is better. 0 = perfect, 0.25 = coin flip")
            bss = raw.get("brier_skill", 0)
            r2.metric("vs Coin Flip", f"{bss:+.4f}",
                       help="Positive = better than always guessing 50%. Higher is better.")
            r3.metric("Cal. Error", f"{raw.get('ece', 0):.4f}",
                       help="Calibration Error: how far off predictions are from reality. Lower is better.")

        with col2:
            st.markdown("### 2025 (Unseen Games)")
            st.caption("The model never trained on 2025 data, so this tests real predictive power")
            test_raw = backtest.get("test_2025_raw", {})
            cal = backtest.get("test_2025_calibrated", {})

            st.markdown("**Before Correction**")
            t1, t2, t3 = st.columns(3)
            t1.metric("Accuracy", f"{test_raw.get('brier', 0):.4f}")
            t2.metric("vs Coin Flip", f"{test_raw.get('brier_skill', 0):+.4f}")
            t3.metric("Cal. Error", f"{test_raw.get('ece', 0):.4f}",
                       help="Calibration Error")

            st.markdown("**After Correction**")
            st.caption("Adjusted so predictions better match reality")
            c1, c2, c3 = st.columns(3)
            c1.metric("Accuracy", f"{cal.get('brier', 0):.4f}")
            # Delta shows improvement from correction
            raw_bss = test_raw.get("brier_skill", 0)
            cal_bss = cal.get("brier_skill", 0)
            improvement = cal_bss - raw_bss
            c2.metric("vs Coin Flip", f"{cal_bss:+.4f}",
                       delta=f"{improvement:+.4f} from correction",
                       delta_color="normal")
            c3.metric("Cal. Error", f"{cal.get('ece', 0):.4f}",
                       help="Calibration Error")

        st.divider()

        # Prediction stats
        st.markdown("### Prediction Spread")
        st.caption("How do the model's predictions compare to what actually happened?")
        p1, p2, p3, p4 = st.columns(4)
        actual = backtest.get("overall_nrfi_rate", 0)
        predicted = backtest.get("overall_mean_prediction", 0)
        p1.metric("Actual NRFI Rate", f"{actual:.1%}",
                   help="The real percentage of games with no runs in the 1st inning")
        p2.metric("Avg Predicted NRFI", f"{predicted:.1%}",
                   delta=f"{(predicted - actual):+.1%} off", delta_color="off",
                   help="The model's average prediction. Ideally matches the actual rate.")
        raw_std = backtest.get('prediction_std', 0)
        cal_std = backtest.get('calibrated_std', 0)
        p3.metric("Spread (raw)", f"{raw_std * 100:.1f}%",
                   help="Prediction spread: how spread out predictions are before correction. Higher = more differentiation between games.")
        p4.metric("Spread (adjusted)", f"{cal_std * 100:.1f}%",
                   help="Prediction spread after correction. Some spread is lost but accuracy improves.")

        st.divider()

        # Accuracy breakdown from full data
        @st.cache_data(ttl=300)
        def _load_health_data():
            return get_all_backtest_predictions()

        sample = _load_health_data()
        if sample:
            st.markdown("### Accuracy by Confidence Level")
            st.caption("We split all predictions into 10 groups from least to most confident. "
                       "If the model is well-calibrated, the 'Predicted' and 'Actual' columns should be close.")
            render_backtest_accuracy(sample)
            st.divider()
            render_high_confidence_table(sample)
            st.divider()
            st.markdown("### Accuracy Over Time")
            st.caption("Does the model stay accurate across all seasons, or does it degrade?")
            render_rolling_accuracy(sample)

        st.divider()

        # Per-season table
        per_season = backtest.get("per_season", {})
        if per_season:
            st.markdown("### Year-by-Year Results")
            st.caption("How the model performed in each MLB season")
            import pandas as pd
            season_rows = []
            for s, data in sorted(per_season.items()):
                bias = data['mean_pred'] - data['nrfi_rate']
                season_rows.append({
                    "Season": s,
                    "Games": data["games"],
                    "Actual NRFI %": f"{data['nrfi_rate']:.1%}",
                    "Model's Avg Prediction": f"{data['mean_pred']:.1%}",
                    "Off By": f"{bias:+.1%}",
                    "Accuracy": f"{data['brier']:.4f}",
                })
            st.dataframe(pd.DataFrame(season_rows), width="stretch", hide_index=True)

            render_backtest_season_chart(backtest)

    else:
        st.warning("No test results found. Run the backtest first.")

    st.divider()

    # System status
    st.markdown("### System Status")
    st.caption("Is all our data up to date?")
    if has_error:
        st.error(f"Database connection issue: {status.get('error', 'unknown')}")
    else:
        def _freshness_dot(timestamp_str, label):
            if not timestamp_str:
                return f"🔴 {label}: No data"
            try:
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                dt_et = dt.astimezone(EASTERN)
                age = datetime.now(pytz.utc) - dt
                if age.days == 0:
                    return f"🟢 {label}: {dt_et.strftime('%b %-d %-I:%M %p ET')} (today)"
                elif age.days == 1:
                    return f"🟡 {label}: {dt_et.strftime('%b %-d')} (yesterday)"
                else:
                    return f"🔴 {label}: {dt_et.strftime('%b %-d')} ({age.days} days ago)"
            except Exception:
                return f"🟡 {label}: {timestamp_str}"

        st.write(_freshness_dot(status.get("predictions_latest"), "Last Prediction Run"))
        st.write(_freshness_dot(status.get("odds_latest"), "Last Odds Update"))
        st.write(f"{'🟢' if status.get('pitcher_stats_count', 0) > 0 else '🔴'} "
                 f"Pitcher Stats: {status.get('pitcher_stats_count', 0):,} records")
        st.write(f"{'🟢' if status.get('weather_count', 0) > 0 else '🔴'} "
                 f"Weather Data: {status.get('weather_count', 0):,} records")
        st.write(f"{'🟢' if predictions_count > 0 else '🔴'} "
                 f"Database: Connected ({status.get('games_count', 0):,} games)")

        cal_path = Path(__file__).parent.parent / "config" / "calibrator.json"
        if cal_path.exists():
            mtime = datetime.fromtimestamp(os.path.getmtime(cal_path))
            st.write(f"🟢 Prediction Adjuster: Trained {mtime.strftime('%b %-d, %Y')}")
        else:
            st.write("🔴 Prediction Adjuster: Not found")


# =====================================================================
# PAGE: BET HISTORY
# =====================================================================
elif page == "Bet History":
    st.markdown("## Bet History")
    st.caption("Every prediction the model has made, with results")

    # Filters
    fcol1, fcol2, fcol3, fcol4, fcol5, fcol6 = st.columns(6)
    with fcol1:
        start_date = st.date_input("Start Date", value=date(2019, 1, 1))
    with fcol2:
        end_date = st.date_input("End Date", value=today)
    with fcol3:
        min_edge_pct = st.slider("Min Advantage %", 0, 15, 0,
                                  help="Only show games where the model found at least this much value")
    with fcol4:
        result_filter = st.selectbox("Outcome", ["All", "Wins", "Losses", "Pending"])
    with fcol5:
        season = st.selectbox("Season", [None, 2025, 2024, 2023, 2022, 2021, 2020, 2019],
                               format_func=lambda x: "All" if x is None else str(x))
    with fcol6:
        tier_filter = st.selectbox("Confidence Tier",
                                    ["All", TIER_STRONG, TIER_VALUE, TIER_LEAN],
                                    help="Filter by pick confidence tier")

    min_edge = min_edge_pct / 100 if min_edge_pct > 0 else None

    if season:
        start_date = date(season, 1, 1)
        end_date = min(date(season, 12, 31), today)

    @st.cache_data(ttl=60)
    def load_history(_start, _end, _min_edge, _result):
        return get_prediction_history(
            start_date=_start, end_date=_end,
            min_edge=_min_edge, result_filter=_result,
            limit=5000,
        )

    history = load_history(start_date, end_date, min_edge, result_filter)

    # Compute tier for each row and apply tier filter
    if history:
        from dashboard.components import _safe_prob
        for h in history:
            edge = float(h["edge"]) if h.get("edge") is not None else None
            prob = _safe_prob(h.get("p_nrfi_calibrated"), h.get("p_nrfi_combined"))
            h["_tier"] = classify_tier(edge, prob)
        if tier_filter != "All":
            history = [h for h in history if h.get("_tier") == tier_filter]

    if not history and min_edge_pct > 0:
        st.info("No games found with that advantage filter. "
                "In historical test mode, advantage is only calculated for live predictions. "
                "Try setting Min Advantage to 0%.")

    if history:
        # Summary stats
        total = len(history)
        wins = sum(1 for h in history if h.get("result") is True)
        losses = sum(1 for h in history if h.get("result") is False)
        pending = sum(1 for h in history if h.get("result") is None)

        edges = [float(h["edge"]) for h in history if h.get("edge") is not None]
        clvs = [float(h["clv"]) for h in history if h.get("clv") is not None]

        total_pl = 0.0
        total_wagered = 0.0
        for h in history:
            if h.get("result") is not None and h.get("best_nrfi_price"):
                units = float(h["bet_size_units"]) if h.get("bet_size_units") else 1.0
                total_pl += calculate_profit(int(h["best_nrfi_price"]), units, h["result"])
                total_wagered += units

        has_betting_data = total_wagered > 0

        if has_betting_data:
            s1, s2, s3, s4, s5, s6 = st.columns(6)
            s1.metric("Games", f"{total:,}")
            s2.metric("Record", f"{wins}W-{losses}L" + (f"-{pending}P" if pending else ""))
            s3.metric("Profit/Loss", format_pl(total_pl))
            roi = calculate_roi(total_pl, total_wagered)
            s4.metric("Return", f"{roi:.1f}%")
            s5.metric("Avg Advantage", f"{sum(edges) / len(edges) * 100:.1f}%" if edges else "N/A")
            s6.metric("Avg Line Value", f"{sum(clvs) / len(clvs) * 100:+.1f}%" if clvs else "N/A")
        else:
            # Backtest mode — show model performance stats instead of betting stats
            decided = wins + losses
            win_rate = (wins / decided * 100) if decided > 0 else 0
            s1, s2, s3, s4 = st.columns([1, 1.3, 1, 1.5])
            s1.metric("Games", f"{total:,}")
            s2.metric("Record", f"{wins}W-{losses}L")
            s3.metric("NRFI Win Rate", f"{win_rate:.1f}%",
                       help="How often NRFIs actually happened in these games")
            # Calculate mean model accuracy for this subset
            from dashboard.components import _safe_prob
            pred_probs = [_safe_prob(h.get("p_nrfi_calibrated"), h.get("p_nrfi_combined"))
                          for h in history if _safe_prob(h.get("p_nrfi_calibrated"), h.get("p_nrfi_combined")) is not None]
            if pred_probs:
                avg_pred = sum(pred_probs) / len(pred_probs)
                s4.metric("Avg Model Prediction", f"{avg_pred:.1%}",
                           delta=f"{(avg_pred - win_rate/100):+.1%} vs actual",
                           delta_color="off")

        # Tier breakdown (only when odds data exists)
        has_odds_data = any(h.get("best_book") for h in history)
        if has_betting_data:
            tier_parts = []
            for t_name in (TIER_STRONG, TIER_VALUE, TIER_LEAN):
                t_bets = [h for h in history if h.get("_tier") == t_name]
                if not t_bets:
                    continue
                t_w = sum(1 for h in t_bets if h.get("result") is True)
                t_l = sum(1 for h in t_bets if h.get("result") is False)
                t_pl = 0.0
                for h in t_bets:
                    if h.get("result") is not None and h.get("best_nrfi_price"):
                        u = float(h["bet_size_units"]) if h.get("bet_size_units") else 1.0
                        t_pl += calculate_profit(int(h["best_nrfi_price"]), u, h["result"])
                tier_parts.append(f"**{TIER_LABELS[t_name]}**: {t_w}W-{t_l}L ({format_pl(t_pl)})")
            if tier_parts:
                st.caption(" | ".join(tier_parts))

        st.divider()

        import pandas as pd

        rows = []
        for h in history:
            display_prob = h.get("p_nrfi_calibrated") if h.get("p_nrfi_calibrated") is not None else h.get("p_nrfi_combined")
            result_str = "✅" if h.get("result") is True else "❌" if h.get("result") is False else "⏳"

            row = {
                "Date": h.get("game_date", ""),
                "Matchup": f"{h['away_team']} @ {h['home_team']}",
                "Away Pitcher": h.get("away_pitcher_name", ""),
                "Home Pitcher": h.get("home_pitcher_name", ""),
                "NRFI Chance": format_prob(display_prob),
            }

            # Only include betting columns when we have actual odds data
            if has_odds_data:
                row["Tier"] = TIER_LABELS.get(h.get("_tier"), "\u2014")
                pl = None
                if h.get("result") is not None and h.get("best_nrfi_price"):
                    units = float(h["bet_size_units"]) if h.get("bet_size_units") else 1.0
                    pl = calculate_profit(int(h["best_nrfi_price"]), units, h["result"])
                row.update({
                    "Best Odds": f"{h['best_book']} {format_odds(h['best_nrfi_price'])}" if h.get("best_book") else "-",
                    "Book's %": format_prob(h.get("implied_prob_best")),
                    "Advantage": format_edge(h.get("edge")),
                    "Bet Size": f"{float(h['bet_size_units']):.2f}u" if h.get("bet_size_units") else "-",
                    "Line Value": format_clv(h.get("clv")),
                    "Profit/Loss": format_pl(pl) if pl is not None else "-",
                })

            row["Result"] = result_str
            rows.append(row)

        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch", hide_index=True, height=600)

        csv = df.to_csv(index=False)
        st.download_button("Download as CSV", csv, "nrfi_history.csv", "text/csv")

    elif not min_edge_pct:
        st.info("No prediction history found for this date range.")
