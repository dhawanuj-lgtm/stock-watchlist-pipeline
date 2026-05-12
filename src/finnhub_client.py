"""
finnhub_client.py — Finnhub API client (free tier, 60 calls/min).

Requires FINNHUB_API_KEY environment variable (free at finnhub.io).
Falls back gracefully — returns empty dict if key not set.

Supplements yfinance with:
  • Earnings surprise history (beat/miss/meet, last 4 quarters)
  • Net insider trading sentiment (90-day buy vs sell share count)

To activate: add FINNHUB_API_KEY as a GitHub Actions secret.
"""

import logging
import os
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

_KEY  = os.environ.get("FINNHUB_API_KEY", "")
_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 8


def _get(endpoint: str, params: dict) -> dict | list | None:
    if not _KEY:
        return None
    try:
        r = requests.get(
            f"{_BASE}/{endpoint}",
            params={"token": _KEY, **params},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"Finnhub {endpoint}: {e}")
        return None


# ── Earnings surprise ─────────────────────────────────────────────────────────

def _fetch_earnings_surprise(ticker: str) -> dict:
    data = _get("stock/earnings", {"symbol": ticker, "limit": 4})
    if not isinstance(data, list) or not data:
        return {}
    try:
        latest = data[0]
        pct = latest.get("surprisePercent", 0) or 0
        return {
            "fh_eps_actual":       latest.get("actual"),
            "fh_eps_estimate":     latest.get("estimate"),
            "fh_eps_surprise_pct": round(float(pct), 2),
            "fh_beat":             float(pct) > 3.0,
            "fh_miss":             float(pct) < -3.0,
            # Consecutive beats — positive signal for scorer
            "fh_consecutive_beats": sum(
                1 for q in data
                if (q.get("surprisePercent") or 0) > 2
            ),
        }
    except (IndexError, KeyError, TypeError):
        return {}


# ── Insider trading sentiment ─────────────────────────────────────────────────

def _fetch_insider_sentiment(ticker: str) -> dict:
    end   = date.today().isoformat()
    start = (date.today() - timedelta(days=90)).isoformat()
    data  = _get("stock/insider-transactions", {"symbol": ticker, "from": start, "to": end})
    if not isinstance(data, dict) or not data.get("data"):
        return {}
    try:
        txns = data["data"]
        buy_codes  = {"P", "A"}   # open market purchase, award
        sell_codes = {"S", "D"}   # open market sale, disposition

        net_shares = 0
        for t in txns:
            shares = t.get("share", 0) or 0
            code   = t.get("transactionCode", "")
            if code in buy_codes:
                net_shares += shares
            elif code in sell_codes:
                net_shares -= shares

        return {
            "fh_insider_net_shares_90d": int(net_shares),
            "fh_insider_bullish":        net_shares > 50_000,
            "fh_insider_bearish":        net_shares < -100_000,
        }
    except Exception:
        return {}


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_finnhub_signals(ticker: str) -> dict:
    """
    Aggregate all Finnhub signals for a ticker.
    Returns empty dict if FINNHUB_API_KEY not set (non-fatal).
    """
    if not _KEY:
        log.debug(f"Finnhub: no API key — skipping {ticker} (set FINNHUB_API_KEY secret to activate)")
        return {}

    result = {}
    result.update(_fetch_earnings_surprise(ticker))
    result.update(_fetch_insider_sentiment(ticker))

    if result:
        beat = result.get("fh_beat")
        insider = result.get("fh_insider_net_shares_90d", 0)
        log.info(
            f"Finnhub {ticker}: "
            f"EPS {'beat' if beat else 'miss/meet'} "
            f"| insider net={insider:+,} shares"
        )
    return result
