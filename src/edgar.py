"""
edgar.py — SEC EDGAR free fundamentals fetcher.

Uses the EDGAR XBRL company facts API (no API key required):
  https://data.sec.gov/api/xbrl/companyfacts/CIK{10-digit-CIK}.json

Fetches most recent annual values for:
  Revenue, Gross Profit / Margin, Net Income, EPS (diluted),
  Operating Cash Flow, CapEx → Free Cash Flow, Total Debt.

Ticker → CIK mapping is cached locally at data/edgar_cik_map.json
to avoid re-fetching 13 MB on every run.

All errors are non-fatal: returns empty dict on any failure so the
pipeline continues with yfinance data as fallback.
"""

import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "stock-watchlist-pipeline andhawan@paypal.com"}
_TICKERS_URL  = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL    = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_CIK_CACHE    = Path("data/edgar_cik_map.json")

_cik_map: dict[str, str] = {}   # module-level — loaded once per run


# ── CIK resolution ────────────────────────────────────────────────────────────

def _load_cik_map() -> dict[str, str]:
    global _cik_map
    if _cik_map:
        return _cik_map

    # Try disk cache first
    if _CIK_CACHE.exists():
        try:
            with open(_CIK_CACHE) as f:
                _cik_map = json.load(f)
            log.info(f"EDGAR: CIK map loaded from cache ({len(_cik_map):,} entries)")
            return _cik_map
        except Exception:
            pass

    # Fetch from SEC (one-time, ~3 MB JSON)
    try:
        log.info("EDGAR: fetching CIK map from SEC (first run only)...")
        r = requests.get(_TICKERS_URL, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        raw = r.json()
        _cik_map = {
            v["ticker"].upper(): str(v["cik_str"]).zfill(10)
            for v in raw.values()
        }
        _CIK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CIK_CACHE, "w") as f:
            json.dump(_cik_map, f)
        log.info(f"EDGAR: CIK map fetched and cached ({len(_cik_map):,} tickers)")
    except Exception as e:
        log.warning(f"EDGAR: CIK map fetch failed — {e}")
    return _cik_map


# ── XBRL value extraction ─────────────────────────────────────────────────────

def _latest_annual(facts: dict, concept: str, unit: str = "USD") -> float | None:
    """
    Extract the most recent annual (10-K) value for a US-GAAP XBRL concept.
    Skips quarterly (10-Q) and frame-tagged entries to avoid double-counting.
    """
    try:
        entries = facts["facts"]["us-gaap"][concept]["units"][unit]
    except KeyError:
        return None

    # Keep only 10-K filings, no 'frame' key (frame = cumulative periods)
    annual = [
        e for e in entries
        if e.get("form") in ("10-K", "10-K/A") and "frame" not in e
    ]
    if not annual:
        return None

    annual.sort(key=lambda e: e["end"])
    return float(annual[-1]["val"])


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_edgar_fundamentals(ticker: str) -> dict:
    """
    Fetch key fundamentals from SEC EDGAR for a ticker.

    Returns a dict with edgar_* prefixed keys, or empty dict on any failure.
    Designed to supplement yfinance — fills None gaps without overriding
    good yfinance data (that decision is made in fetcher.py).
    """
    cik = _load_cik_map().get(ticker.upper())
    if not cik:
        log.debug(f"EDGAR: no CIK for {ticker} — skipping")
        return {}

    try:
        url = _FACTS_URL.format(cik=cik)
        r = requests.get(url, headers=_HEADERS, timeout=30)
        if r.status_code == 404:
            log.debug(f"EDGAR: no facts page for {ticker} (CIK {cik})")
            return {}
        r.raise_for_status()
        facts = r.json()
        time.sleep(0.12)   # SEC asks for courteous rate limiting

        # Revenue — try multiple common XBRL concepts
        revenue = (
            _latest_annual(facts, "Revenues")
            or _latest_annual(facts, "RevenueFromContractWithCustomerExcludingAssessedTax")
            or _latest_annual(facts, "SalesRevenueNet")
            or _latest_annual(facts, "RevenueFromContractWithCustomerIncludingAssessedTax")
        )

        gross_profit  = _latest_annual(facts, "GrossProfit")
        net_income    = _latest_annual(facts, "NetIncomeLoss")
        eps_diluted   = _latest_annual(facts, "EarningsPerShareDiluted", unit="USD/shares")
        op_cf         = _latest_annual(facts, "NetCashProvidedByUsedInOperatingActivities")
        capex         = _latest_annual(facts, "PaymentsToAcquirePropertyPlantAndEquipment")
        total_debt    = (
            _latest_annual(facts, "LongTermDebt")
            or _latest_annual(facts, "LongTermDebtNoncurrent")
        )
        rd_expense    = _latest_annual(facts, "ResearchAndDevelopmentExpense")

        # Computed
        gross_margin = (gross_profit / revenue) if (gross_profit and revenue and revenue > 0) else None
        fcf          = ((op_cf or 0) - abs(capex or 0)) if (op_cf is not None or capex is not None) else None

        result = {
            "edgar_revenue":       revenue,
            "edgar_gross_profit":  gross_profit,
            "edgar_gross_margin":  round(gross_margin, 4) if gross_margin is not None else None,
            "edgar_net_income":    net_income,
            "edgar_eps_diluted":   eps_diluted,
            "edgar_op_cf":         op_cf,
            "edgar_capex":         capex,
            "edgar_fcf":           fcf,
            "edgar_total_debt":    total_debt,
            "edgar_rd_expense":    rd_expense,
        }
        # Strip None values — caller checks for key presence
        result = {k: v for k, v in result.items() if v is not None}

        if result:
            log.info(
                f"EDGAR {ticker}: "
                f"rev=${revenue/1e6:.0f}M " if revenue else f"EDGAR {ticker}: "
                + (f"GM={gross_margin:.1%} " if gross_margin else "")
                + (f"FCF=${fcf/1e6:.0f}M" if fcf else "")
            )
        return result

    except Exception as e:
        log.warning(f"EDGAR {ticker}: fetch failed (non-fatal) — {e}")
        return {}
