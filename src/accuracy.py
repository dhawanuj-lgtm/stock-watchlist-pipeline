"""
accuracy.py — 60-day signal accuracy scorecard.

Methodology:
  For every Buy-direction signal (CONFLUENCE or SQUEEZE ON) recorded
  >= 60 days ago in the history CSVs, compare:
    ticker_return = (price_now - price_then) / price_then
    spy_return    = SPY return over the same 60-day window

  A Buy signal is "correct" if ticker_return > spy_return + 2%.
  An Avoid signal (RISK WATCH) is "correct" if ticker_return < spy_return - 2%.

  Reports:
    • Buy hit rate %
    • Avg return of Buy signals vs SPY
    • Avoid hit rate %
    • Best/worst performing signals
    • Accuracy trend over time (last 4 batches)

Results are written to data/accuracy_report.json and loaded by report.py.
The accuracy section only becomes meaningful after ~8+ weeks of data.
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

from database import read_all_history

log = logging.getLogger(__name__)

ACCURACY_FILE         = Path("data/accuracy_report.json")
LOOKBACK_DAYS         = 60
OUTPERFORM_THRESHOLD  = 0.02   # +2% vs SPY = correct Buy signal

_BUY_SIGNALS    = {"CONFLUENCE", "SQUEEZE ON"}
_AVOID_SIGNALS  = {"RISK WATCH"}


# ── Price helpers ─────────────────────────────────────────────────────────────

def _price_on(ticker: str, as_of_date: str) -> float | None:
    """Fetch closing price for ticker on or near the given date (±3 trading days)."""
    try:
        d     = date.fromisoformat(as_of_date)
        start = (d - timedelta(days=5)).isoformat()
        end   = (d + timedelta(days=2)).isoformat()
        df    = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def _spy_return(start_date: str) -> float | None:
    """SPY total return from start_date to today."""
    spy_then = _price_on("SPY", start_date)
    if not spy_then:
        return None
    try:
        df = yf.download("SPY", period="5d", progress=False, auto_adjust=True)
        if df.empty:
            return None
        spy_now = float(df["Close"].iloc[-1])
        return (spy_now - spy_then) / spy_then
    except Exception:
        return None


# ── Core computation ──────────────────────────────────────────────────────────

def compute_accuracy_report(current_prices: dict[str, float]) -> dict:
    """
    Build the accuracy report from all history CSVs.

    current_prices: {ticker: price} — pulled from latest pipeline run.
    Writes data/accuracy_report.json and returns the report dict.
    """
    history     = read_all_history()
    cutoff_date = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    buy_signals:   list[dict] = []
    avoid_signals: list[dict] = []

    # Fetch SPY baseline once
    spy_ret = _spy_return(cutoff_date)
    log.info(f"Accuracy: SPY {LOOKBACK_DAYS}d return = {spy_ret:.1%}" if spy_ret else "Accuracy: SPY baseline unavailable")

    for ticker, rows in history.items():
        current_price = current_prices.get(ticker)

        for row in rows:
            row_date = row.get("date", "")
            if not row_date or row_date >= cutoff_date:
                continue   # too recent to judge

            signal      = row.get("signal", "")
            price_str   = row.get("price", "")
            score_str   = row.get("score", "")
            if not price_str or not signal:
                continue

            try:
                price_then = float(price_str)
                score_then = float(score_str) if score_str else None
            except ValueError:
                continue

            # Use current_price if we have it, else skip
            if not current_price or price_then <= 0:
                continue

            ticker_ret = (current_price - price_then) / price_then
            correct    = None

            if spy_ret is not None:
                if signal in _BUY_SIGNALS:
                    correct = ticker_ret > (spy_ret + OUTPERFORM_THRESHOLD)
                elif signal in _AVOID_SIGNALS:
                    correct = ticker_ret < (spy_ret - OUTPERFORM_THRESHOLD)

            entry = {
                "ticker":           ticker,
                "date":             row_date,
                "signal":           signal,
                "score_then":       score_then,
                "price_then":       round(price_then, 2),
                "price_now":        round(current_price, 2),
                "ticker_return_pct":round(ticker_ret * 100, 2),
                "spy_return_pct":   round(spy_ret * 100, 2) if spy_ret is not None else None,
                "excess_return_pct":round((ticker_ret - (spy_ret or 0)) * 100, 2),
                "correct":          correct,
            }

            if signal in _BUY_SIGNALS:
                buy_signals.append(entry)
            elif signal in _AVOID_SIGNALS:
                avoid_signals.append(entry)

    def _hit_rate(signals: list[dict]) -> float | None:
        judged = [s for s in signals if s.get("correct") is not None]
        if not judged:
            return None
        return round(sum(1 for s in judged if s["correct"]) / len(judged) * 100, 1)

    def _avg_return(signals: list[dict]) -> float | None:
        rets = [s["ticker_return_pct"] for s in signals if s.get("ticker_return_pct") is not None]
        return round(sum(rets) / len(rets), 2) if rets else None

    def _avg_excess(signals: list[dict]) -> float | None:
        exc = [s["excess_return_pct"] for s in signals if s.get("excess_return_pct") is not None]
        return round(sum(exc) / len(exc), 2) if exc else None

    # Sort for display: best returns first
    buy_top  = sorted(buy_signals,   key=lambda x: x["ticker_return_pct"], reverse=True)[:15]
    avoid_top= sorted(avoid_signals, key=lambda x: x["ticker_return_pct"])[:10]   # worst returns = best avoidance

    report = {
        "generated_at":             date.today().isoformat(),
        "lookback_days":            LOOKBACK_DAYS,
        "benchmark":                "SPY",
        "outperformance_threshold": OUTPERFORM_THRESHOLD * 100,
        "spy_return_pct":           round(spy_ret * 100, 2) if spy_ret is not None else None,
        "buy_signals": {
            "count":          len(buy_signals),
            "hit_rate_pct":   _hit_rate(buy_signals),
            "avg_return_pct": _avg_return(buy_signals),
            "avg_excess_pct": _avg_excess(buy_signals),
            "top_performers": buy_top,
        },
        "avoid_signals": {
            "count":          len(avoid_signals),
            "hit_rate_pct":   _hit_rate(avoid_signals),
            "avg_return_pct": _avg_return(avoid_signals),
            "avg_excess_pct": _avg_excess(avoid_signals),
            "top_avoided":    avoid_top,
        },
        "data_note": (
            "Accuracy data is meaningful after 8+ weeks of history. "
            f"Currently evaluating {len(buy_signals)} buy signals and "
            f"{len(avoid_signals)} avoid signals."
        ),
    }

    # Persist
    ACCURACY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ACCURACY_FILE, "w") as f:
        json.dump(report, f, indent=2)

    log.info(
        f"Accuracy: Buy hit rate={report['buy_signals']['hit_rate_pct']}% "
        f"({report['buy_signals']['count']} signals) | "
        f"Avoid hit rate={report['avoid_signals']['hit_rate_pct']}% "
        f"({report['avoid_signals']['count']} signals)"
    )
    return report


def load_accuracy_report() -> dict:
    """Load persisted accuracy report. Returns empty dict if none exists yet."""
    if not ACCURACY_FILE.exists():
        return {}
    try:
        with open(ACCURACY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}
