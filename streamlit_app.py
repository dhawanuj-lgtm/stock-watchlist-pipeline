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


def _sf(val) -> float | None:
    """Safe float: returns None for None, NaN, Inf, or non-numeric strings."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f or f == float("inf") or f == float("-inf")) else f
    except (ValueError, TypeError):
        return None

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


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_news(ticker: str) -> list[dict]:
    """Fetch recent news headlines from yfinance."""
    try:
        return yf.Ticker(ticker).news or []
    except Exception:
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def generate_ai_thesis(ticker: str, score: dict) -> str | None:
    """
    Generate a 2-sentence investment thesis via Claude Haiku.
    Returns None gracefully if ANTHROPIC_API_KEY is not set.
    score dict is passed as a hashable cache key via str conversion.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        factors = score.get("factors", {})
        factor_str = ", ".join(f"{k}: {v}/10" for k, v in factors.items())
        prompt = (
            f"Write exactly 2 punchy sentences as an investment thesis for {ticker}. "
            f"Data: Overall score {score.get('overall')}/10, {factor_str}. "
            f"Price ${score.get('price', 0):.2f}, analyst upside "
            f"{(score.get('upside') or 0)*100:.1f}%, RSI {score.get('rsi') or 'N/A'}. "
            "Be specific, use the actual numbers, no filler words like 'overall' or 'demonstrates'."
        )
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_finnhub(ticker: str) -> dict:
    """Fetch Finnhub data: analyst recommendations, earnings surprises, news sentiment."""
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return {}
    try:
        import requests as _req
        base    = "https://finnhub.io/api/v1"
        headers = {"X-Finnhub-Token": key}
        result  = {}

        r = _req.get(f"{base}/stock/recommendation?symbol={ticker}", headers=headers, timeout=8)
        if r.ok and r.json():
            result["recommendations"] = r.json()[:3]          # last 3 months

        r = _req.get(f"{base}/stock/earnings?symbol={ticker}", headers=headers, timeout=8)
        if r.ok and r.json():
            result["earnings"] = r.json()[:4]                 # last 4 quarters

        r = _req.get(f"{base}/news-sentiment?symbol={ticker}", headers=headers, timeout=8)
        if r.ok:
            data = r.json()
            if data.get("sentiment"):
                result["sentiment"] = data["sentiment"]
                result["buzz"]      = data.get("buzz", {})

        return result
    except Exception:
        return {}


@st.cache_data(ttl=7200, show_spinner=False)
def fetch_macro_context() -> dict:
    """Fetch macro snapshot from FRED: Fed Funds Rate, 10Y yield, CPI YoY."""
    fred_key = os.environ.get("FRED_API_KEY", "")
    try:
        import requests as _req
        base = "https://api.stlouisfed.org/fred/series/observations"

        def _get(series_id, limit=1):
            params = {"series_id": series_id, "sort_order": "desc",
                      "limit": limit, "file_type": "json"}
            if fred_key:
                params["api_key"] = fred_key
            r = _req.get(base, params=params, timeout=8)
            return [o for o in r.json().get("observations", []) if o.get("value") != "."] if r.ok else []

        result = {}
        fed = _get("FEDFUNDS")
        if fed:
            result["fed_rate"] = float(fed[0]["value"])
            result["fed_date"] = fed[0]["date"]

        t10 = _get("DGS10")
        if t10:
            result["t10y"] = float(t10[0]["value"])

        cpi = _get("CPIAUCSL", limit=13)
        if len(cpi) >= 13:
            result["cpi_yoy"] = round(
                (float(cpi[0]["value"]) - float(cpi[12]["value"])) / float(cpi[12]["value"]) * 100, 1
            )
        return result
    except Exception:
        return {}


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
    pe = _sf(info.get("forwardPE")) or _sf(info.get("trailingPE"))
    if pe and pe > 0:
        if pe < 15:    v_scores.append(9)
        elif pe < 25:  v_scores.append(7)
        elif pe < 40:  v_scores.append(5)
        elif pe < 60:  v_scores.append(3)
        else:          v_scores.append(1)
    target = _sf(info.get("targetMeanPrice"))
    price  = _sf(info.get("currentPrice")) or _sf(info.get("regularMarketPrice"))
    upside = None
    if target and price:
        upside = (target - price) / price
        if upside > 0.30:    v_scores.append(9)
        elif upside > 0.15:  v_scores.append(7)
        elif upside > 0.05:  v_scores.append(5)
        elif upside > -0.05: v_scores.append(3)
        else:                v_scores.append(1)
    peg = _sf(info.get("pegRatio"))
    if peg and peg > 0:
        if peg < 0.8:   v_scores.append(10)
        elif peg < 1.2: v_scores.append(8)
        elif peg < 2.0: v_scores.append(5)
        else:           v_scores.append(2)
    val_score = round(np.mean(v_scores), 1) if v_scores else 5.0
    factors["Valuation"] = val_score

    # ── 2. Growth ─────────────────────────────────────────────────────────────
    g_scores = []
    rg = _sf(info.get("revenueGrowth"))
    if rg is not None:
        if rg > 0.30:   g_scores.append(10)
        elif rg > 0.20: g_scores.append(8)
        elif rg > 0.10: g_scores.append(6)
        elif rg > 0.0:  g_scores.append(4)
        else:           g_scores.append(2)
    eg = _sf(info.get("earningsGrowth"))
    if eg is not None:
        if eg > 0.30:   g_scores.append(9)
        elif eg > 0.15: g_scores.append(7)
        elif eg > 0.0:  g_scores.append(5)
        else:           g_scores.append(2)
    growth_score = round(np.mean(g_scores), 1) if g_scores else 5.0
    factors["Growth"] = growth_score

    # ── 3. Margins & Quality ──────────────────────────────────────────────────
    q_scores = []
    gm = _sf(info.get("grossMargins"))
    if gm is not None:
        if gm > 0.60:   q_scores.append(10)
        elif gm > 0.40: q_scores.append(8)
        elif gm > 0.25: q_scores.append(6)
        elif gm > 0.10: q_scores.append(4)
        else:           q_scores.append(2)
    om = _sf(info.get("operatingMargins"))
    if om is not None:
        if om > 0.25:   q_scores.append(10)
        elif om > 0.15: q_scores.append(8)
        elif om > 0.05: q_scores.append(5)
        elif om > 0:    q_scores.append(3)
        else:           q_scores.append(1)
    fcf = _sf(info.get("freeCashflow"))
    rev = _sf(info.get("totalRevenue"))
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
    rec = _sf(info.get("recommendationMean"))  # 1=Strong Buy, 5=Strong Sell
    if rec:
        if rec <= 1.5:   a_scores.append(9)
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

    # ── Bull / Bear flags ──────────────────────────────────────────────────────
    bull_flags: list[str] = []
    bear_flags: list[str] = []

    if upside is not None and target:
        if upside > 0.20:
            bull_flags.append(f"Analyst target ${target:.2f} — {upside*100:.0f}% upside to consensus")
        elif upside < -0.05:
            bear_flags.append(f"Trading {abs(upside)*100:.0f}% above analyst consensus target")
    if rg is not None:
        if rg > 0.20:
            bull_flags.append(f"Revenue growing {rg*100:.0f}% YoY — strong top-line momentum")
        elif rg < 0:
            bear_flags.append(f"Revenue declining {abs(rg)*100:.0f}% YoY — top-line under pressure")
    if gm is not None:
        if gm > 0.50:
            bull_flags.append(f"Gross margin {gm*100:.0f}% — strong pricing power")
        elif gm < 0.20:
            bear_flags.append(f"Gross margin thin at {gm*100:.0f}% — limited buffer")
    if rsi_val is not None:
        if rsi_val < 35:
            bull_flags.append(f"RSI {rsi_val:.0f} — oversold, potential high-conviction entry zone")
        elif rsi_val > 72:
            bear_flags.append(f"RSI {rsi_val:.0f} — overbought, elevated pullback risk near-term")
    vs50 = None
    if len(close) >= 50:
        ma50_ = float(close.rolling(50).mean().iloc[-1])
        last_ = float(close.iloc[-1])
        vs50  = (last_ - ma50_) / ma50_
        if vs50 > 0.08:
            bull_flags.append(f"Price {vs50*100:.0f}% above 50-day MA — uptrend confirmed")
        elif vs50 < -0.10:
            bear_flags.append(f"Price {abs(vs50)*100:.0f}% below 50-day MA — downtrend in force")
    vs200 = None
    if len(close) >= 200:
        ma200_ = float(close.rolling(200).mean().iloc[-1])
        last_  = float(close.iloc[-1])
        vs200  = (last_ - ma200_) / ma200_
        if vs200 < 0:
            bear_flags.append(f"Trading below 200-day MA — long-term trend broken")
        elif vs200 > 0.15:
            bull_flags.append(f"Price {vs200*100:.0f}% above 200-day MA — long-term uptrend intact")
    if fcf is not None:
        if fcf > 0 and rev and rev > 0:
            fcf_m = fcf / rev
            if fcf_m > 0.15:
                bull_flags.append(f"FCF margin {fcf_m*100:.0f}% — high-quality cash compounder")
        elif fcf < 0:
            bear_flags.append("Negative free cash flow — burning cash, check runway")

    return {
        "overall":    overall,
        "light":      light(overall),
        "factors":    factors,
        "price":      price,
        "upside":     upside,
        "rsi":        rsi_val,
        "vs50":       vs50,
        "vs200":      vs200,
        "rec":        rec,
        "pe":         pe,
        "peg":        peg,
        "gm":         gm,
        "om":         om,
        "rg":         rg,
        "eg":         eg,
        "fcf":        fcf,
        "rev":        rev,
        "bull_flags": bull_flags[:4],
        "bear_flags": bear_flags[:4],
    }


# ── Signal / situation / action helpers ───────────────────────────────────────

def _signal_info(overall: float) -> tuple[str, str]:
    """Returns (label, hex_color)."""
    if overall >= 8.5: return "STRONG BUY",   "#16a34a"
    if overall >= 7.5: return "BUY",           "#22c55e"
    if overall >= 6.5: return "WATCH / ADD",   "#22c55e"
    if overall >= 5.5: return "HOLD",          "#eab308"
    if overall >= 4.5: return "WATCH / TRIM",  "#f97316"
    if overall >= 3.5: return "REDUCE",        "#ef4444"
    return "SELL / EXIT",                       "#dc2626"


def _situation_text(overall: float, rsi, vs50) -> str:
    if overall >= 7.5:
        if rsi and rsi > 70:
            return "Strong conviction but technically overbought. Wait for pullback before adding."
        if rsi and rsi < 35:
            return "High-conviction setup at an oversold entry point. Strong risk/reward."
        return "Fundamentals and technicals aligned. High-conviction setup."
    if overall >= 6.5:
        if vs50 and vs50 > 0:
            return "Developing thesis with positive momentum. Small position or watchlist candidate."
        return "Technicals improving but thesis still developing. Small entry or keep on watchlist."
    if overall >= 5.5:
        return "Mixed signals. Hold existing position; avoid adding until clarity improves."
    if overall >= 4.5:
        return "Below conviction threshold. Review position size or await fundamental improvement."
    return "Weak fundamentals. Consider reducing exposure or exiting on bounce."


def _action_text(overall: float, upside, vs50) -> str:
    if overall >= 7.5:
        dip = f" Add on any dip toward 50-day MA." if (vs50 and vs50 > 0.05) else ""
        return f"Build or hold full position.{dip}"
    if overall >= 6.5:
        up_s = f" {upside*100:.0f}% analyst upside remaining." if upside and upside > 0.10 else ""
        return f"Open or add a modest position.{up_s} Set stop at recent swing low."
    if overall >= 5.5:
        return "Hold current allocation. No new buys until score improves to 7+."
    if overall >= 4.5:
        return "Below conviction threshold. Review position size or await fundamental improvement."
    return "Consider trimming on any bounce toward 50-day MA. Protect capital."


def _insight_text(overall: float, rsi, vs50, upside, signal: str) -> str:
    parts = []
    if rsi and rsi < 35:
        parts.append(f"RSI {rsi:.0f} — oversold entry zone")
    elif rsi and rsi > 70:
        parts.append(f"RSI {rsi:.0f} — overbought near-term")
    if vs50 is not None:
        if vs50 > 0.05:
            parts.append(f"price {vs50*100:.0f}% above 50MA")
        elif vs50 < -0.05:
            parts.append(f"price {abs(vs50)*100:.0f}% below 50MA — watch for support")
    if upside and upside > 0.15:
        parts.append(f"{upside*100:.0f}% analyst upside remaining")
    if parts:
        return f"Signal: {signal}. " + " · ".join(p.capitalize() for p in parts) + "."
    return f"Score {overall}/10. No strong action trigger — continue monitoring per plan."


def _pill(label: str, color: str, bg: str) -> str:
    return (f'<span style="background:{bg};color:{color};font-size:10px;'
            f'padding:2px 8px;border-radius:10px;font-weight:600;">{label}</span>')


def _rsi_pill(rsi) -> str:
    if rsi is None: return ""
    if rsi < 35:  return _pill("oversold", "#86efac", "#14532d")
    if rsi < 45:  return _pill("near-oversold", "#fde68a", "#78350f")
    if rsi < 65:  return _pill("neutral", "#93c5fd", "#1e3a5f")
    if rsi < 75:  return _pill("heated", "#fde68a", "#78350f")
    return _pill("overbought", "#fca5a5", "#450a0a")


def _margin_pill(val, low=0.20, high=0.45) -> str:
    if val is None: return ""
    if val > high:  return _pill("strong", "#86efac", "#14532d")
    if val > low:   return _pill("ok", "#fde68a", "#78350f")
    return _pill("thin", "#fca5a5", "#450a0a")


def _short_pill(val) -> str:
    if val is None: return ""
    pct = val * 100
    if pct > 20:   return _pill("squeeze", "#c4b5fd", "#2e1065")
    if pct > 10:   return _pill("watch", "#fde68a", "#78350f")
    if pct > 5:    return _pill("moderate", "#93c5fd", "#1e3a5f")
    return _pill("low", "#86efac", "#14532d")


def _inst_pill(val) -> str:
    if val is None: return ""
    pct = val * 100
    if pct > 80:   return _pill("crowded", "#fca5a5", "#450a0a")
    if pct > 50:   return _pill("well-held", "#86efac", "#14532d")
    if pct > 30:   return _pill("moderate", "#fde68a", "#78350f")
    return _pill("low", "#fca5a5", "#450a0a")


def _de_pill(val) -> str:
    if val is None: return ""
    if val < 50:   return _pill("lean", "#86efac", "#14532d")
    if val < 150:  return _pill("moderate", "#fde68a", "#78350f")
    return _pill("leveraged", "#fca5a5", "#450a0a")


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
    # History is stored as one CSV per ticker: data/history/{TICKER}.csv
    # Columns: date, score, signal, price, score_light, bull_count, bear_count
    history_dir = Path("data/history")
    if history_dir.exists():
        csv_files = list(history_dir.glob("*.csv"))
        if csv_files:
            st.markdown("---")
            st.markdown("#### 📈 Score Trend (last 8 runs)")
            ticker_options = [t["ticker"] for t in top_tickers]
            sel = st.multiselect(
                "Select tickers", ticker_options, default=ticker_options[:3],
                key="home_spark",
            )
            if sel:
                dfs = []
                for csv_f in csv_files:
                    tkr_name = csv_f.stem          # filename stem = ticker symbol
                    if tkr_name not in sel:
                        continue
                    try:
                        df_w = pd.read_csv(csv_f)
                        if "score" not in df_w.columns:
                            continue
                        df_w["ticker"] = tkr_name
                        # Keep last 8 data points (most recent runs)
                        dfs.append(df_w.tail(8))
                    except Exception:
                        pass
                if dfs:
                    df_hist = pd.concat(dfs, ignore_index=True)
                    # Use "date" column as x-axis
                    x_col = "date" if "date" in df_hist.columns else df_hist.columns[0]
                    df_hist = df_hist.sort_values([x_col])
                    fig_line = px.line(
                        df_hist, x=x_col, y="score", color="ticker",
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

    # ── Macro context banner ────────────────────────────────────────────────────
    macro = fetch_macro_context()
    if macro:
        parts = []
        if "fed_rate" in macro:
            parts.append(f"Fed Rate: <b>{macro['fed_rate']:.2f}%</b>")
        if "t10y" in macro:
            parts.append(f"10Y Yield: <b>{macro['t10y']:.2f}%</b>")
        if "cpi_yoy" in macro:
            cpi   = macro["cpi_yoy"]
            cpi_c = "#fca5a5" if cpi > 3.5 else ("#86efac" if cpi < 2.5 else "#fbbf24")
            parts.append(f'CPI: <b style="color:{cpi_c};">{cpi}% YoY</b>')
        if parts:
            st.markdown(
                f'<div style="background:#1e293b;border:1px solid #334155;border-radius:6px;'
                f'padding:8px 16px;font-size:12px;color:#94a3b8;margin-bottom:16px;">'
                f'<span style="color:#64748b;font-weight:600;text-transform:uppercase;'
                f'letter-spacing:.05em;font-size:10px;">MACRO CONTEXT</span>'
                f'&nbsp;&nbsp;' + '&nbsp; · &nbsp;'.join(parts) +
                f'</div>',
                unsafe_allow_html=True,
            )

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

        overall     = score["overall"]
        overall_color = GREEN if overall >= 7 else (YELLOW if overall >= 4.5 else RED)
        price       = score.get("price") or 0
        name        = info.get("shortName") or info.get("longName") or tkr
        sector      = info.get("sector") or "—"
        industry    = info.get("industry") or ""
        upside      = score.get("upside")
        rsi_val     = score.get("rsi")
        vs50        = score.get("vs50")
        factors     = score["factors"]
        sig_label, sig_color = _signal_info(overall)
        rec         = score.get("rec")
        rec_map     = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Sell", 5: "Strong Sell"}
        rec_label   = rec_map.get(int(round(rec)), "Hold") if rec else "Hold"
        target      = _sf(info.get("targetMeanPrice"))
        earn_date   = info.get("earningsDate") or info.get("earningTimestamp")
        earn_str    = ""
        if earn_date:
            try:
                if isinstance(earn_date, (int, float)):
                    earn_str = datetime.fromtimestamp(int(earn_date)).strftime("%Y-%m-%d")
                else:
                    earn_str = str(earn_date)[:10]
            except Exception:
                pass

        with st.expander("", expanded=True):

            # ══ HEADER CARD ════════════════════════════════════════════════════
            col_info, col_score, col_action = st.columns([2, 1.2, 1.2])

            with col_info:
                st.markdown(
                    f"<div style='margin-bottom:4px;'>"
                    f"<span style='font-size:24px;font-weight:800;color:#f1f5f9;'>{tkr}</span>"
                    f"&nbsp;&nbsp;<span style='font-size:15px;color:#94a3b8;'>{name}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                pills = " · ".join(p for p in [sector, industry] if p and p != "—")
                if pills:
                    st.markdown(
                        f'<span style="background:#1e293b;color:#94a3b8;font-size:12px;'
                        f'padding:3px 10px;border-radius:12px;">{pills}</span>',
                        unsafe_allow_html=True,
                    )
                st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
                sig_btn = (
                    f'<span style="background:{sig_color};color:#0f172a;font-size:12px;'
                    f'font-weight:700;padding:4px 12px;border-radius:6px;">{sig_label}</span>'
                )
                earn_badge = (
                    f'&nbsp; → &nbsp;<span style="color:#94a3b8;font-size:12px;">'
                    f'Earnings {earn_str}</span>' if earn_str else ""
                )
                st.markdown(sig_btn + earn_badge, unsafe_allow_html=True)

            with col_score:
                score_c = GREEN if overall >= 7 else (YELLOW if overall >= 4.5 else RED)
                st.markdown(
                    f"""<div style="background:#1e293b;border-left:4px solid {score_c};
                        border-radius:8px;padding:12px 16px;">
                      <div style="font-size:32px;font-weight:800;color:{score_c};">{overall}</div>
                      <div style="font-size:11px;color:#64748b;font-weight:600;margin:2px 0;">
                        {sig_label}
                      </div>
                      <div style="font-size:11px;color:#94a3b8;">
                        {_pct_fmt(upside)} analyst upside
                      </div>
                    </div>""",
                    unsafe_allow_html=True,
                )

            with col_action:
                st.markdown(
                    f"""<div style="background:#1e293b;border:1px solid #334155;
                        border-radius:8px;padding:12px 16px;">
                      <div style="font-size:14px;font-weight:600;color:#f1f5f9;">
                        {rec_label}
                      </div>
                      <div style="font-size:11px;color:#64748b;margin-top:4px;">
                        {_fmt_large(_sf(info.get('marketCap')))} mkt cap
                      </div>
                      <div style="font-size:11px;color:#64748b;margin-top:2px;">
                        {int(round(rec*10))/10 if rec else '—'} analyst consensus
                      </div>
                    </div>""",
                    unsafe_allow_html=True,
                )

            st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

            # ══ THESIS ════════════════════════════════════════════════════════
            thesis = generate_ai_thesis(tkr, score)
            if not thesis:
                # Rule-based fallback thesis
                gm_s  = f"gross margin {score['gm']*100:.0f}%" if score.get("gm") else ""
                rg_s  = f"revenue growing {score['rg']*100:.0f}% YoY" if score.get("rg") else ""
                thesis = (
                    f"Score {overall}/10. "
                    + " · ".join(x for x in [rg_s, gm_s] if x)
                    + f". Analyst rating: {rec_label}."
                )
            st.markdown(
                f"""<div style="border-left:3px solid {INDIGO};background:#1e293b;
                    border-radius:0 8px 8px 0;padding:12px 16px;margin:10px 0;">
                  <div style="font-size:10px;color:{INDIGO};font-weight:700;
                      letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px;">
                    THESIS
                  </div>
                  <div style="color:#cbd5e1;font-size:14px;line-height:1.6;">{thesis}</div>
                </div>""",
                unsafe_allow_html=True,
            )

            # ══ SITUATION | ACTION ════════════════════════════════════════════
            col_sit, col_act = st.columns(2)
            with col_sit:
                st.markdown(
                    f"""<div style="background:#1e293b;border:1px solid #334155;
                        border-radius:8px;padding:12px 14px;min-height:90px;">
                      <div style="font-size:10px;color:#64748b;font-weight:700;
                          letter-spacing:.1em;margin-bottom:6px;">SITUATION</div>
                      <div style="color:#cbd5e1;font-size:13px;line-height:1.5;">
                        {_situation_text(overall, rsi_val, vs50)}
                      </div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            with col_act:
                st.markdown(
                    f"""<div style="background:#1e293b;border:1px solid #334155;
                        border-radius:8px;padding:12px 14px;min-height:90px;">
                      <div style="font-size:10px;color:#64748b;font-weight:700;
                          letter-spacing:.1em;margin-bottom:6px;">ACTION</div>
                      <div style="color:#cbd5e1;font-size:13px;line-height:1.5;">
                        {_action_text(overall, upside, vs50)}
                      </div>
                    </div>""",
                    unsafe_allow_html=True,
                )

            # ══ INSIGHT BAR ═══════════════════════════════════════════════════
            insight = _insight_text(overall, rsi_val, vs50, upside, sig_label)
            st.markdown(
                f"""<div style="background:#1e293b;border:1px solid #334155;
                    padding:10px 14px;border-radius:6px;margin:10px 0;">
                  <span style="font-size:10px;color:#64748b;font-weight:700;
                      text-transform:uppercase;letter-spacing:.08em;">INSIGHT &nbsp;</span>
                  <span style="color:#94a3b8;font-size:13px;">{insight}</span>
                </div>""",
                unsafe_allow_html=True,
            )

            # ══ METRICS STRIP ═════════════════════════════════════════════════
            _gm      = score.get("gm")
            _om      = score.get("om")
            _de      = _sf(info.get("debtToEquity"))
            _si      = _sf(info.get("shortPercentOfFloat"))
            _io      = _sf(info.get("institutionPercentHeld")) or _sf(info.get("heldPercentInstitutions"))
            # regularMarketChangePercent = decimal (0.0125 = +1.25%)
            # regularMarketChange = dollar change — NOT a pct, never multiply by 100
            _chg_pct = _sf(info.get("regularMarketChangePercent"))
            if _chg_pct is None:
                # compute from price vs previous close
                _prev = _sf(info.get("previousClose")) or _sf(info.get("regularMarketPreviousClose"))
                if price and _prev and _prev > 0:
                    _chg_pct = (price - _prev) / _prev
            chg_s    = f'<span style="color:{"#22c55e" if (_chg_pct or 0)>=0 else "#ef4444"};">{_chg_pct*100:+.2f}%</span>' if _chg_pct is not None else ""
            price_s  = f'<b style="color:#f1f5f9;font-size:16px;">${price:.2f}</b> {chg_s}'
            rsi_s    = f'RSI <b>{rsi_val:.0f}</b> {_rsi_pill(rsi_val)}' if rsi_val else ""
            gm_s     = f'Gross Margin <b>{_gm*100:.1f}%</b> {_margin_pill(_gm)}' if _gm else ""
            om_s     = f'Op Margin <b>{_om*100:.1f}%</b> {_margin_pill(_om, 0.05, 0.20)}' if _om else ""
            si_s     = f'Short Float <b>{_si*100:.1f}%</b> {_short_pill(_si)}' if _si else ""
            io_s     = f'Inst Own <b>{_io*100:.0f}%</b> {_inst_pill(_io)}' if _io else ""
            de_s     = f'D/E <b>{_de:.0f}%</b> {_de_pill(_de)}' if _de is not None else ""
            tgt_s    = (f'Analyst Tgt <b>${target:.0f}</b> '
                        f'<b style="color:{"#22c55e" if (upside or 0)>=0 else "#ef4444"};">'
                        f'{upside*100:+.0f}%</b>') if target and upside is not None else ""
            earn_s   = f'Earnings <b>{earn_str}</b>' if earn_str else ""
            strip    = " &nbsp;|&nbsp; ".join(x for x in
                       [price_s, rsi_s, gm_s, om_s, si_s, io_s, de_s, tgt_s, earn_s] if x)
            st.markdown(
                f'<div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;'
                f'padding:10px 14px;font-size:12px;color:#94a3b8;margin:10px 0;'
                f'line-height:2.2;">{strip}</div>',
                unsafe_allow_html=True,
            )

            # ══ CATEGORY BREAKDOWN | KEY SIGNALS ═════════════════════════════
            col_cats, col_flags = st.columns([1, 1.4])

            with col_cats:
                st.markdown(
                    '<div style="font-size:10px;color:#64748b;font-weight:700;'
                    'letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px;">'
                    'FACTOR BREAKDOWN</div>',
                    unsafe_allow_html=True,
                )
                for fname, fscore in factors.items():
                    fc = GREEN if fscore >= 7 else (YELLOW if fscore >= 4.5 else RED)
                    bar_w = int(fscore * 10)
                    st.markdown(
                        f"""<div style="display:flex;align-items:center;
                            margin-bottom:6px;gap:10px;">
                          <span style="color:#94a3b8;font-size:12px;width:80px;">{fname}</span>
                          <div style="flex:1;background:#1e293b;border-radius:4px;height:6px;">
                            <div style="width:{bar_w}%;background:{fc};
                                border-radius:4px;height:6px;"></div>
                          </div>
                          <span style="color:{fc};font-size:13px;font-weight:700;
                              width:32px;text-align:right;">{fscore:.1f}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )

            with col_flags:
                st.markdown(
                    '<div style="font-size:10px;color:#64748b;font-weight:700;'
                    'letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px;">'
                    'KEY SIGNALS</div>',
                    unsafe_allow_html=True,
                )
                for flag in score.get("bull_flags", []):
                    st.markdown(
                        f'<div style="background:#1e293b;border-left:3px solid #22c55e;'
                        f'border-radius:0 6px 6px 0;padding:6px 10px;margin-bottom:5px;'
                        f'font-size:12px;color:#86efac;">▲ {flag}</div>',
                        unsafe_allow_html=True,
                    )
                for flag in score.get("bear_flags", []):
                    st.markdown(
                        f'<div style="background:#1e293b;border-left:3px solid #ef4444;'
                        f'border-radius:0 6px 6px 0;padding:6px 10px;margin-bottom:5px;'
                        f'font-size:12px;color:#fca5a5;">▼ {flag}</div>',
                        unsafe_allow_html=True,
                    )
                if not score.get("bull_flags") and not score.get("bear_flags"):
                    st.caption("No strong signals detected.")

            # ══ PIPELINE CATEGORY BREAKDOWN (when pipeline data is available) ═
            pipeline_ticker_json = load_ticker_json(tkr)
            if pipeline_ticker_json:
                # cat_scores = {cat_id: {"score": float, "light": str}, ...}
                cat_scores_raw = pipeline_ticker_json.get("cat_scores", {})
                cats = {k: v.get("score", 5.0) for k, v in cat_scores_raw.items() if isinstance(v, dict)}
                if cats:
                    st.markdown(
                        '<div style="font-size:10px;color:#64748b;font-weight:700;'
                        'letter-spacing:.1em;text-transform:uppercase;margin:14px 0 8px;">'
                        'PIPELINE CATEGORY DETAIL '
                        '<span style="font-weight:400;color:#475569;font-size:9px;text-transform:none;">'
                        '(from last pipeline run)</span></div>',
                        unsafe_allow_html=True,
                    )
                    cat_labels = {
                        "fundamentals":  "Fundamentals",
                        "valuation":     "Valuation",
                        "technical":     "Technical",
                        "sentiment":     "Sentiment",
                        "management":    "Management",
                        "moat":          "Moat",
                        "partnerships":  "Partnerships",
                        "macro":         "Macro",
                        "catalysts":     "Catalysts",
                        "risk":          "Risk",
                        "institutional": "Institutional",
                    }
                    cols_per_row = 2
                    cat_items = [(cat_labels.get(k, k), v) for k, v in cats.items() if isinstance(v, (int, float))]
                    rows = [cat_items[i:i+cols_per_row] for i in range(0, len(cat_items), cols_per_row)]
                    for row in rows:
                        rcols = st.columns(cols_per_row)
                        for j, (lbl, val) in enumerate(row):
                            with rcols[j]:
                                fc = GREEN if val >= 7 else (YELLOW if val >= 4.5 else RED)
                                bar_w = int(val * 10)
                                hint = "High" if val >= 7 else ("Moderate" if val >= 4.5 else "Low")
                                st.markdown(
                                    f"""<div style="margin-bottom:8px;">
                                      <div style="display:flex;justify-content:space-between;
                                          margin-bottom:4px;">
                                        <span style="color:#94a3b8;font-size:12px;">{lbl}</span>
                                        <span style="color:{fc};font-size:12px;font-weight:700;">
                                          {val:.1f}
                                          <span style="font-size:10px;font-weight:400;color:#64748b;">
                                            · {hint}
                                          </span>
                                        </span>
                                      </div>
                                      <div style="background:#1e293b;border-radius:4px;height:5px;">
                                        <div style="width:{bar_w}%;background:{fc};
                                            border-radius:4px;height:5px;"></div>
                                      </div>
                                    </div>""",
                                    unsafe_allow_html=True,
                                )

            # ══ FINNHUB MARKET INTELLIGENCE ══════════════════════════════════
            fh = fetch_finnhub(tkr)
            if fh:
                st.markdown(
                    '<div style="font-size:10px;color:#64748b;font-weight:700;'
                    'letter-spacing:.1em;text-transform:uppercase;margin:16px 0 10px;">'
                    'MARKET INTELLIGENCE</div>',
                    unsafe_allow_html=True,
                )
                col_rec, col_earn, col_sent = st.columns(3)

                with col_rec:
                    recs = fh.get("recommendations", [])
                    if recs:
                        latest = recs[0]
                        total  = sum(latest.get(k, 0) for k in
                                     ["strongBuy","buy","hold","sell","strongSell"])
                        buy_pct  = (latest.get("strongBuy",0) + latest.get("buy",0)) / max(total,1) * 100
                        hold_pct = latest.get("hold",0) / max(total,1) * 100
                        sell_pct = (latest.get("sell",0) + latest.get("strongSell",0)) / max(total,1) * 100
                        period   = latest.get("period","")
                        # mini horizontal bar
                        bar_html = (
                            f'<div style="background:#1e293b;border:1px solid #334155;'
                            f'border-radius:8px;padding:12px 14px;">'
                            f'<div style="font-size:10px;color:#64748b;font-weight:700;'
                            f'margin-bottom:10px;">ANALYST RECS ({total} analysts)</div>'
                            f'<div style="font-size:12px;color:#86efac;margin-bottom:4px;">'
                            f'▲ Buy &nbsp;<b>{buy_pct:.0f}%</b></div>'
                            f'<div style="height:5px;background:#334155;border-radius:3px;margin-bottom:8px;">'
                            f'<div style="width:{buy_pct:.0f}%;height:5px;background:#22c55e;border-radius:3px;"></div></div>'
                            f'<div style="font-size:12px;color:#94a3b8;margin-bottom:4px;">'
                            f'● Hold &nbsp;<b>{hold_pct:.0f}%</b></div>'
                            f'<div style="height:5px;background:#334155;border-radius:3px;margin-bottom:8px;">'
                            f'<div style="width:{hold_pct:.0f}%;height:5px;background:#64748b;border-radius:3px;"></div></div>'
                            f'<div style="font-size:12px;color:#fca5a5;margin-bottom:4px;">'
                            f'▼ Sell &nbsp;<b>{sell_pct:.0f}%</b></div>'
                            f'<div style="height:5px;background:#334155;border-radius:3px;margin-bottom:8px;">'
                            f'<div style="width:{sell_pct:.0f}%;height:5px;background:#ef4444;border-radius:3px;"></div></div>'
                            f'<div style="font-size:10px;color:#475569;margin-top:4px;">{period}</div>'
                            f'</div>'
                        )
                        st.markdown(bar_html, unsafe_allow_html=True)

                with col_earn:
                    earnings = fh.get("earnings", [])
                    if earnings:
                        earn_html = (
                            '<div style="background:#1e293b;border:1px solid #334155;'
                            'border-radius:8px;padding:12px 14px;">'
                            '<div style="font-size:10px;color:#64748b;font-weight:700;'
                            'margin-bottom:10px;">EARNINGS SURPRISE</div>'
                        )
                        for eq in earnings[:4]:
                            actual  = eq.get("actual")
                            est     = eq.get("estimate")
                            period  = eq.get("period","")
                            if actual is not None and est is not None and est != 0:
                                surprise = (actual - est) / abs(est) * 100
                                icon  = "▲" if surprise >= 0 else "▼"
                                color = "#86efac" if surprise >= 0 else "#fca5a5"
                                earn_html += (
                                    f'<div style="display:flex;justify-content:space-between;'
                                    f'font-size:12px;margin-bottom:6px;">'
                                    f'<span style="color:#64748b;">{period}</span>'
                                    f'<span style="color:{color};">{icon} {surprise:+.1f}%</span>'
                                    f'</div>'
                                )
                        earn_html += '</div>'
                        st.markdown(earn_html, unsafe_allow_html=True)

                with col_sent:
                    sentiment = fh.get("sentiment", {})
                    buzz      = fh.get("buzz", {})
                    if sentiment:
                        bull_pct  = sentiment.get("bullishPercent", 0) * 100
                        bear_pct  = sentiment.get("bearishPercent", 0) * 100
                        articles  = buzz.get("articlesInLastWeek", 0)
                        buzz_score= buzz.get("buzz", 0)
                        bull_c    = "#86efac" if bull_pct > 55 else ("#fca5a5" if bull_pct < 40 else "#94a3b8")
                        st.markdown(
                            f'<div style="background:#1e293b;border:1px solid #334155;'
                            f'border-radius:8px;padding:12px 14px;">'
                            f'<div style="font-size:10px;color:#64748b;font-weight:700;'
                            f'margin-bottom:10px;">NEWS SENTIMENT</div>'
                            f'<div style="font-size:24px;font-weight:700;color:{bull_c};">'
                            f'{bull_pct:.0f}%</div>'
                            f'<div style="font-size:12px;color:#64748b;margin-bottom:8px;">Bullish</div>'
                            f'<div style="font-size:12px;color:#94a3b8;">{bear_pct:.0f}% Bearish</div>'
                            f'<div style="font-size:11px;color:#475569;margin-top:8px;">'
                            f'{articles} articles · Buzz {buzz_score:.1f}x</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

            st.markdown("---")

            # ══ GAUGE + RADAR (kept — user likes them) ════════════════════════
            col_gauge, col_radar = st.columns(2)

            with col_gauge:
                st.caption("Score Gauge")
                fig_gauge = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=overall,
                    domain={"x": [0, 1], "y": [0, 1]},
                    title={"text": "Overall Score", "font": {"size": 13, "color": "#94a3b8"}},
                    number={"font": {"size": 40, "color": "#f1f5f9"}, "suffix": "/10"},
                    gauge={
                        "axis": {
                            "range": [0, 10],
                            "tickvals": [0, 2, 4.5, 7, 10],
                            "tickcolor": "#475569",
                            "tickfont": {"size": 9, "color": "#64748b"},
                        },
                        "bar": {"color": overall_color, "thickness": 0.25},
                        "bgcolor": "#0f172a",
                        "borderwidth": 1,
                        "bordercolor": "#334155",
                        "steps": [
                            {"range": [0, 4.5],  "color": "rgba(239,68,68,0.12)"},
                            {"range": [4.5, 7.0],"color": "rgba(234,179,8,0.12)"},
                            {"range": [7.0, 10], "color": "rgba(34,197,94,0.12)"},
                        ],
                        "threshold": {
                            "line": {"color": overall_color, "width": 4},
                            "thickness": 0.8,
                            "value": overall,
                        },
                    },
                ))
                fig_gauge.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    font_color="#94a3b8",
                    height=220,
                    margin=dict(l=15, r=15, t=40, b=10),
                )
                st.plotly_chart(fig_gauge, use_container_width=True)

            with col_radar:
                st.caption("Factor Radar")
                _radar_fills = {
                    GREEN:  "rgba(34,197,94,0.2)",
                    YELLOW: "rgba(234,179,8,0.2)",
                    RED:    "rgba(239,68,68,0.2)",
                }
                fig_radar = go.Figure(go.Scatterpolar(
                    r=list(factors.values()),
                    theta=list(factors.keys()),
                    fill="toself",
                    line_color=overall_color,
                    fillcolor=_radar_fills.get(overall_color, "rgba(99,102,241,0.2)"),
                ))
                fig_radar.update_layout(
                    polar=dict(
                        radialaxis=dict(range=[0, 10], showticklabels=True, tickfont_size=9),
                        bgcolor="rgba(0,0,0,0)",
                    ),
                    paper_bgcolor="rgba(0,0,0,0)",
                    font_color="#94a3b8",
                    margin=dict(l=20, r=20, t=30, b=20),
                    height=220,
                    showlegend=False,
                )
                st.plotly_chart(fig_radar, use_container_width=True)

            # ══ PRICE + MA CHART ══════════════════════════════════════════════
            if not hist.empty:
                st.caption("Price History")
                fig_price = go.Figure()
                fig_price.add_trace(go.Scatter(
                    x=hist.index, y=hist["Close"],
                    mode="lines", name="Price",
                    line=dict(color=overall_color, width=2),
                ))
                if len(hist) >= 50:
                    fig_price.add_trace(go.Scatter(
                        x=hist.index, y=hist["Close"].rolling(50).mean(),
                        mode="lines", name="50d MA",
                        line=dict(color="#3b82f6", width=1, dash="dash"),
                    ))
                if len(hist) >= 200:
                    fig_price.add_trace(go.Scatter(
                        x=hist.index, y=hist["Close"].rolling(200).mean(),
                        mode="lines", name="200d MA",
                        line=dict(color="#f59e0b", width=1, dash="dot"),
                    ))
                fig_price.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#94a3b8",
                    xaxis=dict(showgrid=False),
                    yaxis=dict(gridcolor="#334155"),
                    legend=dict(orientation="h", y=1.08),
                    margin=dict(l=0, r=0, t=30, b=0),
                    height=260,
                )
                st.plotly_chart(fig_price, use_container_width=True)

            # ── Volume line ────────────────────────────────────────────────────
            if not hist.empty and "Volume" in hist.columns:
                avg_vol  = hist["Volume"].mean()
                last_vol = hist["Volume"].iloc[-1]
                vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1
                vol_color = GREEN if vol_ratio > 1.2 else (RED if vol_ratio < 0.6 else YELLOW)
                try:
                    st.markdown(
                        f"**Volume:** {_fmt_large(int(last_vol))} "
                        f'<span style="color:{vol_color};">({vol_ratio:.1f}x avg)</span>',
                        unsafe_allow_html=True,
                    )
                except Exception:
                    pass

            # ── Analyst coverage ───────────────────────────────────────────────
            n_analysts = _sf(info.get("numberOfAnalystOpinions"))
            if n_analysts:
                st.caption(f"Based on {int(n_analysts)} analyst opinions.")

            # ── News headlines ─────────────────────────────────────────────────
            st.markdown("---")
            st.markdown("#### 📰 Recent News")
            st.caption("Source: Yahoo Finance (via yfinance, no API key needed)")
            news_items = fetch_news(tkr)
            shown = 0
            if news_items:
                for item in news_items[:10]:
                    # yfinance news dict fields (handle both old and new format)
                    title     = (item.get("title") or item.get("headline") or "").strip()
                    link      = (item.get("link")  or item.get("url")      or "").strip()
                    publisher = (item.get("publisher") or item.get("source") or "").strip()
                    pub_ts    = item.get("providerPublishTime") or item.get("datetime") or 0
                    if not title:
                        continue
                    try:
                        pub_dt = datetime.fromtimestamp(int(pub_ts)).strftime("%b %d") if pub_ts else ""
                    except Exception:
                        pub_dt = ""

                    # Keyword sentiment
                    t_low    = title.lower()
                    pos_hits = sum(1 for w in {"beat","surge","rally","growth","record","buy",
                                               "upgrade","strong","profit","gain","rose","raised",
                                               "bullish","jumps","soars"} if w in t_low)
                    neg_hits = sum(1 for w in {"miss","fall","drop","decline","loss","cut",
                                               "downgrade","weak","concern","risk","fell","lowered",
                                               "bearish","slumps","tumbles"} if w in t_low)
                    if pos_hits > neg_hits:
                        badge_bg, badge_fg, badge_lbl = "#14532d", "#86efac", "▲ Positive"
                    elif neg_hits > pos_hits:
                        badge_bg, badge_fg, badge_lbl = "#450a0a", "#fca5a5", "▼ Negative"
                    else:
                        badge_bg, badge_fg, badge_lbl = "#1e293b", "#94a3b8", "● Neutral"

                    # Use columns to avoid HTML-in-markdown parsing issues
                    col_badge, col_body = st.columns([1, 7])
                    with col_badge:
                        st.markdown(
                            f'<div style="background:{badge_bg};color:{badge_fg};'
                            f'font-size:10px;padding:4px 8px;border-radius:8px;'
                            f'text-align:center;margin-top:4px;">{badge_lbl}</div>',
                            unsafe_allow_html=True,
                        )
                    with col_body:
                        if link:
                            st.markdown(f"[{title}]({link})")
                        else:
                            st.markdown(title)
                        meta = " · ".join(x for x in [publisher, pub_dt] if x)
                        if meta:
                            st.caption(meta)
                    shown += 1
                    if shown >= 6:
                        break
            if shown == 0:
                st.caption("No recent news found for this ticker.")

            # ── Remove button ──────────────────────────────────────────────────
            st.markdown("")
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
        ("Rev Growth",     lambda sc, inf: _pct_fmt(_sf(inf.get("revenueGrowth")))),
        ("Gross Margin",   lambda sc, inf: _pct_fmt(_sf(inf.get("grossMargins")))),
        ("Op Margin",      lambda sc, inf: _pct_fmt(_sf(inf.get("operatingMargins")))),
        ("RSI",            lambda sc, inf: f"{sc.get('rsi'):.0f}" if sc.get("rsi") else "—"),
        ("Market Cap",     lambda sc, inf: _fmt_large(_sf(inf.get("marketCap")))),
        ("Beta",           lambda sc, inf: f"{_sf(inf.get('beta')):.2f}" if _sf(inf.get("beta")) is not None else "—"),
        ("Short Float",    lambda sc, inf: _pct_fmt(_sf(inf.get("shortPercentOfFloat")))),
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
    v = _sf(val)
    if v is None:
        return "—"
    if abs(v) >= 1e12:  return f"${v/1e12:.2f}T"
    if abs(v) >= 1e9:   return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:   return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"

def _pct_fmt(val) -> str:
    v = _sf(val)
    if v is None:
        return "—"
    return f"{v*100:.1f}%"


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
