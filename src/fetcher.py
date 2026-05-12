"""
fetcher.py — Data acquisition layer.

Pulls from: yfinance (primary), NewsAPI, Alpha Vantage, FRED, SEC EDGAR, Finnhub.
All sources are free-tier. Missing API keys degrade gracefully.
Returns a unified dict per ticker consumed by scorer.py and signals.py.
"""

import os
import time
import logging
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from edgar          import fetch_edgar_fundamentals
from finnhub_client import fetch_finnhub_signals

log = logging.getLogger(__name__)

NEWS_API_KEY       = os.getenv("NEWS_API_KEY", "")
ALPHA_VANTAGE_KEY  = os.getenv("ALPHA_VANTAGE_KEY", "")
FRED_API_KEY       = os.getenv("FRED_API_KEY", "")   # always free at fred.stlouisfed.org


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_ticker(ticker: str, archetype: str) -> dict:
    """
    Fetch all available data for a ticker.
    Returns a unified dict; missing fields are None (scorer handles gracefully).
    """
    log.info(f"Fetching {ticker} ({archetype})...")
    t = yf.Ticker(ticker)
    info = _safe_info(t)
    hist = _safe_history(t)

    data = {
        "ticker":    ticker,
        "archetype": archetype,
        "name":      info.get("longName") or info.get("shortName", ticker),
        "sector":    info.get("sector", "Unknown"),
        "timestamp": datetime.utcnow().isoformat(),

        # ── Price & market data ───────────────────────────────────────────
        "price":              info.get("currentPrice") or info.get("regularMarketPrice"),
        "prev_close":         info.get("previousClose"),
        "market_cap":         info.get("marketCap"),
        "52w_high":           info.get("fiftyTwoWeekHigh"),
        "52w_low":            info.get("fiftyTwoWeekLow"),
        "avg_volume_10d":     info.get("averageVolume10days"),
        "volume_today":       info.get("volume"),
        "beta":               info.get("beta"),

        # ── Fundamentals ──────────────────────────────────────────────────
        "revenue_growth_yoy": info.get("revenueGrowth"),          # e.g. 0.18 = 18%
        "earnings_growth_yoy":info.get("earningsGrowth"),
        "gross_margin":       info.get("grossMargins"),
        "operating_margin":   info.get("operatingMargins"),
        "net_margin":         info.get("profitMargins"),
        "free_cashflow":      info.get("freeCashflow"),
        "total_cash":         info.get("totalCash"),
        "total_debt":         info.get("totalDebt"),
        "debt_to_equity":     info.get("debtToEquity"),           # already ratio
        "current_ratio":      info.get("currentRatio"),
        "roe":                info.get("returnOnEquity"),
        "roa":                info.get("returnOnAssets"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "shares_float":       info.get("floatShares"),

        # ── Valuation ─────────────────────────────────────────────────────
        "pe_trailing":        info.get("trailingPE"),
        "pe_forward":         info.get("forwardPE"),
        "peg_ratio":          info.get("pegRatio"),
        "price_to_sales":     info.get("priceToSalesTrailingTwelveMonths"),
        "price_to_book":      info.get("priceToBook"),
        "ev_to_ebitda":       info.get("enterpriseToEbitda"),
        "ev_to_revenue":      info.get("enterpriseToRevenue"),
        "analyst_target":     info.get("targetMeanPrice"),
        "analyst_low":        info.get("targetLowPrice"),
        "analyst_high":       info.get("targetHighPrice"),
        "analyst_count":      info.get("numberOfAnalystOpinions"),
        "recommendation":     info.get("recommendationMean"),     # 1=Strong Buy, 5=Strong Sell

        # ── Sentiment / short interest ────────────────────────────────────
        "short_float_pct":    info.get("shortPercentOfFloat"),    # e.g. 0.18 = 18%
        "short_ratio":        info.get("shortRatio"),
        "shares_short":       info.get("sharesShort"),
        "shares_short_prior": info.get("sharesShortPriorMonth"),

        # ── Institutional / 13-F ──────────────────────────────────────────
        "inst_ownership_pct": info.get("heldPercentInstitutions"),
        "insider_pct":        info.get("heldPercentInsiders"),
        "inst_holders":       _safe_inst_holders(t),
        "insider_txns":       _safe_insider_txns(t),

        # ── Earnings ──────────────────────────────────────────────────────
        "earnings_date":      _safe_earnings_date(t, info),
        "earnings_history":   _safe_earnings_history(t),
        "fwd_eps":            info.get("forwardEps"),
        "trailing_eps":       info.get("trailingEps"),

        # ── Price history (90 days) for technical calculations ─────────────
        "hist":               hist,

        # ── Technical indicators (computed from hist) ─────────────────────
        **_compute_technicals(hist),

        # ── News sentiment (NewsAPI) ───────────────────────────────────────
        "news_sentiment":     _fetch_news_sentiment(ticker, info.get("longName", ticker)),

        # ── Macro (FRED — rate environment) ──────────────────────────────
        "macro":              _fetch_macro(),
    }

    # ── SEC EDGAR fundamentals (free, no key — fills yfinance gaps) ───────────
    edgar = fetch_edgar_fundamentals(ticker)
    if edgar:
        # Fill None gaps: EDGAR supplements, does not override good yfinance data
        if data.get("gross_margin") is None and edgar.get("edgar_gross_margin") is not None:
            data["gross_margin"] = edgar["edgar_gross_margin"]
        if data.get("free_cashflow") is None and edgar.get("edgar_fcf") is not None:
            data["free_cashflow"] = edgar["edgar_fcf"]
        if data.get("total_debt") is None and edgar.get("edgar_total_debt") is not None:
            data["total_debt"] = edgar["edgar_total_debt"]
        # Store all raw EDGAR fields for report display + scoring context
        data.update(edgar)

    # ── Finnhub signals (free tier, optional — activate via FINNHUB_API_KEY) ──
    finnhub = fetch_finnhub_signals(ticker)
    if finnhub:
        if finnhub.get("fh_insider_bullish") and not data.get("insider_txns"):
            data["insider_txns"] = [{
                "source": "finnhub",
                "net_shares_90d": finnhub.get("fh_insider_net_shares_90d"),
            }]
        data.update(finnhub)

    # Derived: price change 1D
    if data["price"] and data["prev_close"]:
        data["price_change_1d_pct"] = (data["price"] - data["prev_close"]) / data["prev_close"]
    else:
        data["price_change_1d_pct"] = None

    # Derived: upside to analyst target
    if data["price"] and data["analyst_target"]:
        data["analyst_upside"] = (data["analyst_target"] - data["price"]) / data["price"]
    else:
        data["analyst_upside"] = None

    # Derived: price vs 52w range (0=at low, 1=at high)
    if data["52w_high"] and data["52w_low"] and data["price"]:
        rng = data["52w_high"] - data["52w_low"]
        data["pct_of_52w_range"] = (data["price"] - data["52w_low"]) / rng if rng > 0 else 0.5
    else:
        data["pct_of_52w_range"] = None

    time.sleep(0.5)   # polite delay between tickers
    return data


# ── yfinance helpers ──────────────────────────────────────────────────────────

def _safe_info(t) -> dict:
    try:
        return t.info or {}
    except Exception as e:
        log.warning(f"info() failed: {e}")
        return {}


def _safe_history(t) -> pd.DataFrame:
    try:
        hist = t.history(period="6mo", interval="1d", auto_adjust=True)
        return hist if not hist.empty else pd.DataFrame()
    except Exception as e:
        log.warning(f"history() failed: {e}")
        return pd.DataFrame()


def _safe_inst_holders(t) -> list:
    try:
        df = t.institutional_holders
        if df is None or df.empty:
            return []
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        return df.head(10).to_dict("records")
    except Exception:
        return []


def _safe_insider_txns(t) -> list:
    try:
        df = t.insider_transactions
        if df is None or df.empty:
            return []
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        recent = df[df.index >= (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")]
        return recent.head(20).to_dict("records")
    except Exception:
        return []


def _safe_earnings_date(t, info: dict):
    try:
        cal = t.calendar
        if cal is not None and not cal.empty:
            dates = cal.get("Earnings Date", [])
            if hasattr(dates, '__iter__') and len(dates) > 0:
                return str(dates[0])[:10]
    except Exception:
        pass
    try:
        ed = info.get("earningsTimestamp")
        if ed:
            return datetime.fromtimestamp(ed).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def _safe_earnings_history(t) -> list:
    """Return last 8 quarters of earnings surprise data."""
    try:
        df = t.earnings_history
        if df is None or df.empty:
            return []
        df = df.dropna(subset=["epsActual", "epsEstimate"]).tail(8)
        df["surprise_pct"] = (df["epsActual"] - df["epsEstimate"]) / df["epsEstimate"].abs()
        return df[["epsActual", "epsEstimate", "surprise_pct"]].to_dict("records")
    except Exception:
        return []


# ── Technical indicator computation ──────────────────────────────────────────

def _compute_technicals(hist: pd.DataFrame) -> dict:
    """Compute RSI, moving averages, OBV trend, volume momentum from price history."""
    result = {
        "rsi_14":          None,
        "ma_50":           None,
        "ma_200":          None,
        "price_vs_ma50":   None,
        "price_vs_ma200":  None,
        "vol_ratio":       None,   # today's vol / 20d avg
        "vol_momentum_5d": None,   # 5d avg vol / 20d avg vol (>1.2 = institutional spike)
        "momentum_20d":    None,   # price return over last 20 days
        "price_4w_return": None,   # price return over last 4 weeks (~20 trading days)
        "obv_trend":       "flat", # "up" | "down" | "flat" — OBV 10d vs 20d EMA direction
        "atr_14":          None,   # average true range (volatility proxy)
    }
    if hist.empty or len(hist) < 20:
        return result

    close = hist["Close"]
    volume = hist["Volume"]

    # Moving averages
    if len(close) >= 50:
        result["ma_50"] = close.rolling(50).mean().iloc[-1]
    if len(close) >= 200:
        result["ma_200"] = close.rolling(200).mean().iloc[-1]

    last = close.iloc[-1]
    if result["ma_50"]:
        result["price_vs_ma50"] = (last - result["ma_50"]) / result["ma_50"]
    if result["ma_200"]:
        result["price_vs_ma200"] = (last - result["ma_200"]) / result["ma_200"]

    # RSI-14
    result["rsi_14"] = _rsi(close, 14)

    # Volume ratio (today vs 20d avg)
    if len(volume) >= 21:
        avg_vol = volume.iloc[-21:-1].mean()
        if avg_vol > 0:
            result["vol_ratio"] = volume.iloc[-1] / avg_vol

    # Volume momentum: 5d avg vs 20d avg — institutional activity fingerprint
    # >1.2 = unusual volume, possibly institutional; used by darkpool.py
    if len(volume) >= 20:
        avg_20d = volume.iloc[-20:].mean()
        avg_5d  = volume.iloc[-5:].mean()
        if avg_20d > 0:
            result["vol_momentum_5d"] = round(float(avg_5d / avg_20d), 2)

    # 20-day momentum
    if len(close) >= 21:
        result["momentum_20d"] = (close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]

    # 4-week return (~20 trading days) — used for entry/exit signal logic
    if len(close) >= 20:
        result["price_4w_return"] = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20]

    # OBV trend — On-Balance Volume: rising OBV with flat/up price = institutional accumulation
    # We compare the 5-period EMA of OBV vs the 20-period EMA.
    if len(close) >= 21 and len(volume) >= 21:
        direction = (close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0)))
        obv = (direction * volume).cumsum()
        if len(obv) >= 20:
            obv_ema5  = float(obv.ewm(span=5, adjust=False).mean().iloc[-1])
            obv_ema20 = float(obv.ewm(span=20, adjust=False).mean().iloc[-1])
            if obv_ema5 > obv_ema20 * 1.005:
                result["obv_trend"] = "up"
            elif obv_ema5 < obv_ema20 * 0.995:
                result["obv_trend"] = "down"
            else:
                result["obv_trend"] = "flat"

    # ATR-14
    if len(hist) >= 15 and "High" in hist.columns and "Low" in hist.columns:
        result["atr_14"] = _atr(hist, 14)

    return result


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 1) if not np.isnan(val) else None


def _atr(hist: pd.DataFrame, period: int = 14) -> float | None:
    try:
        high = hist["High"]
        low  = hist["Low"]
        close = hist["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        return round(float(tr.rolling(period).mean().iloc[-1]), 4)
    except Exception:
        return None


# ── NewsAPI sentiment ─────────────────────────────────────────────────────────

def _fetch_news_sentiment(ticker: str, company_name: str) -> dict:
    """
    Fetch last 7 days of news headlines. Count positive / negative / neutral.
    Returns {"score": float -1..1, "positive": int, "negative": int, "total": int}
    Score is a rough heuristic — not trained sentiment model.
    """
    default = {"score": None, "positive": 0, "negative": 0, "total": 0, "headlines": []}
    if not NEWS_API_KEY:
        return default
    try:
        query = f"{ticker} OR \"{company_name}\""
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "from": from_date,
                "language": "en",
                "sortBy": "relevancy",
                "pageSize": 20,
                "apiKey": NEWS_API_KEY,
            },
            timeout=10,
        )
        articles = resp.json().get("articles", [])
        pos_words = {"surge", "soar", "beat", "record", "rally", "breakthrough", "upgrade",
                     "profit", "growth", "strong", "bullish", "wins", "award", "partnership"}
        neg_words = {"drop", "fall", "miss", "loss", "decline", "cut", "downgrade", "risk",
                     "concern", "warn", "layoff", "investigation", "lawsuit", "bearish"}
        positive = negative = 0
        headlines = []
        for a in articles[:10]:
            title = (a.get("title") or "").lower()
            headlines.append(a.get("title", ""))
            words = set(title.split())
            if words & pos_words:
                positive += 1
            elif words & neg_words:
                negative += 1
        total = len(articles)
        score = (positive - negative) / max(total, 1)
        return {"score": round(score, 2), "positive": positive, "negative": negative,
                "total": total, "headlines": headlines[:5]}
    except Exception as e:
        log.warning(f"NewsAPI failed for {ticker}: {e}")
        return default


# ── FRED macro data ───────────────────────────────────────────────────────────

_macro_cache: dict = {}   # module-level cache — FRED doesn't change intraday

def _fetch_macro() -> dict:
    """
    Fetch macro data with a three-tier fallback chain:
      1. FRED API (requires free key at fred.stlouisfed.org → add FRED_API_KEY secret)
      2. Alpha Vantage economic indicators (free tier, uses ALPHA_VANTAGE_KEY)
      3. yfinance treasury ETF prices (^TNX 10yr, always works, no key needed)
    """
    global _macro_cache
    if _macro_cache:
        return _macro_cache

    result: dict = {}

    # ── Tier 1: FRED (most accurate, monthly/daily series) ────────────────────
    if FRED_API_KEY:
        fred_series = {
            "fed_funds_rate": "FEDFUNDS",
            "cpi_yoy":        "CPIAUCSL",
            "ten_yr_yield":   "DGS10",
            "two_yr_yield":   "DGS2",
            "unemployment":   "UNRATE",
        }
        base = "https://api.stlouisfed.org/fred/series/observations"
        for name, series_id in fred_series.items():
            try:
                r = requests.get(base, params={
                    "series_id":  series_id,
                    "api_key":    FRED_API_KEY,
                    "sort_order": "desc",
                    "limit":      2,
                    "file_type":  "json",
                }, timeout=8)
                obs = r.json().get("observations", [])
                if obs:
                    val = obs[0]["value"]
                    result[name] = float(val) if val != "." else None
            except Exception:
                result[name] = None
        result["_source"] = "FRED"
        log.info("Macro: loaded from FRED")

    # ── Tier 2: Alpha Vantage economic indicators (free tier) ─────────────────
    if ALPHA_VANTAGE_KEY and not result.get("fed_funds_rate"):
        av_functions = {
            "fed_funds_rate": "FEDERAL_FUNDS_RATE",
            "cpi_yoy":        "INFLATION",
            "unemployment":   "UNEMPLOYMENT",
        }
        av_base = "https://www.alphavantage.co/query"
        for name, func in av_functions.items():
            if result.get(name):
                continue
            try:
                r = requests.get(av_base, params={
                    "function": func,
                    "interval": "monthly",
                    "apikey":   ALPHA_VANTAGE_KEY,
                }, timeout=8)
                data_list = r.json().get("data", [])
                if data_list:
                    result[name] = float(data_list[0]["value"])
            except Exception:
                pass
        if not result.get("_source"):
            result["_source"] = "Alpha Vantage"
            log.info("Macro: loaded from Alpha Vantage")

    # ── Tier 3: yfinance treasury tickers (always free, no key) ───────────────
    # ^TNX = CBOE 10-Year Treasury Note Yield Index (actual rate in %)
    # ^FVX = CBOE 5-Year Treasury Note Yield Index (proxy for short end)
    yf_map = {
        "ten_yr_yield": "^TNX",
        "five_yr_yield": "^FVX",   # internal — used for spread if 2yr unavailable
    }
    for field_name, sym in yf_map.items():
        if result.get(field_name):
            continue
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if not hist.empty:
                result[field_name] = round(float(hist["Close"].dropna().iloc[-1]), 2)
        except Exception:
            pass

    if not result.get("_source") and result.get("ten_yr_yield"):
        result["_source"] = "yfinance"
        log.info("Macro: loaded from yfinance fallback (yields only)")

    # ── Yield curve spread ────────────────────────────────────────────────────
    # Prefer 10y–2y; fall back to 10y–5y if 2yr not available
    if result.get("ten_yr_yield") and result.get("two_yr_yield"):
        result["yield_spread_10_2"] = round(result["ten_yr_yield"] - result["two_yr_yield"], 2)
    elif result.get("ten_yr_yield") and result.get("five_yr_yield"):
        result["yield_spread_10_2"] = round(result["ten_yr_yield"] - result["five_yr_yield"], 2)
        result["_spread_proxy"] = True   # flag: spread uses 10y–5y, not 10y–2y
    else:
        result["yield_spread_10_2"] = None

    _macro_cache = result
    return result
