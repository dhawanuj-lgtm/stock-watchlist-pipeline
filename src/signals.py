"""
signals.py — Daily technical signal detector with overnight flip detection.

Signals (matching your existing tool's vocabulary):
  CONFLUENCE   — Multiple indicators aligned bullish (strongest)
  SQUEEZE ON   — TTM Squeeze proxy: low volatility coil + momentum positive
  CONSOLIDATION — Healthy base-building, no clear direction yet
  RISK WATCH   — Indicators degrading; caution warranted

Flip detection: compares today's signal to cached yesterday signal.
Divergence logic: flags when thesis score contradicts the technical signal.
"""

import json
import logging
from pathlib import Path
from dataclasses import dataclass

log = logging.getLogger(__name__)

CACHE_PATH = Path("cache/signals_cache.json")

# Signal hierarchy (higher = more bullish)
SIGNAL_RANK = {
    "CONFLUENCE":    4,
    "SQUEEZE ON":    3,
    "CONSOLIDATION": 2,
    "RISK WATCH":    1,
}


@dataclass
class SignalResult:
    signal:         str    # CONFLUENCE | SQUEEZE ON | CONSOLIDATION | RISK WATCH
    subtitle:       str    # sub-label shown in email (e.g. "Profile Rules Aligned")
    previous:       str    # yesterday's signal (from cache)
    flipped:        bool   # True if signal changed overnight
    flip_direction: str    # "upgraded" | "downgraded" | "none"
    divergence:     str    # insight text when thesis ≠ technical signal
    factors:        dict   # raw indicator values for report


# ── Public entry points ───────────────────────────────────────────────────────

def detect_signal(data: dict, thesis_score: float) -> SignalResult:
    """
    Compute technical signal for a ticker and detect overnight flip.
    thesis_score — the stable monthly weighted score (0–10)
    """
    ticker = data["ticker"]
    factors = _extract_factors(data)
    signal, subtitle = _classify_signal(factors)

    # Load yesterday's cached signal
    cache = _load_cache()
    previous = cache.get(ticker, {}).get("signal", "UNKNOWN")

    flipped = previous != "UNKNOWN" and previous != signal
    if flipped:
        prev_rank = SIGNAL_RANK.get(previous, 0)
        curr_rank = SIGNAL_RANK.get(signal, 0)
        flip_direction = "upgraded" if curr_rank > prev_rank else "downgraded"
    else:
        flip_direction = "none"

    divergence = _build_divergence(
        ticker=ticker,
        signal=signal,
        previous=previous,
        flipped=flipped,
        flip_direction=flip_direction,
        thesis_score=thesis_score,
        factors=factors,
        archetype=data.get("archetype", ""),
    )

    return SignalResult(
        signal=signal,
        subtitle=subtitle,
        previous=previous,
        flipped=flipped,
        flip_direction=flip_direction,
        divergence=divergence,
        factors=factors,
    )


def save_cache(results: dict[str, SignalResult]) -> None:
    """Persist today's signals for tomorrow's flip comparison."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        ticker: {"signal": r.signal, "subtitle": r.subtitle}
        for ticker, r in results.items()
    }
    with open(CACHE_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    log.info(f"Signal cache saved: {len(payload)} tickers")


# ── Signal classification ─────────────────────────────────────────────────────

def _classify_signal(f: dict) -> tuple[str, str]:
    """
    Map technical indicators to a signal label.
    Returns (signal, subtitle).
    """
    rsi       = f.get("rsi", 50)
    vs_ma50   = f.get("vs_ma50", 0)
    vs_ma200  = f.get("vs_ma200", 0)
    vol_ratio = f.get("vol_ratio", 1)
    momentum  = f.get("momentum_20d", 0)
    squeeze   = f.get("squeeze_proxy", False)

    bullish_count = 0
    bearish_count = 0

    # RSI
    if 40 <= rsi <= 65:
        bullish_count += 1
    elif rsi > 72:
        bearish_count += 1
    elif rsi < 30:
        bullish_count += 1   # oversold = opportunity

    # Trend
    if vs_ma50 > 0:
        bullish_count += 1
    else:
        bearish_count += 1
    if vs_ma200 > 0:
        bullish_count += 1
    else:
        bearish_count += 1

    # Volume
    if vol_ratio > 1.3:
        bullish_count += 1
    elif vol_ratio < 0.6:
        bearish_count += 1

    # Momentum
    if momentum > 0.05:
        bullish_count += 1
    elif momentum < -0.05:
        bearish_count += 1

    # Classify
    if squeeze and bullish_count >= 3:
        return "SQUEEZE ON", "Macro coiling phase"

    if bullish_count >= 4 and bearish_count == 0:
        return "CONFLUENCE", "Profile rules aligned"

    if bullish_count >= 4 and bearish_count == 1:
        return "CONFLUENCE", "Profile rules aligned"

    if squeeze:
        return "SQUEEZE ON", "Macro coiling phase"

    if bullish_count >= 3 and bearish_count <= 1:
        return "CONSOLIDATION", "Base-building phase"

    if bearish_count >= 3:
        return "RISK WATCH", "Macro trend degrading"

    return "CONSOLIDATION", "Mixed signals"


def _extract_factors(data: dict) -> dict:
    rsi       = data.get("rsi_14", 50) or 50
    vs_ma50   = data.get("price_vs_ma50", 0) or 0
    vs_ma200  = data.get("price_vs_ma200", 0) or 0
    vol_ratio = data.get("vol_ratio", 1) or 1
    momentum  = data.get("momentum_20d", 0) or 0
    beta      = data.get("beta", 1) or 1
    atr       = data.get("atr_14")
    price     = data.get("price", 0) or 1

    # TTM Squeeze proxy: low volatility (tight Bollinger Bands) + positive momentum
    # ATR-based: if ATR is below 2% of price, volatility is compressed
    squeeze_proxy = False
    if atr and price:
        atr_pct = atr / price
        squeeze_proxy = atr_pct < 0.025 and momentum > 0   # < 2.5% ATR and positive momentum

    return {
        "rsi":          round(rsi, 1),
        "vs_ma50":      round(vs_ma50 * 100, 1),   # in %
        "vs_ma200":     round(vs_ma200 * 100, 1),
        "vol_ratio":    round(vol_ratio, 2),
        "momentum_20d": round(momentum * 100, 1),   # in %
        "beta":         round(beta, 2),
        "squeeze_proxy":squeeze_proxy,
        "atr_pct":      round((atr / price * 100) if atr and price else 0, 2),
    }


# ── Divergence insight builder ────────────────────────────────────────────────

def _build_divergence(ticker: str, signal: str, previous: str, flipped: bool,
                      flip_direction: str, thesis_score: float,
                      factors: dict, archetype: str) -> str:
    """
    Generate the one-line divergence insight shown in the TLDR email.
    This is the core value-add over the user's existing tool.
    """
    signal_bullish = SIGNAL_RANK.get(signal, 0) >= 3
    signal_bearish = SIGNAL_RANK.get(signal, 0) <= 1
    thesis_strong  = thesis_score >= 7.0
    thesis_weak    = thesis_score <= 4.5

    rsi = factors.get("rsi", 50)
    mom = factors.get("momentum_20d", 0)

    # Case 1: Signal flipped downward — most important case (MRAM scenario)
    if flipped and flip_direction == "downgraded" and thesis_strong:
        return (
            f"Thesis intact (score {thesis_score:.1f}/10). Signal flip on short-term "
            f"price move is noise for a long-term hold. "
            f"{'Oversold RSI ' + str(rsi) + ' may be re-entry zone. ' if rsi < 40 else ''}"
            f"Do not exit on technical flip alone — monitor for fundamental change."
        )

    # Case 2: Signal flipped upward
    if flipped and flip_direction == "upgraded" and thesis_strong:
        return (
            f"Technical and thesis now aligned. Thesis score {thesis_score:.1f}/10 + "
            f"{signal} = high-conviction setup. Verify volume confirms the move before adding."
        )

    # Case 3: Strong technical signal but weak thesis
    if signal_bullish and thesis_weak:
        return (
            f"Caution: {signal} signal without thesis support (score {thesis_score:.1f}/10). "
            f"Technical signals in fundamentally weak stocks are higher-risk. "
            f"Reduce position size or wait for fundamental improvement."
        )

    # Case 4: Weak technical signal but strong thesis
    if signal_bearish and thesis_strong:
        archetype_note = {
            "micro":  "Normal for micro-caps — 10-15% drawdowns are routine.",
            "spec":   "Expected speculative volatility — thesis, not price, drives conviction.",
            "smallg": "Small-cap pullbacks in healthy uptrends are common entry points.",
            "largeg": "Check if sector rotation or macro is driving this, not company-specific.",
            "mega":   "Mega-cap pullbacks in strong businesses are historically buying opportunities.",
        }.get(archetype, "Monitor for any fundamental change.")
        return (
            f"Thesis strong ({thesis_score:.1f}/10) despite RISK WATCH signal. "
            f"{archetype_note} "
            f"{'RSI ' + str(rsi) + ' approaching oversold — watch for re-entry. ' if rsi < 38 else ''}"
            f"Hold unless thesis changes."
        )

    # Case 5: Full alignment
    if signal_bullish and thesis_strong:
        return (
            f"Full alignment: thesis ({thesis_score:.1f}/10) and technicals agree. "
            f"Highest-conviction setup. Size according to your archetype weight."
        )

    # Case 6: Signal consistent — no flip
    if not flipped and signal == previous and previous != "UNKNOWN":
        days_word = "Consistent signal"
        return (
            f"{days_word}: {signal} holding. Thesis score {thesis_score:.1f}/10. "
            f"{'Momentum ' + ('+' if mom > 0 else '') + str(mom) + '% over 20 days.' if mom else ''} "
            f"No action trigger — continue holding as planned."
        ).strip()

    # Default
    return (
        f"Thesis score {thesis_score:.1f}/10. Technical signal {signal}. "
        f"Review full report for details."
    )


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Could not load signal cache: {e}")
        return {}
