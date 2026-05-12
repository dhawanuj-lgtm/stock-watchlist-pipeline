"""
darkpool.py — Institutional accumulation / distribution signals (daily timeframe).

────────────────────────────────────────────────────────────────────────────────
ABOUT DARK POOL PRICES (like you see in retail Telegram channels):
  The "$137.70 DP price vs $134.78 close" number comes from ATS (Alternative
  Trading System) transaction data. Accessing it requires a paid service like
  Unusual Whales ($60/mo), Ortex, or similar.

  We approximate the same signal through three free proxies:

  (a) OBV Trend (On-Balance Volume) — derived from your existing yfinance history.
      Rising OBV while price is flat/up = institutions accumulating quietly.
      Falling OBV while price holds = distribution under the surface.

  (b) Volume momentum (5d avg / 20d avg) — institutional activity leaves
      volume footprints. A 1.3x+ spike with price up = buying pressure.

  (c) Quiver Quant FINRA short-sale volume (PAID, ~$30/month Hobbyist plan).
      Low short-sale ratio (<40%) + price rising = clean institutional buy.
      High short-sale ratio (>60%) + price rising = potential short squeeze.
      Only worth it if you're already a subscriber. Add QUIVER_QUANT_KEY secret.
      The system works fully without this — (a) and (b) above are solid free proxies.

SIGNAL INTERPRETATION:
  sentiment = "bullish"  → institutional footprint consistent with accumulation
  sentiment = "bearish"  → footprint consistent with distribution
  sentiment = "neutral"  → no strong read either way

These are CONFIRMING signals only — they never override the thesis/technical score.
Best used as: "Thesis says 7.8/10 — confirming accumulation? → Entry signal."

TIMEFRAME NOTE:
  We work with daily/weekly data only.
  30-minute dark pool intervals (as seen in some Telegram alerts) are intraday
  noise for a long-term investor. Use 1d intervals for our purposes.
────────────────────────────────────────────────────────────────────────────────
"""

import os
import logging
from dataclasses import dataclass

import pandas as pd
import requests

log = logging.getLogger(__name__)

_QQ_KEY  = os.environ.get("QUIVER_QUANT_KEY", "")
_QQ_BASE = "https://api.quiverquant.com/beta/historical/darkpool/{ticker}"


# ── Data structure ────────────────────────────────────────────────────────────

@dataclass
class DarkPoolSignal:
    ticker:           str
    sentiment:        str          # "bullish" | "bearish" | "neutral"
    volume_signal:    str          # "accumulation" | "distribution" | "neutral"
    obv_trend:        str          # "up" | "down" | "flat"
    vol_momentum_5d:  float | None # 5d avg vol / 20d avg vol (>1.2 = spike)
    short_vol_ratio:  float | None # from Quiver Quant FINRA (0–1); None if no key
    pct_of_52w_range: float | None # 0=at 52w low, 1=at 52w high
    confidence:       float        # 0–1
    note:             str          # human-readable summary for Telegram


# ── Public entry point ────────────────────────────────────────────────────────

def compute_darkpool_signal(ticker: str, data: dict) -> DarkPoolSignal:
    """
    Derive institutional accumulation signal from free data already in `data`.
    Optionally enriches with Quiver Quant FINRA short volume data.
    Always returns a DarkPoolSignal; never raises.
    """
    try:
        return _compute(ticker, data)
    except Exception as e:
        log.warning(f"darkpool signal failed for {ticker} (non-fatal): {e}")
        return DarkPoolSignal(
            ticker=ticker, sentiment="neutral", volume_signal="neutral",
            obv_trend="flat", vol_momentum_5d=None, short_vol_ratio=None,
            pct_of_52w_range=data.get("pct_of_52w_range"),
            confidence=0.0, note="Signal unavailable"
        )


def _compute(ticker: str, data: dict) -> DarkPoolSignal:
    hist: pd.DataFrame = data.get("hist", pd.DataFrame())
    price             = data.get("price")
    pct_52w           = data.get("pct_of_52w_range")
    vol_momentum_5d   = data.get("vol_momentum_5d")
    obv_trend         = data.get("obv_trend", "flat")
    momentum_4w       = data.get("price_4w_return")
    momentum_20d      = data.get("momentum_20d")

    # ── Quiver Quant FINRA short-sale volume (optional) ───────────────────────
    short_vol_ratio = _fetch_quiver_short_vol(ticker)

    # ── Signals ───────────────────────────────────────────────────────────────
    bullish_signals = 0
    bearish_signals = 0
    notes = []

    # OBV trend
    if obv_trend == "up":
        bullish_signals += 2
        notes.append("OBV rising — sustained accumulation")
    elif obv_trend == "down":
        bearish_signals += 2
        notes.append("OBV falling — distribution detected")

    # Volume momentum
    if vol_momentum_5d is not None:
        if vol_momentum_5d > 1.3 and (momentum_20d or 0) > 0:
            bullish_signals += 2
            notes.append(f"Volume spike {vol_momentum_5d:.1f}x avg with price up — buying pressure")
        elif vol_momentum_5d > 1.3 and (momentum_20d or 0) < -0.03:
            bearish_signals += 2
            notes.append(f"Volume spike {vol_momentum_5d:.1f}x avg with price down — selling pressure")
        elif vol_momentum_5d > 1.2:
            bullish_signals += 1
            notes.append(f"Above-avg volume ({vol_momentum_5d:.1f}x) — institutional interest")

    # 52-week range position: institutions prefer accumulating below 40% of range
    if pct_52w is not None:
        if pct_52w < 0.30:
            bullish_signals += 1
            notes.append(f"Price in lower 30% of 52w range — value accumulation zone")
        elif pct_52w > 0.85:
            bearish_signals += 1
            notes.append(f"Price near 52w high ({int(pct_52w*100)}%) — extended; watch for distribution")

    # 4-week momentum (institutions build positions over weeks, not days)
    if momentum_4w is not None:
        if momentum_4w > 0.05 and obv_trend == "up":
            bullish_signals += 1
            notes.append(f"4-week return {momentum_4w*100:.1f}% with OBV support — trend intact")
        elif momentum_4w < -0.10:
            bearish_signals += 1
            notes.append(f"4-week return {momentum_4w*100:.1f}% — price deterioration")

    # Quiver Quant: FINRA short-sale ratio
    if short_vol_ratio is not None:
        if short_vol_ratio < 0.38:
            # Low short volume = predominantly real buy-side orders in ATS
            bullish_signals += 2
            notes.append(f"Low ATS short-sale ratio {short_vol_ratio:.0%} — clean institutional buying")
        elif short_vol_ratio > 0.60:
            # High short volume + price rising = potential squeeze
            if (momentum_20d or 0) > 0:
                bullish_signals += 1
                notes.append(f"High ATS short ratio {short_vol_ratio:.0%} + rising price — short squeeze risk")
            else:
                bearish_signals += 2
                notes.append(f"High ATS short ratio {short_vol_ratio:.0%} with price down — distribution")

    # ── Classify ─────────────────────────────────────────────────────────────
    total = bullish_signals + bearish_signals
    if total == 0:
        sentiment     = "neutral"
        volume_signal = "neutral"
        confidence    = 0.2
    elif bullish_signals > bearish_signals:
        sentiment     = "bullish"
        volume_signal = "accumulation"
        confidence    = min(0.9, 0.4 + bullish_signals * 0.1)
    elif bearish_signals > bullish_signals:
        sentiment     = "bearish"
        volume_signal = "distribution"
        confidence    = min(0.9, 0.4 + bearish_signals * 0.1)
    else:
        sentiment     = "neutral"
        volume_signal = "neutral"
        confidence    = 0.3

    note = notes[0] if notes else "No strong accumulation/distribution signal"

    return DarkPoolSignal(
        ticker=ticker,
        sentiment=sentiment,
        volume_signal=volume_signal,
        obv_trend=obv_trend,
        vol_momentum_5d=vol_momentum_5d,
        short_vol_ratio=short_vol_ratio,
        pct_of_52w_range=pct_52w,
        confidence=confidence,
        note=note,
    )


# ── Quiver Quant FINRA short-sale data (free tier) ───────────────────────────

def _fetch_quiver_short_vol(ticker: str) -> float | None:
    """
    Fetch last 5 days of FINRA short-sale data from Quiver Quant.
    Returns the average short-volume ratio (short vol / total vol) over 5 days.

    REQUIRES PAID SUBSCRIPTION ($30/month Hobbyist plan at api.quiverquant.com).
    Returns None if QUIVER_QUANT_KEY not set — all other signals still work without it.

    How to read:
      < 0.38 → Most ATS volume is real buying (bullish)
      0.38–0.55 → Normal range (neutral)
      > 0.55 → Heavy short activity (bearish, unless price is rising = squeeze)
    """
    if not _QQ_KEY:
        return None
    try:
        resp = requests.get(
            _QQ_BASE.format(ticker=ticker),
            headers={"Accept": "application/json", "Authorization": f"Token {_QQ_KEY}"},
            timeout=8,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        # rows is a list of {Date, Short, TotalVolume, ...}
        recent = sorted(rows, key=lambda r: r.get("Date", ""), reverse=True)[:5]
        ratios = []
        for row in recent:
            short = row.get("Short", 0) or 0
            total = row.get("TotalVolume", 0) or 0
            if total > 0:
                ratios.append(short / total)
        return round(sum(ratios) / len(ratios), 3) if ratios else None
    except Exception as e:
        log.debug(f"Quiver Quant darkpool fetch failed for {ticker}: {e}")
        return None
