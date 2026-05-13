"""
streamlit_app.py — Interactive stock watchlist dashboard.

5 tabs:
  Home       — pipeline run summary, sector chart, top signals, score distribution
  Portfolio  — track owned positions with cost basis, P&L
  Watchlist  — candidates list; move to portfolio
  Analyze    — live on-demand analysis for any ticker (yfinance, no CORS)
  Compare    — side-by-side comparison of up to 3 tickers

Run locally:   streamlit run streamlit_app.py
Deploy:        Streamlit Community Cloud → connect repo → set main file
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Watchlist",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Colour palette ─────────────────────────────────────────────────────────────
GREEN  = "#22c55e"
YELLOW = "#eab308"
RED    = "#ef4444"
BLUE   = "#3b82f6"
INDIGO = "#6366f1"
GRAY   = "#6b7280"

LIGHT_COLORS = {"green": GREEN, "yellow": YELLOW, "red": RED, "gray": GRAY}

# ── Session-state init ─────────────────────────────────────────────────────────
_DEFAULTS = {
    "portfolio":  [],   # list of {ticker, name, shares, cost_basis}
    "watchlist":  [],   # list of ticker strings
    "compare":    [],   # up to 3 tickers
    "live_cache": {},   # ticker → yf data dict
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ══════════════════════════════════════════════════════════════════════════════
# Data helpers
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def load_manifest() -> dict:
    path = Path("public/data/manifest.json")
    if path.exists():
        return json.loads(path.read_text())
    return {"run_date": None, "total": 0, "tickers": []}


@st.cache_data(ttl=3600, show_spinner=False)
def load_ticker_json(ticker: str) -> dict | None:
    path = Path(f"public/data/{ticker}.json")
    if path.exists():
        return json.loads(path.read_text())
    return None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_live(ticker: str) -> dict | None:
    """Fetch live data from yfinance. Returns None on failure."""
    try:
        t    = yf.Ticker(ticker)
        info = t.info
        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            return None
        hist = t.history(period="1y")
        return {"info": info, "hist": hist, "ticker": ticker.upper()}
    except Exception:
        return None


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val), 1) if not np.isnan(val) else None


def compute_quick_score(data: dict) -> dict:
    """
    Compute a 5-factor live score from yfinance info + price history.
    Returns dict with per-factor scores and overall.
    """
    info  = data.get("info", {})
    hist  = data.get("hist", pd.DataFrame())
    close = hist["Close"] if not hist.empty else pd.Series([], dtype=float)

    factors = {}

    # ── 1. Valuation ──────────────────────────────────────────────────────────
    v_scores = []
    pe = info.get("forwardPE") or info.get("trailingPE")
    if pe and pe > 0:
        if pe < 15:    v_scores.append(9)
        elif pe < 25:  v_scores.append(7)
        elif pe < 40:  v_scores.append(5)
        elif pe < 60:  v_scores.append(3)
        else:          v_scores.append(1)
    target = info.get("targetMeanPrice")
    price  = info.get("currentPrice") or info.get("regularMarketPrice")
    upside = None
    if target and price:
        upside = (target - price) / price
        if upside > 0.30:  v_scores.append(9)
        elif upside > 0.15: v_scores.append(7)
        elif upside > 0.05: v_scores.append(5)
        elif upside > -0.05: v_scores.append(3)
        else:               v_scores.append(1)
    peg = info.get("pegRatio")
    if peg and peg > 0:
        if peg < 0.8:   v_scores.append(10)
        elif peg < 1.2: v_scores.append(8)
        elif peg < 2.0: v_scores.append(5)
        else:           v_scores.append(2)
    val_score = round(np.mean(v_scores), 1) if v_scores else 5.0
    factors["Valuation"] = val_score

    # ── 2. Growth ─────────────────────────────────────────────────────────────
    g_scores = []
    rg = info.get("revenueGrowth")
    if rg is not None:
        if rg > 0.30:   g_scores.append(10)
        elif rg > 0.20: g_scores.append(8)
        elif rg > 0.10: g_scores.append(6)
        elif rg > 0.0:  g_scores.append(4)
        else:           g_scores.append(2)
    eg = info.get("earningsGrowth")
    if eg is not None:
        if eg > 0.30:   g_scores.append(9)
        elif eg > 0.15: g_scores.append(7)
        elif eg > 0.0:  g_scores.append(5)
        else:           g_scores.append(2)
    growth_score = round(np.mean(g_scores), 1) if g_scores else 5.0
    factors["Growth"] = growth_score

    # ── 3. Margins & Quality ──────────────────────────────────────────────────
    q_scores = []
    gm = info.get("grossMargins")
    if gm is not None:
        if gm > 0.60:   q_scores.append(10)
        elif gm > 0.40: q_scores.append(8)
        elif gm > 0.25: q_scores.append(6)
        elif gm > 0.10: q_scores.append(4)
        else:           q_scores.append(2)
    om = info.get("operatingMargins")
    if om is not None:
        if om > 0.25:   q_scores.append(10)
        elif om > 0.15: q_scores.append(8)
        elif om > 0.05: q_scores.append(5)
        elif om > 0:    q_scores.append(3)
        else:           q_scores.append(1)
    fcf = info.get("freeCashflow")
    rev = info.get("totalRevenue")
    if fcf and rev and rev > 0:
        fcf_m = fcf / rev
        if fcf_m > 0.15:   q_scores.append(10)
        elif fcf_m > 0.08: q_scores.append(8)
        elif fcf_m > 0.02: q_scores.append(6)
        elif fcf_m > 0:    q_scores.append(4)
        else:              q_scores.append(2)
    quality_score = round(np.mean(q_scores), 1) if q_scores else 5.0
    factors["Margins"] = quality_score

    # ── 4. Analyst sentiment ──────────────────────────────────────────────────
    a_scores = []
    rec = info.get("recommendationMean")  # 1=Strong Buy, 5=Strong Sell
    if rec:
        if rec <= 1.5:  a_scores.append(9)
        elif rec <= 2.0: a_scores.append(7)
        elif rec <= 2.5: a_scores.append(5)
        elif rec <= 3.0: a_scores.append(3)
        else:            a_scores.append(1)
    if upside is not None:
        if upside > 0.25:  a_scores.append(9)
        elif upside > 0.10: a_scores.append(7)
        elif upside > 0:    a_scores.append(5)
        else:               a_scores.append(2)
    analyst_score = round(np.mean(a_scores), 1) if a_scores else 5.0
    factors["Analyst"] = analyst_score

    # ── 5. Technical / Momentum ───────────────────────────────────────────────
    t_scores = []
    rsi_val = _rsi(close) if len(close) > 0 else None
    if rsi_val is not None:
        if rsi_val < 30:        t_scores.append(7)   # oversold — entry zone
        elif rsi_val <= 65:     t_scores.append(8)   # healthy
        elif rsi_val <= 75:     t_scores.append(5)
        else:                   t_scores.append(2)   # overbought
    if len(close) >= 50:
        ma50  = float(close.rolling(50).mean().iloc[-1])
        last  = float(close.iloc[-1])
        vs50  = (last - ma50) / ma50
        if vs50 > 0.05:   t_scores.append(8)
        elif vs50 > 0:    t_scores.append(6)
        elif vs50 > -0.10: t_scores.append(4)
        else:              t_scores.append(2)
    if len(close) >= 200:
        ma200 = float(close.rolling(200).mean().iloc[-1])
        last  = float(close.iloc[-1])
        if last > ma200:  t_scores.append(7)
        else:             t_scores.append(3)
    tech_score = round(np.mean(t_scores), 1) if t_scores else 5.0
    factors["Technical"] = tech_score

    # ── Overall weighted average ───────────────────────────────────────────────
    weights   = [0.20, 0.25, 0.20, 0.20, 0.15]
    scores    = [val_score, growth_score, quality_score, analyst_score, tech_score]
    overall   = round(sum(w * s for w, s in zip(weights, scores)), 1)

    def light(s):
        if s >= 7.0:  return "🟢"
        if s >= 4.5:  return "🟡"
        return "🔴"

    return {
        "overall":  overall,
        "light":    light(overall),
        "factors":  factors,
        "price":    price,
        "upside":   upside,
        "rsi":      rsi_val,
        "rec":      rec,
        "pe":       pe,
        "peg":      peg,
        "gm":       gm,
        "om":       om,
        "rg":       rg,
        "eg":       eg,
        "fcf":      fcf,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Shared UI components
# ══════════════════════════════════════════════════════════════════════════════

def metric_card(label: str, value: str, delta: str = "", color: str = BLUE):
    st.markdown(
        f"""
        <div style="background:#1e293b;border-left:4px solid {color};
                    padding:12px 16px;border-radius:8px;margin-bottom:8px;">
          <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;
                      letter-spacing:.05em;">{label}</div>
          <div style="font-size:22px;font-weight:700;color:#f1f5f9;">{value}</div>
          {"<div style='font-size:12px;color:#94a3b8;'>"+delta+"</div>" if delta else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )


def score_badge(score: float, size: int = 28) -> str:
    color = GREEN if score >= 7 else (YELLOW if score >= 4.5 else RED)
    return (
        f'<span style="background:{color};color:#0f172a;font-weight:700;'
        f'font-size:{size}px;padding:2px 10px;border-radius:20px;">{score:.1f}</span>'
    )


def light_dot(light: str) -> str:
    color = LIGHT_COLORS.get(light, GRAY)
    return f'<span style="color:{color};font-size:18px;">●</span>'


def ticker_tile(t: dict, show_move: bool = False):
    """Compact tile card for manifest ticker rows."""
    score = t.get("score", 0)
    color = GREEN if score >= 7 else (YELLOW if score >= 4.5 else RED)
    price = t.get("price")
    chg   = t.get("price_change_1d")
    chg_s = f"{chg:+.1f}%" if chg is not None else ""
    chg_c = GREEN if (chg or 0) >= 0 else RED
    signal = t.get("signal", "")
    sector = t.get("sector", "")

    with st.container():
        st.markdown(
            f"""<div style="background:#1e293b;border-radius:10px;padding:14px 16px;
                    border-left:4px solid {color};margin-bottom:10px;">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:18px;font-weight:700;color:#f1f5f9;">{t['ticker']}</span>
                {score_badge(score, 22)}
              </div>
              <div style="font-size:12px;color:#94a3b8;margin:4px 0;">
                {t.get('name','')[:40]}
              </div>
              <div style="display:flex;gap:16px;margin-top:6px;font-size:12px;">
                {"<span style='color:#f1f5f9;'>$"+f"{price:.2f}"+"</span>" if price else ""}
                {"<span style='color:"+chg_c+";'>"+chg_s+"</span>" if chg_s else ""}
                {"<span style='color:#94a3b8;'>"+sector+"</span>" if sector else ""}
              </div>
              <div style="font-size:11px;color:#64748b;margin-top:4px;">{signal}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        if show_move:
            if st.button(f"➕ Add to Portfolio", key=f"move_{t['ticker']}_{show_move}"):
                if t["ticker"] not in [p["ticker"] for p in st.session_state.portfolio]:
                    st.session_state.portfolio.append({
                        "ticker": t["ticker"],
                        "name": t.get("name", ""),
                        "shares": 0,
                        "cost_basis": 0.0,
                    })
                    st.success(f"Added {t['ticker']} to Portfolio!")
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB: Home
# ══════════════════════════════════════════════════════════════════════════════

def tab_home(manifest: dict):
    tickers = manifest.get("tickers", [])
    run_date = manifest.get("run_date", "Never")

    st.markdown("### 📊 Portfolio Overview")

    if not tickers:
        st.info(
            "No pipeline data yet. Trigger the **Weekly watchlist digest** workflow "
            "in GitHub Actions (group: owned) to populate this dashboard.",
            icon="⚙️",
        )
        return

    # ── Stat cards ─────────────────────────────────────────────────────────────
    strong = [t for t in tickers if t.get("score", 0) >= 7]
    weak   = [t for t in tickers if t.get("score", 0) < 4.5]
    flips  = [t for t in tickers if t.get("signal_flipped")]

    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Total Tickers",   str(len(tickers)),  f"Last run: {run_date}", BLUE)
    with c2: metric_card("Strong (≥7.0)",   str(len(strong)),   "Score green",           GREEN)
    with c3: metric_card("Weak (<4.5)",     str(len(weak)),     "Score red",             RED)
    with c4: metric_card("Signal Flips",    str(len(flips)),    "This week",             YELLOW)

    st.markdown("---")

    col_left, col_right = st.columns([1, 1])

    # ── Sector allocation ──────────────────────────────────────────────────────
    with col_left:
        st.markdown("#### Sector Allocation")
        sector_counts: dict[str, int] = {}
        for t in tickers:
            s = t.get("sector") or "Unknown"
            sector_counts[s] = sector_counts.get(s, 0) + 1
        df_sec = pd.DataFrame(
            [{"Sector": k, "Count": v} for k, v in sorted(sector_counts.items(), key=lambda x: -x[1])]
        )
        fig_pie = px.pie(
            df_sec, names="Sector", values="Count",
            hole=0.45,
            color_discrete_sequence=px.colors.qualitative.Set3,
        )
        fig_pie.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#94a3b8",
            legend=dict(orientation="v", font_size=11),
            margin=dict(l=0, r=0, t=20, b=0),
            showlegend=True,
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    # ── Score distribution ────────────────────────────────────────────────────
    with col_right:
        st.markdown("#### Score Distribution")
        scores = [t["score"] for t in tickers if t.get("score") is not None]
        bins   = [0, 3, 5, 7, 8, 10]
        labels = ["<3 Bear", "3–5 Weak", "5–7 Neutral", "7–8 Strong", "8+ Elite"]
        counts = [0] * len(labels)
        for s in scores:
            for i in range(len(bins) - 1):
                if bins[i] <= s < bins[i + 1] or (i == len(bins) - 2 and s == bins[-1]):
                    counts[i] += 1
                    break
        colors_dist = [RED, "#f97316", YELLOW, GREEN, INDIGO]
        fig_bar = go.Figure(go.Bar(
            x=labels, y=counts,
            marker_color=colors_dist,
            text=counts, textposition="outside",
        ))
        fig_bar.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#94a3b8",
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=True, gridcolor="#334155"),
            margin=dict(l=0, r=0, t=20, b=0),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    # ── Top signals ────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 🔔 Top Signals This Week")

    flip_tickers = [t for t in tickers if t.get("signal_flipped")]
    top_tickers  = sorted(tickers, key=lambda x: x.get("score", 0), reverse=True)[:5]

    col_sig, col_top = st.columns(2)
    with col_sig:
        if flip_tickers:
            st.markdown("**Signal Flips**")
            for t in flip_tickers[:5]:
                sig  = t.get("signal", "")
                scr  = t.get("score", 0)
                icon = "🟢" if "BUY" in sig.upper() else "🔴"
                st.markdown(
                    f"{icon} **{t['ticker']}** — {sig} &nbsp;"
                    f"{score_badge(scr, 16)}",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No signal flips this run.")

    with col_top:
        st.markdown("**Highest Scored**")
        for t in top_tickers:
            price_s = f"${t['price']:.2f}" if t.get("price") else ""
            st.markdown(
                f"🏆 **{t['ticker']}** {price_s} &nbsp; {score_badge(t['score'], 16)}",
                unsafe_allow_html=True,
            )

    # ── Score history sparkline (if history CSV exists) ────────────────────────
    history_dir = Path("data/history")
    if history_dir.exists():
        csv_files = sorted(history_dir.glob("*.csv"))
        if csv_files:
            st.markdown("---")
            st.markdown("#### 📈 Score Trend (last 8 weeks)")
            ticker_options = [t["ticker"] for t in top_tickers]
            sel = st.multiselect(
                "Select tickers", ticker_options, default=ticker_options[:3],
                key="home_spark",
            )
            if sel:
                dfs = []
                for csv_f in csv_files[-8:]:
                    try:
                        df_w = pd.read_csv(csv_f)
                        df_w["week"] = csv_f.stem
                        dfs.append(df_w)
                    except Exception:
                        pass
                if dfs:
                    df_hist = pd.concat(dfs)
                    df_hist = df_hist[df_hist["ticker"].isin(sel)]
                    fig_line = px.line(
                        df_hist, x="week", y="score", color="ticker",
                        markers=True,
                        color_discrete_sequence=px.colors.qualitative.Pastel,
                    )
                    fig_line.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font_color="#94a3b8",
                        yaxis=dict(range=[0, 10], gridcolor="#334155"),
                        xaxis=dict(showgrid=False),
                        margin=dict(l=0, r=0, t=10, b=0),
                    )
                    st.plotly_chart(fig_line, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB: Portfolio
# ══════════════════════════════════════════════════════════════════════════════

def tab_portfolio(manifest: dict):
    manifest_map = {t["ticker"]: t for t in manifest.get("tickers", [])}

    st.markdown("### 💼 My Portfolio")

    # ── Add position form ──────────────────────────────────────────────────────
    with st.expander("➕ Add / update position", expanded=not st.session_state.portfolio):
        c1, c2, c3, c4 = st.columns([2, 1.5, 2, 1])
        with c1: new_ticker = st.text_input("Ticker", placeholder="AAPL", key="p_ticker").upper().strip()
        with c2: new_shares = st.number_input("Shares", min_value=0.0, step=1.0, key="p_shares")
        with c3: new_cost   = st.number_input("Cost basis / share ($)", min_value=0.0, step=0.01, key="p_cost")
        with c4:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Add", type="primary", key="p_add"):
                if new_ticker:
                    existing = [p for p in st.session_state.portfolio if p["ticker"] == new_ticker]
                    if existing:
                        existing[0]["shares"]     = new_shares
                        existing[0]["cost_basis"] = new_cost
                    else:
                        m = manifest_map.get(new_ticker, {})
                        st.session_state.portfolio.append({
                            "ticker":     new_ticker,
                            "name":       m.get("name", ""),
                            "shares":     new_shares,
                            "cost_basis": new_cost,
                        })
                    st.rerun()

    # ── CSV import / export ────────────────────────────────────────────────────
    col_imp, col_exp = st.columns(2)
    with col_imp:
        up = st.file_uploader("Import CSV (ticker,shares,cost_basis)", type="csv", key="p_import")
        if up:
            try:
                df_up = pd.read_csv(up)
                for _, row in df_up.iterrows():
                    tkr = str(row.get("ticker", "")).upper().strip()
                    if tkr:
                        m = manifest_map.get(tkr, {})
                        st.session_state.portfolio.append({
                            "ticker":     tkr,
                            "name":       m.get("name", ""),
                            "shares":     float(row.get("shares", 0)),
                            "cost_basis": float(row.get("cost_basis", 0)),
                        })
                st.success(f"Imported {len(df_up)} positions.")
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")
    with col_exp:
        if st.session_state.portfolio:
            df_exp = pd.DataFrame(st.session_state.portfolio)[["ticker", "shares", "cost_basis"]]
            st.download_button(
                "⬇ Export CSV", df_exp.to_csv(index=False),
                file_name="portfolio.csv", mime="text/csv",
            )

    if not st.session_state.portfolio:
        st.info("No positions yet. Add your first ticker above or import a CSV.")
        return

    # ── Fetch live prices and compute P&L ─────────────────────────────────────
    st.markdown("---")
    rows = []
    total_cost  = 0.0
    total_value = 0.0

    for pos in st.session_state.portfolio:
        tkr    = pos["ticker"]
        shares = pos.get("shares", 0)
        cost   = pos.get("cost_basis", 0.0)

        # Try manifest first (fast), then live yf
        m_data  = manifest_map.get(tkr, {})
        cur_price = m_data.get("price")
        if cur_price is None:
            with st.spinner(f"Fetching {tkr}…"):
                live = fetch_live(tkr)
            if live:
                cur_price = live["info"].get("currentPrice") or live["info"].get("regularMarketPrice")

        mkt_val  = (cur_price * shares) if (cur_price and shares) else None
        cost_val = cost * shares if shares else 0
        pnl      = (mkt_val - cost_val) if mkt_val is not None else None
        pnl_pct  = (pnl / cost_val * 100) if (pnl is not None and cost_val > 0) else None

        if mkt_val:  total_value += mkt_val
        total_cost  += cost_val

        rows.append({
            "Ticker":       tkr,
            "Name":         pos.get("name", m_data.get("name", "")),
            "Shares":       shares,
            "Cost/sh":      f"${cost:.2f}" if cost else "—",
            "Current":      f"${cur_price:.2f}" if cur_price else "—",
            "Market Val":   f"${mkt_val:,.0f}" if mkt_val else "—",
            "P&L ($)":      f"{pnl:+,.0f}" if pnl is not None else "—",
            "P&L (%)":      f"{pnl_pct:+.1f}%" if pnl_pct is not None else "—",
            "Score":        m_data.get("score", "—"),
            "Signal":       m_data.get("signal", "—"),
            "_pnl_pct_raw": pnl_pct or 0,
            "_ticker":      tkr,
        })

    # ── P&L summary bar ────────────────────────────────────────────────────────
    total_pnl     = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    pnl_color     = GREEN if total_pnl >= 0 else RED

    c1, c2, c3 = st.columns(3)
    with c1: metric_card("Portfolio Value",  f"${total_value:,.0f}" if total_value else "—", "", BLUE)
    with c2: metric_card("Total Cost",       f"${total_cost:,.0f}",  "", GRAY)
    with c3: metric_card("Total P&L",
                         f"${total_pnl:+,.0f}" if total_value else "—",
                         f"{total_pnl_pct:+.1f}%" if total_value else "",
                         pnl_color)

    # ── P&L bar chart ──────────────────────────────────────────────────────────
    chart_rows = [r for r in rows if r["_pnl_pct_raw"] != 0]
    if chart_rows:
        df_chart = pd.DataFrame(chart_rows).sort_values("_pnl_pct_raw")
        colors_bar = [GREEN if v >= 0 else RED for v in df_chart["_pnl_pct_raw"]]
        fig_pnl = go.Figure(go.Bar(
            x=df_chart["Ticker"],
            y=df_chart["_pnl_pct_raw"],
            marker_color=colors_bar,
            text=[f"{v:+.1f}%" for v in df_chart["_pnl_pct_raw"]],
            textposition="outside",
        ))
        fig_pnl.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#94a3b8",
            yaxis=dict(title="P&L %", gridcolor="#334155"),
            xaxis=dict(showgrid=False),
            margin=dict(l=0, r=0, t=20, b=0),
        )
        st.plotly_chart(fig_pnl, use_container_width=True)

    # ── Positions table ────────────────────────────────────────────────────────
    st.markdown("#### Positions")
    display_cols = ["Ticker", "Name", "Shares", "Cost/sh", "Current",
                    "Market Val", "P&L ($)", "P&L (%)", "Score", "Signal"]
    st.dataframe(
        pd.DataFrame(rows)[display_cols],
        use_container_width=True,
        hide_index=True,
    )

    # ── Remove button ──────────────────────────────────────────────────────────
    remove_sel = st.selectbox(
        "Remove position",
        ["— select —"] + [r["Ticker"] for r in rows],
        key="p_remove_sel",
    )
    if remove_sel and remove_sel != "— select —":
        if st.button(f"🗑 Remove {remove_sel}", key="p_remove_btn"):
            st.session_state.portfolio = [
                p for p in st.session_state.portfolio if p["ticker"] != remove_sel
            ]
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB: Watchlist
# ══════════════════════════════════════════════════════════════════════════════

def tab_watchlist(manifest: dict):
    manifest_map  = {t["ticker"]: t for t in manifest.get("tickers", [])}
    portfolio_set = {p["ticker"] for p in st.session_state.portfolio}

    st.markdown("### 👁 Watchlist Candidates")

    # ── Add ticker ──────────────────────────────────────────────────────────────
    c1, c2 = st.columns([3, 1])
    with c1:
        new_wl = st.text_input("Add ticker to watchlist", placeholder="TSLA", key="wl_add").upper().strip()
    with c2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Add", key="wl_add_btn", type="primary"):
            if new_wl and new_wl not in st.session_state.watchlist:
                st.session_state.watchlist.append(new_wl)
                st.rerun()

    # ── Import pipeline watchlist group ────────────────────────────────────────
    wl_from_manifest = [t for t in manifest.get("tickers", [])
                        if t["ticker"] not in portfolio_set]
    if wl_from_manifest and st.button("⬇ Import pipeline watchlist tickers"):
        for t in wl_from_manifest:
            if t["ticker"] not in st.session_state.watchlist:
                st.session_state.watchlist.append(t["ticker"])
        st.rerun()

    if not st.session_state.watchlist and not wl_from_manifest:
        st.info("No tickers on watchlist. Add one above or run the pipeline.")
        return

    # ── Show watchlist tiles ───────────────────────────────────────────────────
    display_tickers = st.session_state.watchlist or [t["ticker"] for t in wl_from_manifest]

    cols = st.columns(3)
    for i, tkr in enumerate(display_tickers):
        with cols[i % 3]:
            t_data = manifest_map.get(tkr, {"ticker": tkr, "score": 0})
            ticker_tile(t_data, show_move=True)
            if st.button(f"✕ Remove", key=f"wl_remove_{tkr}"):
                st.session_state.watchlist = [x for x in st.session_state.watchlist if x != tkr]
                st.rerun()

    # ── Export ──────────────────────────────────────────────────────────────────
    if display_tickers:
        df_wl = pd.DataFrame({"ticker": display_tickers})
        st.download_button(
            "⬇ Export watchlist CSV",
            df_wl.to_csv(index=False),
            file_name="watchlist.csv",
            mime="text/csv",
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB: Analyze (Live On-Demand)
# ══════════════════════════════════════════════════════════════════════════════

def tab_analyze(manifest: dict):
    manifest_map = {t["ticker"]: t for t in manifest.get("tickers", [])}

    st.markdown("### ⚡ Live On-Demand Analysis")
    st.caption("Type any ticker — data fetched live from Yahoo Finance via Python (no CORS).")

    # ── Input ───────────────────────────────────────────────────────────────────
    c1, c2 = st.columns([3, 1])
    with c1:
        query = st.text_input("Ticker symbol", placeholder="CRDO, NVDA, AAPL…",
                              key="analyze_input").upper().strip()
    with c2:
        st.markdown("<br>", unsafe_allow_html=True)
        run_btn = st.button("Analyze ⚡", type="primary", key="analyze_run")

    if not query and not st.session_state.live_cache:
        st.info("Enter a ticker above to fetch live data instantly.")

    # ── Run analysis ───────────────────────────────────────────────────────────
    if run_btn and query:
        with st.spinner(f"Fetching {query} from Yahoo Finance…"):
            live = fetch_live(query)
        if live is None:
            st.error(
                f"❌ Could not fetch data for **{query}**. "
                "Check the ticker spelling (e.g. CRDO not CDRO).",
                icon="🚫",
            )
        else:
            st.session_state.live_cache[query] = live
            st.success(f"✅ {query} loaded!", icon="📡")

    # ── Display all cached results ─────────────────────────────────────────────
    for tkr, live in st.session_state.live_cache.items():
        info  = live["info"]
        hist  = live["hist"]
        score = compute_quick_score(live)

        overall_color = GREEN if score["overall"] >= 7 else (YELLOW if score["overall"] >= 4.5 else RED)
        price  = score.get("price") or 0
        name   = info.get("shortName") or info.get("longName") or tkr
        sector = info.get("sector") or "—"

        with st.expander(
            f"**{tkr}** — {name} &nbsp; | &nbsp; Score: {score['overall']:.1f} {score['light']} &nbsp; | &nbsp; ${price:.2f}",
            expanded=True,
        ):
            # ── Top metrics ────────────────────────────────────────────────────
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                st.metric("Price", f"${price:.2f}")
            with c2:
                upside = score.get("upside")
                target = info.get("targetMeanPrice")
                st.metric("Analyst Target",
                          f"${target:.2f}" if target else "—",
                          f"{upside*100:+.1f}%" if upside is not None else "")
            with c3:
                st.metric("RSI (14)", f"{score['rsi']:.0f}" if score.get("rsi") else "—")
            with c4:
                pe = score.get("pe")
                st.metric("Fwd P/E", f"{pe:.1f}x" if pe else "—")
            with c5:
                rec_map = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Sell", 5: "Strong Sell"}
                rec = score.get("rec")
                st.metric("Analyst Rec", rec_map.get(round(rec) if rec else 0, "—") if rec else "—")

            # ── Score radar chart ──────────────────────────────────────────────
            col_radar, col_detail = st.columns([1, 1])
            with col_radar:
                factors = score["factors"]
                fig_radar = go.Figure(go.Scatterpolar(
                    r=list(factors.values()),
                    theta=list(factors.keys()),
                    fill="toself",
                    line_color=overall_color,
                    fillcolor=overall_color.replace(")", ",0.2)").replace("rgb", "rgba") if "rgb" in overall_color else overall_color + "33",
                ))
                fig_radar.update_layout(
                    polar=dict(
                        radialaxis=dict(range=[0, 10], showticklabels=True, tickfont_size=9),
                        bgcolor="rgba(0,0,0,0)",
                    ),
                    paper_bgcolor="rgba(0,0,0,0)",
                    font_color="#94a3b8",
                    margin=dict(l=30, r=30, t=30, b=30),
                    showlegend=False,
                )
                st.plotly_chart(fig_radar, use_container_width=True)

            with col_detail:
                st.markdown(f"#### {score['light']} Overall Score: **{score['overall']:.1f} / 10**")
                st.markdown(f"**Sector:** {sector}")
                st.markdown(f"**Market Cap:** {_fmt_large(info.get('marketCap'))}")
                st.markdown(f"**52w Range:** ${info.get('fiftyTwoWeekLow',0):.2f} – ${info.get('fiftyTwoWeekHigh',0):.2f}")

                st.markdown("**Factor Breakdown:**")
                for fname, fscore in factors.items():
                    light = "🟢" if fscore >= 7 else ("🟡" if fscore >= 4.5 else "🔴")
                    bar = "█" * int(fscore) + "░" * (10 - int(fscore))
                    st.markdown(
                        f"{light} **{fname}**: {fscore:.1f} &nbsp;"
                        f"<span style='color:#334155;font-size:10px;'>{bar}</span>",
                        unsafe_allow_html=True,
                    )

            # ── Price chart ────────────────────────────────────────────────────
            if not hist.empty:
                fig_price = go.Figure()
                fig_price.add_trace(go.Scatter(
                    x=hist.index, y=hist["Close"],
                    mode="lines", name="Price",
                    line=dict(color=overall_color, width=2),
                ))
                if len(hist) >= 50:
                    fig_price.add_trace(go.Scatter(
                        x=hist.index,
                        y=hist["Close"].rolling(50).mean(),
                        mode="lines", name="50-day MA",
                        line=dict(color="#3b82f6", width=1, dash="dash"),
                    ))
                if len(hist) >= 200:
                    fig_price.add_trace(go.Scatter(
                        x=hist.index,
                        y=hist["Close"].rolling(200).mean(),
                        mode="lines", name="200-day MA",
                        line=dict(color="#f59e0b", width=1, dash="dot"),
                    ))
                fig_price.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#94a3b8",
                    xaxis=dict(showgrid=False),
                    yaxis=dict(gridcolor="#334155"),
                    legend=dict(orientation="h", y=1.1),
                    margin=dict(l=0, r=0, t=30, b=0),
                    height=280,
                )
                st.plotly_chart(fig_price, use_container_width=True)

            # ── Volume ─────────────────────────────────────────────────────────
            if not hist.empty and "Volume" in hist.columns:
                avg_vol = hist["Volume"].mean()
                last_vol = hist["Volume"].iloc[-1]
                vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1
                vol_color = GREEN if vol_ratio > 1.2 else (RED if vol_ratio < 0.6 else YELLOW)
                st.markdown(
                    f"**Volume:** {_fmt_large(int(last_vol))} "
                    f"<span style='color:{vol_color};'>({vol_ratio:.1f}x avg)</span>",
                    unsafe_allow_html=True,
                )

            # ── Key financials ─────────────────────────────────────────────────
            st.markdown("**Key Financials:**")
            fin_cols = st.columns(4)
            fin_items = [
                ("Rev Growth",    _pct_fmt(info.get("revenueGrowth"))),
                ("Gross Margin",  _pct_fmt(info.get("grossMargins"))),
                ("Op Margin",     _pct_fmt(info.get("operatingMargins"))),
                ("FCF",           _fmt_large(info.get("freeCashflow"))),
                ("Total Revenue", _fmt_large(info.get("totalRevenue"))),
                ("D/E Ratio",     f"{info.get('debtToEquity',0):.0f}%" if info.get("debtToEquity") else "—"),
                ("Beta",          f"{info.get('beta',0):.2f}" if info.get("beta") else "—"),
                ("Short Float",   f"{info.get('shortPercentOfFloat',0)*100:.1f}%" if info.get("shortPercentOfFloat") else "—"),
            ]
            for i, (label, val) in enumerate(fin_items):
                with fin_cols[i % 4]:
                    st.metric(label, val)

            # ── Analyst coverage ────────────────────────────────────────────────
            n_analysts = info.get("numberOfAnalystOpinions") or info.get("recommendationKey")
            if n_analysts and isinstance(n_analysts, int):
                st.caption(f"Based on {n_analysts} analyst opinions.")

            # ── Remove button ──────────────────────────────────────────────────
            if st.button(f"✕ Clear {tkr}", key=f"clear_{tkr}"):
                del st.session_state.live_cache[tkr]
                st.rerun()

    # ── Pipeline grid (from manifest) ──────────────────────────────────────────
    if manifest_map:
        st.markdown("---")
        st.markdown("#### 📋 Pipeline Results (last run)")
        search = st.text_input("Filter by ticker or sector", key="analyze_filter").upper().strip()
        all_t  = manifest.get("tickers", [])
        if search:
            all_t = [t for t in all_t if search in t["ticker"] or search in (t.get("sector") or "").upper()]
        cols = st.columns(3)
        for i, t in enumerate(all_t):
            with cols[i % 3]:
                ticker_tile(t)


# ══════════════════════════════════════════════════════════════════════════════
# TAB: Compare
# ══════════════════════════════════════════════════════════════════════════════

def tab_compare(manifest: dict):
    manifest_map = {t["ticker"]: t for t in manifest.get("tickers", [])}

    st.markdown("### 🔍 Compare Tickers")
    st.caption("Compare up to 3 tickers side-by-side with live data.")

    # ── Ticker input ────────────────────────────────────────────────────────────
    cols = st.columns(3)
    inputs = []
    for i, col in enumerate(cols):
        with col:
            val = st.text_input(f"Ticker {i+1}", key=f"cmp_{i}").upper().strip()
            inputs.append(val)

    run_cmp = st.button("Compare ⚡", type="primary", key="cmp_run")

    tickers_to_compare = [t for t in inputs if t]
    if not tickers_to_compare:
        st.info("Enter up to 3 tickers above and click Compare.")
        return

    if run_cmp:
        # Fetch live data for all tickers
        live_data = {}
        for tkr in tickers_to_compare:
            with st.spinner(f"Fetching {tkr}…"):
                d = fetch_live(tkr)
            if d:
                live_data[tkr] = d
            else:
                st.warning(f"⚠ Could not fetch {tkr}")
        st.session_state.compare = live_data

    if not st.session_state.compare:
        return

    live_data = st.session_state.compare

    # ── Score comparison bar chart ──────────────────────────────────────────────
    scores = {tkr: compute_quick_score(d) for tkr, d in live_data.items()}

    factor_names = ["Valuation", "Growth", "Margins", "Analyst", "Technical"]
    fig_cmp = go.Figure()
    colors_cmp = [INDIGO, GREEN, YELLOW]
    for idx, (tkr, sc) in enumerate(scores.items()):
        fig_cmp.add_trace(go.Bar(
            name=tkr,
            x=factor_names,
            y=[sc["factors"].get(f, 0) for f in factor_names],
            marker_color=colors_cmp[idx % len(colors_cmp)],
        ))
    fig_cmp.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#94a3b8",
        yaxis=dict(range=[0, 10], gridcolor="#334155"),
        xaxis=dict(showgrid=False),
        legend=dict(orientation="h", y=1.1),
        margin=dict(l=0, r=0, t=30, b=0),
        height=300,
    )
    st.plotly_chart(fig_cmp, use_container_width=True)

    # ── Side-by-side metrics table ─────────────────────────────────────────────
    st.markdown("#### Metrics Comparison")
    metric_rows = []
    labels = [
        ("Overall Score",  lambda sc, inf: f"{sc['overall']:.1f} {sc['light']}"),
        ("Price",          lambda sc, inf: f"${sc.get('price',0):.2f}" if sc.get("price") else "—"),
        ("Analyst Target", lambda sc, inf: f"${inf.get('targetMeanPrice',0):.2f}" if inf.get("targetMeanPrice") else "—"),
        ("Upside",         lambda sc, inf: f"{sc.get('upside',0)*100:+.1f}%" if sc.get("upside") is not None else "—"),
        ("Fwd P/E",        lambda sc, inf: f"{sc.get('pe'):.1f}x" if sc.get("pe") else "—"),
        ("PEG",            lambda sc, inf: f"{sc.get('peg'):.2f}" if sc.get("peg") else "—"),
        ("Rev Growth",     lambda sc, inf: f"{inf.get('revenueGrowth',0)*100:+.1f}%" if inf.get("revenueGrowth") is not None else "—"),
        ("Gross Margin",   lambda sc, inf: f"{inf.get('grossMargins',0)*100:.1f}%" if inf.get("grossMargins") is not None else "—"),
        ("Op Margin",      lambda sc, inf: f"{inf.get('operatingMargins',0)*100:.1f}%" if inf.get("operatingMargins") is not None else "—"),
        ("RSI",            lambda sc, inf: f"{sc.get('rsi'):.0f}" if sc.get("rsi") else "—"),
        ("Market Cap",     lambda sc, inf: _fmt_large(inf.get("marketCap"))),
        ("Beta",           lambda sc, inf: f"{inf.get('beta'):.2f}" if inf.get("beta") else "—"),
        ("Short Float",    lambda sc, inf: f"{inf.get('shortPercentOfFloat',0)*100:.1f}%" if inf.get("shortPercentOfFloat") else "—"),
    ]
    for label, fn in labels:
        row = {"Metric": label}
        for tkr, d in live_data.items():
            sc  = scores[tkr]
            inf = d["info"]
            row[tkr] = fn(sc, inf)
        metric_rows.append(row)

    df_cmp = pd.DataFrame(metric_rows).set_index("Metric")
    st.dataframe(df_cmp, use_container_width=True)

    # ── Overlaid price chart ────────────────────────────────────────────────────
    st.markdown("#### Price History (normalised to 100)")
    fig_price = go.Figure()
    colors_cmp2 = [INDIGO, GREEN, YELLOW]
    for idx, (tkr, d) in enumerate(live_data.items()):
        hist = d["hist"]
        if hist.empty:
            continue
        close = hist["Close"]
        norm  = (close / close.iloc[0]) * 100
        fig_price.add_trace(go.Scatter(
            x=hist.index, y=norm, mode="lines",
            name=tkr, line=dict(color=colors_cmp2[idx % len(colors_cmp2)], width=2),
        ))
    fig_price.add_hline(y=100, line_dash="dash", line_color="#475569", line_width=1)
    fig_price.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#94a3b8",
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="#334155", title="Normalised (base=100)"),
        legend=dict(orientation="h", y=1.1),
        margin=dict(l=0, r=0, t=30, b=0),
        height=350,
    )
    st.plotly_chart(fig_price, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_large(val) -> str:
    if val is None:
        return "—"
    val = float(val)
    if abs(val) >= 1e12:  return f"${val/1e12:.2f}T"
    if abs(val) >= 1e9:   return f"${val/1e9:.2f}B"
    if abs(val) >= 1e6:   return f"${val/1e6:.1f}M"
    return f"${val:,.0f}"

def _pct_fmt(val) -> str:
    if val is None:
        return "—"
    return f"{float(val)*100:.1f}%"


# ══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ══════════════════════════════════════════════════════════════════════════════

def render_sidebar(manifest: dict):
    with st.sidebar:
        st.markdown("## 📈 Stock Watchlist")
        st.caption("Powered by Python + yfinance")
        st.markdown("---")

        run_date = manifest.get("run_date")
        if run_date:
            st.success(f"✅ Pipeline data loaded")
            st.caption(f"Last run: {run_date}")
        else:
            st.warning("⚠ No pipeline data yet")
            st.caption("Trigger **Weekly watchlist digest** in GitHub Actions.")

        total = manifest.get("total", 0)
        if total:
            tickers = manifest.get("tickers", [])
            strong  = sum(1 for t in tickers if t.get("score", 0) >= 7)
            st.metric("Tickers", total)
            st.metric("Strong (≥7)", strong)

        st.markdown("---")
        st.markdown("**Portfolio**")
        n_pos = len(st.session_state.portfolio)
        st.metric("Positions", n_pos)

        st.markdown("**Watchlist**")
        n_wl = len(st.session_state.watchlist)
        st.metric("Candidates", n_wl)

        st.markdown("---")
        st.caption(
            "Data: Yahoo Finance (yfinance)\n\n"
            "Scores: live 5-factor model\n\n"
            "Pipeline: Mon & Thu 6:35 AM PDT"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    manifest = load_manifest()
    render_sidebar(manifest)

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🏠 Home", "💼 Portfolio", "👁 Watchlist", "⚡ Analyze", "🔍 Compare"
    ])

    with tab1:
        tab_home(manifest)
    with tab2:
        tab_portfolio(manifest)
    with tab3:
        tab_watchlist(manifest)
    with tab4:
        tab_analyze(manifest)
    with tab5:
        tab_compare(manifest)


if __name__ == "__main__":
    main()
