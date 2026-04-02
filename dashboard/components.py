"""Reusable UI components: bet cards, charts, tables, calendar."""

import math
from datetime import datetime, date

import plotly.graph_objects as go
import streamlit as st

from .calculations import (
    format_odds, format_prob, format_pl, format_edge, format_clv,
    american_to_implied, calculate_profit, current_streak,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_prob(calibrated, raw):
    """Pick calibrated prob if available, else raw. Handles 0.0 correctly (unlike `or`)."""
    if calibrated is not None:
        return float(calibrated)
    if raw is not None:
        return float(raw)
    return None


def _result_icon(result):
    if result is True:
        return "✅"
    if result is False:
        return "❌"
    return "⏳"


def _status_label(status):
    if status == "final":
        return "Final"
    if status in ("live", "in_progress"):
        return "Live"
    return "Upcoming"


def _parse_utc_to_eastern(utc_str):
    """Parse a UTC timestamp string to Eastern time string. Returns '' on failure."""
    if not utc_str:
        return ""
    try:
        import pytz
        utc = pytz.utc
        eastern = pytz.timezone("US/Eastern")
        if isinstance(utc_str, str):
            dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        else:
            dt = utc_str
        if dt.tzinfo is None:
            dt = utc.localize(dt)
        return dt.astimezone(eastern).strftime("%-I:%M %p ET")
    except Exception:
        return ""


def _hr_factor_label(hr_factor):
    """Turn park HR factor into plain English."""
    if hr_factor is None:
        return ""
    f = float(hr_factor)
    pct = abs(f - 1.0) * 100
    if f > 1.03:
        return f"hitter-friendly (+{pct:.0f}% HR)"
    elif f < 0.97:
        return f"pitcher-friendly (-{pct:.0f}% HR)"
    return "neutral park"


# ---------------------------------------------------------------------------
# Bet card
# ---------------------------------------------------------------------------

def render_bet_card(pred: dict, odds_by_game: dict = None, pitcher_rates: dict = None):
    """Render a single recommended bet card."""
    odds_list = (odds_by_game or {}).get(pred["game_pk"], [])
    matchup = f"{pred['away_team']} @ {pred['home_team']}"
    game_time_str = _parse_utc_to_eastern(pred.get("game_time_utc"))

    with st.container(border=True):
        st.markdown(f"### {matchup}")
        if game_time_str:
            st.caption(game_time_str)

        # Pitcher scoreless rates
        away_rate = (pitcher_rates or {}).get(pred.get("away_pitcher_id"), {})
        home_rate = (pitcher_rates or {}).get(pred.get("home_pitcher_id"), {})

        away_nrfi = f"scoreless {away_rate['nrfi_rate']:.0%} of {away_rate['first_inn_starts']} starts" if away_rate else ""
        home_nrfi = f"scoreless {home_rate['nrfi_rate']:.0%} of {home_rate['first_inn_starts']} starts" if home_rate else ""

        st.markdown(f"**{pred['away_pitcher_name']}** {away_nrfi}")
        st.markdown(f"**{pred['home_pitcher_name']}** {home_nrfi}")

        # Model probability
        display_prob = _safe_prob(pred.get("p_nrfi_calibrated"), pred.get("p_nrfi_combined"))

        if display_prob is not None:
            color = "#00cc66" if display_prob > 0.55 else "#ffaa00" if display_prob > 0.50 else "#ff4444"
            st.markdown(f'<p style="font-size:2em; font-weight:bold; color:{color}; margin:0">'
                        f'NRFI Chance: {display_prob:.1%}</p>', unsafe_allow_html=True)

        # Best odds + sharpest book
        col1, col2 = st.columns(2)
        with col1:
            if pred.get("best_book") and pred.get("best_nrfi_price"):
                st.metric("Best Odds", f"{pred['best_book']} {format_odds(pred['best_nrfi_price'])}")
            elif odds_list:
                best = max(odds_list, key=lambda x: x.get("nrfi_decimal") or 0)
                st.metric("Best Odds", f"{best['book']} {format_odds(best.get('nrfi_price'))}")
            else:
                st.metric("Best Odds", "No odds yet")

        with col2:
            pinnacle = [o for o in odds_list if "pinnacle" in (o.get("book") or "").lower()]
            if pinnacle:
                st.metric("Sharpest Book", format_odds(pinnacle[0].get("nrfi_price")))
            elif pred.get("edge") is not None:
                st.metric("Our Advantage", format_edge(pred["edge"]))

        # Advantage & bet size
        col3, col4 = st.columns(2)
        with col3:
            if pred.get("edge") is not None:
                edge_val = float(pred["edge"]) * 100
                st.metric("Our Advantage", f"{edge_val:.1f}%",
                           help="How much higher our estimate is vs the sportsbook's odds")
        with col4:
            if pred.get("bet_size_units") is not None:
                st.metric("Suggested Bet", f"{float(pred['bet_size_units']):.2f} units",
                           help="Based on Kelly criterion (capped at 2% of bankroll)")

        # Key factors
        if pred.get("factor_details"):
            fd = pred["factor_details"] if isinstance(pred["factor_details"], dict) else {}
            factors = []
            if fd.get("park_name"):
                hr_label = _hr_factor_label(fd.get("park_hr_factor"))
                factors.append(f"{fd['park_name']} \u2014 {hr_label}" if hr_label else fd["park_name"])
            if fd.get("temperature_f"):
                factors.append(f"{fd['temperature_f']}\u00b0F")
            if fd.get("wind_speed_mph"):
                factors.append(f"Wind: {fd['wind_speed_mph']} mph {fd.get('wind_relative', '')}")
            if factors:
                st.caption("Key factors: " + " | ".join(factors))


# ---------------------------------------------------------------------------
# Games table
# ---------------------------------------------------------------------------

def render_games_table(predictions: list, odds_by_game: dict = None,
                       pitcher_rates: dict = None, weather_by_game: dict = None):
    """Render the full games table for a day."""
    if not predictions:
        st.info("No predictions available for this date.")
        return

    # Detect if any game has odds data
    has_odds = any(
        pred.get("best_book") or (odds_by_game or {}).get(pred["game_pk"])
        for pred in predictions
    )

    rows = []
    for pred in predictions:
        time_str = _parse_utc_to_eastern(pred.get("game_time_utc"))
        if time_str:
            time_str = time_str.replace(" ET", "")

        matchup = f"{pred['away_team']} @ {pred['home_team']}"

        # Pitcher NRFI rates
        away_rate = (pitcher_rates or {}).get(pred.get("away_pitcher_id"), {})
        home_rate = (pitcher_rates or {}).get(pred.get("home_pitcher_id"), {})
        away_p = pred.get("away_pitcher_name", "TBD")
        home_p = pred.get("home_pitcher_name", "TBD")
        if away_rate:
            away_p += f" ({away_rate['nrfi_rate']:.0%})"
        if home_rate:
            home_p += f" ({home_rate['nrfi_rate']:.0%})"

        display_prob = _safe_prob(pred.get("p_nrfi_calibrated"), pred.get("p_nrfi_combined"))
        p_top = float(pred["p_nrfi_top"]) if pred.get("p_nrfi_top") is not None else None
        p_bot = float(pred["p_nrfi_bottom"]) if pred.get("p_nrfi_bottom") is not None else None

        status = _status_label(pred.get("status"))
        result = _result_icon(pred.get("result"))

        row = {
            "Time": time_str,
            "Matchup": matchup,
            "Away Pitcher": away_p,
            "Home Pitcher": home_p,
            "NRFI Chance": format_prob(display_prob),
            "Away Scoreless": format_prob(p_top),
            "Home Scoreless": format_prob(p_bot),
        }

        # Only show betting columns when odds exist
        if has_odds:
            game_odds = (odds_by_game or {}).get(pred["game_pk"], [])
            best_line = "-"
            pinnacle_line = "-"
            implied_prob = None
            if pred.get("best_book") and pred.get("best_nrfi_price"):
                best_line = f"{pred['best_book']} {format_odds(pred['best_nrfi_price'])}"
                implied_prob = american_to_implied(int(pred["best_nrfi_price"]))
            elif game_odds:
                best = max(game_odds, key=lambda x: x.get("nrfi_decimal") or 0)
                best_line = f"{best['book']} {format_odds(best.get('nrfi_price'))}"
                if best.get("nrfi_price"):
                    implied_prob = american_to_implied(int(best["nrfi_price"]))

            for o in game_odds:
                if "pinnacle" in (o.get("book") or "").lower():
                    pinnacle_line = format_odds(o.get("nrfi_price"))

            edge = float(pred["edge"]) if pred.get("edge") is not None else None
            kelly = float(pred["bet_size_units"]) if pred.get("bet_size_units") is not None else None
            game_total = pred.get("game_total")

            row.update({
                "Best Odds": best_line,
                "Game Total": f"{float(game_total):.1f}" if game_total else "-",
                "Sharpest Book": pinnacle_line,
                "Book's %": format_prob(implied_prob),
                "Advantage": format_edge(edge),
                "Bet Size": f"{kelly:.2f}u" if kelly and edge and edge >= 0.03 else "-",
            })

        row["Status"] = status
        row["Result"] = result
        rows.append(row)

    import pandas as pd
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Expandable details per game
    for pred in predictions:
        matchup = f"{pred['away_team']} @ {pred['home_team']}"
        with st.expander(f"Details: {matchup}"):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Scoreless Inning Breakdown**")
                p_top = pred.get("p_nrfi_top")
                p_bot = pred.get("p_nrfi_bottom")
                p_raw = pred.get("p_nrfi_combined")
                p_adj = pred.get("p_nrfi_calibrated")
                st.write(f"Away half scoreless: {format_prob(p_top)}")
                st.write(f"Home half scoreless: {format_prob(p_bot)}")
                st.write(f"Both halves (raw): {format_prob(p_raw)}")
                st.write(f"Both halves (adjusted): {format_prob(p_adj)}")
                if p_top is not None and p_bot is not None:
                    st.caption("NRFI = both halves scoreless. "
                               f"Raw = {format_prob(p_top)} x {format_prob(p_bot)}")

            with col2:
                st.markdown("**Ballpark & Weather**")
                if pred.get("park_name"):
                    hr_label = _hr_factor_label(pred.get("park_hr_factor"))
                    elev = f", {pred['park_elevation']}ft elevation" if pred.get("park_elevation") else ""
                    park_desc = f"{pred['park_name']}"
                    if hr_label:
                        park_desc += f" ({hr_label})"
                    if elev:
                        park_desc += elev
                    st.write(park_desc)

                weather = (weather_by_game or {}).get(pred["game_pk"], {})
                if weather:
                    dome = weather.get("is_dome_closed")
                    if dome:
                        st.write("Retractable roof: Closed (weather neutral)")
                    else:
                        parts = []
                        temp = weather.get("temperature_f")
                        wind = weather.get("wind_speed_mph")
                        wind_dir = weather.get("wind_relative", "")
                        humidity = weather.get("humidity_pct")
                        if temp is not None:
                            parts.append(f"{float(temp):.0f}\u00b0F")
                        if wind is not None:
                            parts.append(f"Wind: {float(wind):.0f} mph {wind_dir}")
                        if humidity is not None:
                            parts.append(f"Humidity: {float(humidity):.0f}%")
                        if parts:
                            st.write(" | ".join(parts))
                elif not pred.get("factor_details"):
                    st.write("No weather data available")

            if pred.get("factor_details") and isinstance(pred["factor_details"], dict):
                st.markdown("**What's Driving This Prediction**")
                fd = pred["factor_details"]
                for key, val in fd.items():
                    # Clean up internal key names for display
                    label = key.replace("_", " ").title()
                    st.write(f"- {label}: {val}")


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def render_cumulative_pl_chart(daily_pl: list):
    """Running total of profit/loss over time."""
    if not daily_pl:
        st.info("No profit/loss data available yet. Place bets to see results here.")
        return

    cum_actual = 0.0
    cum_expected = 0.0
    dates = []
    actuals = []
    expecteds = []

    for day in daily_pl:
        cum_actual += day["pl"]
        cum_expected += day.get("expected_pl", 0.0)
        dates.append(day["date"])
        actuals.append(cum_actual)
        expecteds.append(cum_expected)

    cum_bets = 0
    stds = []
    for day in daily_pl:
        cum_bets += day["bets"]
        stds.append(math.sqrt(max(cum_bets, 1)) * 0.5)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=actuals, mode="lines", name="Actual Profit/Loss",
        line=dict(color="#1f77b4", width=2)
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=expecteds, mode="lines", name="Expected (based on advantage found)",
        line=dict(color="#888888", width=1, dash="dash")
    ))

    upper = [e + s for e, s in zip(expecteds, stds)]
    lower = [e - s for e, s in zip(expecteds, stds)]
    fig.add_trace(go.Scatter(
        x=dates + dates[::-1], y=upper + lower[::-1],
        fill="toself", fillcolor="rgba(136,136,136,0.15)",
        line=dict(color="rgba(0,0,0,0)"), name="Normal variance range", showlegend=True
    ))

    fig.update_layout(
        title="Running Profit/Loss Over Time",
        xaxis_title="Date", yaxis_title="Units",
        template="plotly_dark", height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02)
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Blue line above gray dashed = running hot. Below = running cold. "
               "If both track together, the model is performing as expected.")


def render_profit_calendar(daily_pl: list, year: int = None, month: int = None):
    """Monthly profit calendar as a heatmap."""
    if not daily_pl:
        st.info("No daily data for calendar.")
        return

    import calendar

    filtered = daily_pl
    if year and month:
        prefix = f"{year}-{month:02d}"
        filtered = [d for d in daily_pl if d["date"].startswith(prefix)]

    if not filtered:
        st.info("No data for selected month.")
        return

    pl_by_date = {d["date"]: d["pl"] for d in filtered}

    first = filtered[0]["date"]
    if not year:
        year = int(first[:4])
    if not month:
        month = int(first[5:7])

    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdatescalendar(year, month)

    z_data = []
    text_data = []
    for week in weeks:
        week_vals = []
        week_text = []
        for day in week:
            if day.month != month:
                week_vals.append(None)
                week_text.append("")
            else:
                key = day.isoformat()
                pl = pl_by_date.get(key)
                week_vals.append(pl if pl is not None else None)
                if pl is not None:
                    week_text.append(f"{day.day}<br>{pl:+.1f}u")
                else:
                    week_text.append(f"{day.day}")
        z_data.append(week_vals)
        text_data.append(week_text)

    fig = go.Figure(data=go.Heatmap(
        z=z_data,
        text=text_data,
        texttemplate="%{text}",
        textfont={"size": 10},
        x=["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        colorscale=[
            [0, "#cc0000"], [0.25, "#ff6666"],
            [0.5, "#333333"],
            [0.75, "#66cc66"], [1, "#00cc66"]
        ],
        zmid=0,
        showscale=True,
        colorbar=dict(title="Profit (u)"),
    ))

    month_name = calendar.month_name[month]
    fig.update_layout(
        title=f"Daily Results \u2014 {month_name} {year}",
        template="plotly_dark", height=250,
        yaxis=dict(autorange="reversed", showticklabels=False),
        xaxis=dict(side="top"),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Green = winning day, Red = losing day, Gray = no bets")


def render_monthly_pl_bars(daily_pl: list):
    """Monthly profit/loss bar chart."""
    if not daily_pl:
        st.info("No data for monthly chart.")
        return

    monthly = {}
    for d in daily_pl:
        month_key = d["date"][:7]
        monthly[month_key] = monthly.get(month_key, 0.0) + d["pl"]

    months = sorted(monthly.keys())
    values = [monthly[m] for m in months]
    colors = ["#00cc66" if v >= 0 else "#cc0000" for v in values]

    fig = go.Figure(go.Bar(x=months, y=values, marker_color=colors))
    fig.update_layout(
        title="Monthly Profit/Loss",
        xaxis_title="Month", yaxis_title="Units",
        template="plotly_dark", height=350,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_accuracy_chart(predictions: list):
    """How close predictions are to reality, shown as a chart."""
    if not predictions:
        st.info("No prediction data available.")
        return

    import numpy as np

    probs = []
    actuals = []
    for p in predictions:
        prob = _safe_prob(p.get("p_nrfi_calibrated"), p.get("p_nrfi_combined"))
        result = p.get("result")
        if prob is not None and result is not None:
            probs.append(prob)
            actuals.append(1 if result else 0)

    if len(probs) < 100:
        st.info("Not enough data for accuracy chart.")
        return

    probs = np.array(probs)
    actuals = np.array(actuals)

    n_bins = 10
    indices = np.argsort(probs)
    bin_size = len(indices) // n_bins

    pred_means = []
    actual_means = []
    counts = []
    for i in range(n_bins):
        start = i * bin_size
        end = start + bin_size if i < n_bins - 1 else len(indices)
        idx = indices[start:end]
        pred_means.append(probs[idx].mean())
        actual_means.append(actuals[idx].mean())
        counts.append(len(idx))

    fig = go.Figure()

    min_p = min(pred_means) - 0.02
    max_p = max(pred_means) + 0.02
    fig.add_trace(go.Scatter(
        x=[min_p, max_p], y=[min_p, max_p],
        mode="lines", name="Perfect accuracy",
        line=dict(color="#888888", dash="dash")
    ))

    fig.add_trace(go.Scatter(
        x=pred_means, y=actual_means,
        mode="lines+markers", name="Our model",
        line=dict(color="#00cc66", width=2),
        marker=dict(size=8),
        text=[f"{c:,} games" for c in counts],
        hovertemplate="We predicted: %{x:.1%}<br>Actually happened: %{y:.1%}<br>%{text}"
    ))

    fig.update_layout(
        title=f"Prediction Accuracy Check ({len(probs):,} games)",
        xaxis_title="What We Predicted", yaxis_title="What Actually Happened",
        template="plotly_dark", height=400,
        xaxis=dict(tickformat=".0%"), yaxis=dict(tickformat=".0%"),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("If the green line follows the dashed gray line, the model is accurate. "
               "Points above the line = model underestimates NRFI. Below = overestimates.")

    return pred_means, actual_means, counts


def render_model_vs_pinnacle(predictions: list, odds_data: list):
    """Our predictions vs the sharpest sportsbook."""
    if not predictions or not odds_data:
        st.info("Need both predictions and Pinnacle odds for this comparison.")
        return

    pinnacle = {}
    for o in odds_data:
        if "pinnacle" in (o.get("book") or "").lower() and o.get("implied_nrfi_prob"):
            pinnacle[o["game_pk"]] = float(o["implied_nrfi_prob"])

    if not pinnacle:
        st.info("No sharp book odds available.")
        return

    model_probs = []
    pinnacle_probs = []
    for p in predictions:
        if p["game_pk"] in pinnacle:
            prob = _safe_prob(p.get("p_nrfi_calibrated"), p.get("p_nrfi_combined"))
            if prob is not None:
                model_probs.append(prob)
                pinnacle_probs.append(pinnacle[p["game_pk"]])

    if not model_probs:
        st.info("No matching data between our predictions and Pinnacle.")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=model_probs, y=pinnacle_probs,
        mode="markers", name="Games",
        marker=dict(color="#00cc66", size=5, opacity=0.5),
    ))
    fig.add_trace(go.Scatter(
        x=[0.3, 0.7], y=[0.3, 0.7],
        mode="lines", name="Perfect agreement",
        line=dict(color="#888888", dash="dash"),
    ))

    fig.update_layout(
        title="Our Model vs Sharpest Sportsbook (Pinnacle)",
        xaxis_title="Our NRFI Estimate", yaxis_title="Pinnacle's NRFI Estimate",
        template="plotly_dark", height=400,
        xaxis=dict(tickformat=".0%"), yaxis=dict(tickformat=".0%"),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Points above the diagonal = we think NRFI is less likely than Pinnacle. "
               "Below = we think NRFI is more likely (potential value).")


def render_clv_histogram(bets: list):
    """How often we got better odds than the closing line."""
    clvs = [float(b["clv"]) * 100 for b in bets if b.get("clv") is not None]
    if not clvs:
        st.info("No line value data available yet. This appears once live bets are placed.")
        return

    import numpy as np
    mean_clv = np.mean(clvs)

    fig = go.Figure()
    fig.add_trace(go.Histogram(x=clvs, nbinsx=30, marker_color="#00cc66", opacity=0.7))
    fig.add_vline(x=mean_clv, line_dash="dash", line_color="#ffaa00",
                  annotation_text=f"Average: {mean_clv:+.1f}%")
    fig.add_vline(x=0, line_dash="dot", line_color="#888888")

    fig.update_layout(
        title="Line Value Distribution",
        xaxis_title="Line Value (%)", yaxis_title="Number of Bets",
        template="plotly_dark", height=350,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Line Value = did we bet at better odds than the final odds before game start? "
               "Positive average = consistently finding value. This is the #1 predictor of long-term profit.")


def render_edge_histogram(bets: list):
    """Distribution of advantages found on recommended bets."""
    edges = [float(b["edge"]) * 100 for b in bets if b.get("edge") is not None]
    if not edges:
        st.info("No advantage data available yet.")
        return

    import numpy as np
    mean_edge = np.mean(edges)

    fig = go.Figure()
    fig.add_trace(go.Histogram(x=edges, nbinsx=30, marker_color="#1f77b4", opacity=0.7))
    fig.add_vline(x=mean_edge, line_dash="dash", line_color="#ffaa00",
                  annotation_text=f"Average: {mean_edge:.1f}%")

    fig.update_layout(
        title="Advantage Distribution",
        xaxis_title="Advantage Over Sportsbook (%)", yaxis_title="Number of Bets",
        template="plotly_dark", height=350,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("How much value the model found on each bet. Higher = more value.")


def render_bookmaker_table(book_data: list):
    """Which sportsbooks give us the best prices?"""
    if not book_data:
        st.info("No sportsbook data yet. This appears once live bets are placed.")
        return

    st.markdown("### Sportsbook Comparison")
    st.caption("Which books consistently give the best NRFI prices?")

    import pandas as pd
    df = pd.DataFrame(book_data)
    df.columns = ["Sportsbook", "Times Used", "Win Rate", "Profit/Loss", "Avg Line Value"]
    df["Win Rate"] = df["Win Rate"].apply(lambda x: f"{x:.1f}%")
    df["Profit/Loss"] = df["Profit/Loss"].apply(lambda x: f"{x:+.2f}u")
    df["Avg Line Value"] = df["Avg Line Value"].apply(lambda x: f"{x * 100:+.1f}%")
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Historical test charts
# ---------------------------------------------------------------------------

def render_backtest_accuracy(predictions: list):
    """Accuracy chart + breakdown table from historical data."""
    if not predictions:
        st.info("No historical data available.")
        return

    import numpy as np
    import pandas as pd

    # Collect both raw and calibrated for the table
    raw_probs = []
    cal_probs = []
    actuals = []
    for p in predictions:
        raw = float(p["p_nrfi_combined"]) if p.get("p_nrfi_combined") is not None else None
        cal = float(p["p_nrfi_calibrated"]) if p.get("p_nrfi_calibrated") is not None else None
        result = p.get("result")
        prob = cal if cal is not None else raw
        if prob is not None and result is not None and raw is not None:
            cal_probs.append(prob)
            raw_probs.append(raw)
            actuals.append(1 if result else 0)

    if len(cal_probs) < 100:
        st.info("Not enough data.")
        return

    cal_arr = np.array(cal_probs)
    raw_arr = np.array(raw_probs)
    actuals_arr = np.array(actuals)

    # Sort by calibrated probability for binning
    n_bins = 10
    indices = np.argsort(cal_arr)
    bin_size = len(indices) // n_bins

    rows = []
    for i in range(n_bins):
        start = i * bin_size
        end = start + bin_size if i < n_bins - 1 else len(indices)
        idx = indices[start:end]
        pm = cal_arr[idx].mean()
        am = actuals_arr[idx].mean()
        # Show RAW prediction range (has real spread) rather than calibrated (step function)
        raw_min = raw_arr[idx].min()
        raw_max = raw_arr[idx].max()
        rows.append({
            "Group": f"{i + 1} of 10",
            "Raw Model Range": f"{raw_min:.1%} \u2013 {raw_max:.1%}",
            "Adjusted Prediction": f"{pm:.1%}",
            "Actually Happened": f"{am:.1%}",
            "Games": f"{len(idx):,}",
            "Difference": f"{(am - pm):+.1%}",
        })

    render_accuracy_chart(predictions)

    st.markdown("**Accuracy by Confidence Level**")
    st.caption("We split all predictions into 10 equal groups from least confident to most confident")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_backtest_season_chart(backtest_results: dict):
    """How predictions compared to reality each season."""
    per_season = backtest_results.get("per_season", {})
    if not per_season:
        st.info("No season data available.")
        return

    seasons = sorted(per_season.keys())
    nrfi_rates = [per_season[s]["nrfi_rate"] * 100 for s in seasons]
    mean_preds = [per_season[s]["mean_pred"] * 100 for s in seasons]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=seasons, y=nrfi_rates, name="What Actually Happened",
                         marker_color="#1f77b4", opacity=0.7))
    fig.add_trace(go.Scatter(x=seasons, y=mean_preds, name="What We Predicted",
                             mode="lines+markers", line=dict(color="#00cc66", width=2)))

    fig.update_layout(
        title="Season-by-Season: Predicted vs Actual NRFI Rate",
        xaxis_title="Season", yaxis_title="NRFI Rate (%)",
        template="plotly_dark", height=350,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("The green line should track the blue bars closely. "
               "If the model is accurate, predicted and actual NRFI rates match each year.")


def render_rolling_accuracy(predictions: list, window: int = 500):
    """Rolling Brier score chart to detect model accuracy drift over time."""
    if not predictions:
        return

    import numpy as np

    # Need predictions sorted by date (already sorted from query)
    dated = [(p["game_date"], _safe_prob(p.get("p_nrfi_calibrated"), p.get("p_nrfi_combined")),
              p.get("result"))
             for p in predictions
             if p.get("game_date") and p.get("result") is not None
             and _safe_prob(p.get("p_nrfi_calibrated"), p.get("p_nrfi_combined")) is not None]

    if len(dated) < window + 100:
        st.info(f"Need at least {window + 100} predictions for rolling accuracy chart.")
        return

    dates = [d[0] for d in dated]
    probs = np.array([d[1] for d in dated])
    actuals = np.array([1.0 if d[2] else 0.0 for d in dated])

    # Rolling Brier score
    brier_scores = (probs - actuals) ** 2
    rolling_brier = np.convolve(brier_scores, np.ones(window) / window, mode="valid")
    rolling_dates = dates[window - 1:]

    # No-skill baseline (always predicting the base rate)
    base_rate = actuals.mean()
    no_skill = base_rate * (1 - base_rate)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=rolling_dates, y=rolling_brier.tolist(),
        mode="lines", name=f"Model ({window}-game rolling)",
        line=dict(color="#00cc66", width=2),
    ))
    fig.add_hline(y=no_skill, line_dash="dash", line_color="#ff6666",
                  annotation_text=f"No-skill baseline ({no_skill:.4f})")
    fig.add_hline(y=0.25, line_dash="dot", line_color="#888888",
                  annotation_text="Coin flip (0.2500)")

    fig.update_layout(
        title=f"Is the Model's Accuracy Stable Over Time? ({window}-game rolling window)",
        xaxis_title="Date", yaxis_title="Accuracy Score (lower = better)",
        template="plotly_dark", height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Green line should stay BELOW the red dashed line (the no-skill baseline). "
               "If the green line rises above it, the model is performing worse than always guessing the average. "
               "Spikes are normal variance; sustained rises indicate real degradation.")


def render_prediction_distribution(predictions: list):
    """How the model's predictions are spread out."""
    if not predictions:
        return

    probs = []
    for p in predictions:
        prob = _safe_prob(p.get("p_nrfi_calibrated"), p.get("p_nrfi_combined"))
        if prob is not None:
            probs.append(prob * 100)

    if not probs:
        return

    import numpy as np
    mean_val = np.mean(probs)

    fig = go.Figure()
    fig.add_trace(go.Histogram(x=probs, nbinsx=50, marker_color="#1f77b4", opacity=0.7))

    # Avoid overlapping annotations when mean is close to 50%
    if abs(mean_val - 50.0) < 1.5:
        fig.add_vline(x=50.0, line_dash="dash", line_color="#888888")
        fig.add_vline(x=mean_val, line_dash="dash", line_color="#00cc66",
                      annotation_text=f"50% baseline / Average: {mean_val:.1f}%",
                      annotation_position="top right")
    else:
        fig.add_vline(x=50.0, line_dash="dash", line_color="#888888",
                      annotation_text="50% (coin flip)")
        fig.add_vline(x=mean_val, line_dash="dash", line_color="#00cc66",
                      annotation_text=f"Average: {mean_val:.1f}%")

    fig.update_layout(
        title=f"How Predictions Are Spread Out ({len(probs):,} games)",
        xaxis_title="NRFI Chance (%)", yaxis_title="Number of Games",
        template="plotly_dark", height=350,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("A wider spread means the model is differentiating games well. "
               "If everything clustered at 50%, the model wouldn't be useful.")


def render_high_confidence_table(predictions: list):
    """When the model is most confident, how often is it right?"""
    if not predictions:
        return

    thresholds = [0.52, 0.54, 0.56, 0.58, 0.60]
    rows = []
    for t in thresholds:
        matching = [p for p in predictions
                    if (_safe_prob(p.get("p_nrfi_calibrated"), p.get("p_nrfi_combined")) or 0) > t
                    and p.get("result") is not None]
        if matching:
            wins = sum(1 for p in matching if p["result"])
            rows.append({
                "When Model Says": f"> {t:.0%} NRFI chance",
                "Games Found": f"{len(matching):,}",
                "Actually NRFI": f"{wins:,}",
                "Actually YRFI": f"{len(matching) - wins:,}",
                "Win Rate": f"{wins / len(matching):.1%}",
            })

    if rows:
        import pandas as pd
        st.markdown("### When We're Most Confident")
        st.caption("Higher confidence thresholds find fewer games but should have higher win rates")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
