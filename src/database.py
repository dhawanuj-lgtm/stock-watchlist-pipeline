"""
database.py — CSV-based score history manager.

Stores weekly scores, signals, and prices per ticker in:
  data/history/{TICKER}.csv

Columns: date, score, signal, price, score_light, bull_count, bear_count

Designed to be committed back to the repo each run so GitHub becomes
the free-tier historical database with full version-control audit trail.
"""

import csv
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

HISTORY_DIR = Path("data/history")
COLUMNS = ["date", "score", "signal", "price", "score_light", "bull_count", "bear_count"]


def _ensure_dir() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _path(ticker: str) -> Path:
    return HISTORY_DIR / f"{ticker}.csv"


# ── Write ─────────────────────────────────────────────────────────────────────

def write_score_row(
    ticker: str,
    score: float,
    signal: str,
    price: float | None,
    score_light: str,
    bull_count: int,
    bear_count: int,
    run_date: str | None = None,
) -> None:
    """Append one weekly row to the ticker's history CSV. Creates file if needed."""
    _ensure_dir()
    today = run_date or date.today().isoformat()
    path = _path(ticker)
    file_exists = path.exists()

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "date":        today,
            "score":       round(score, 2),
            "signal":      signal,
            "price":       round(price, 4) if price else "",
            "score_light": score_light,
            "bull_count":  bull_count,
            "bear_count":  bear_count,
        })
    log.debug(f"History: wrote {ticker} {today} score={score} signal={signal}")


# ── Read ──────────────────────────────────────────────────────────────────────

def read_history(ticker: str, n_weeks: int = 16) -> list[dict]:
    """Return last N rows for ticker as list of dicts. Empty list if no history."""
    path = _path(ticker)
    if not path.exists():
        return []
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        return rows[-n_weeks:]
    except Exception as e:
        log.warning(f"History read failed for {ticker}: {e}")
        return []


def read_all_history() -> dict[str, list[dict]]:
    """Return history dict for all tickers that have CSV files."""
    _ensure_dir()
    result = {}
    for path in sorted(HISTORY_DIR.glob("*.csv")):
        ticker = path.stem
        try:
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
            if rows:
                result[ticker] = rows
        except Exception as e:
            log.warning(f"History read failed for {ticker}: {e}")
    return result


def get_current_prices(all_results: list[dict]) -> dict[str, float]:
    """Extract ticker → current price from the pipeline results list."""
    prices = {}
    for r in all_results:
        price = r.get("data", {}).get("price")
        if price:
            prices[r["ticker"]] = float(price)
    return prices
