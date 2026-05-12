"""
scorer.py — Archetype-aware scoring engine.

For each ticker produces:
  • Per-category score (0–10) and traffic light (green / yellow / red)
  • Top 3 bull flags and top 3 bear flags (data-driven, not templated)
  • Weighted overall score based on archetype
  • Category weights differ across mega / largeg / smallg / spec / micro
"""

from dataclasses import dataclass, field
from typing import Optional


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class CategoryResult:
    score:      float           # 0–10
    light:      str             # "green" | "yellow" | "red" | "gray"
    factors:    list[str]       # individual criterion results (used for flag extraction)
    flags_bull: list[str] = field(default_factory=list)
    flags_bear: list[str] = field(default_factory=list)

@dataclass
class TickerScore:
    ticker:           str
    archetype:        str
    weighted_score:   float          # 0–10 overall, archetype-weighted
    weighted_light:   str
    categories:       dict[str, CategoryResult]
    bull_flags:       list[str]      # top 3 across all categories
    bear_flags:       list[str]      # top 3 across all categories
    thesis_data:      dict           # raw numbers for AI thesis prompt


# ── Archetype category weights ────────────────────────────────────────────────
# Values: 0 = skip, 1 = low, 2 = medium, 3 = high, 4 = critical

ARCHETYPE_WEIGHTS = {
    #                    fund  val  tech  sent  mgmt  moat  part  macro  cat   risk  inst
    "mega":   dict(fundamentals=4, valuation=4, technical=1, sentiment=2, management=2,
                   moat=4, partnerships=1, macro=3, catalysts=1, risk=3, institutional=4),
    "largeg": dict(fundamentals=3, valuation=2, technical=3, sentiment=3, management=3,
                   moat=4, partnerships=2, macro=2, catalysts=3, risk=2, institutional=4),
    "smallg": dict(fundamentals=3, valuation=3, technical=2, sentiment=2, management=4,
                   moat=3, partnerships=3, macro=1, catalysts=4, risk=3, institutional=3),
    "spec":   dict(fundamentals=1, valuation=1, technical=2, sentiment=3, management=4,
                   moat=1, partnerships=4, macro=1, catalysts=4, risk=4, institutional=2),
    "micro":  dict(fundamentals=1, valuation=2, technical=1, sentiment=1, management=4,
                   moat=3, partnerships=4, macro=0, catalysts=4, risk=4, institutional=1),
}


# ── Public entry point ────────────────────────────────────────────────────────

def score_ticker(data: dict, thesis: dict) -> TickerScore:
    """
    Score a ticker across all categories.
    data     — from fetcher.fetch_ticker()
    thesis   — from thesis_scores.yaml (the stable monthly layer)
    """
    arch = data["archetype"]
    weights = ARCHETYPE_WEIGHTS.get(arch, ARCHETYPE_WEIGHTS["largeg"])

    cats: dict[str, CategoryResult] = {}

    cats["fundamentals"]  = _score_fundamentals(data, thesis, arch)
    cats["valuation"]     = _score_valuation(data, thesis, arch)
    cats["technical"]     = _score_technical(data, arch)
    cats["sentiment"]     = _score_sentiment(data, arch)
    cats["management"]    = _score_management(data, thesis, arch)
    cats["moat"]          = _score_moat(data, thesis, arch)
    cats["partnerships"]  = _score_partnerships(thesis, arch)
    cats["macro"]         = _score_macro(data, arch)
    cats["catalysts"]     = _score_catalysts(data, arch)
    cats["risk"]          = _score_risk(data, thesis, arch)
    cats["institutional"] = _score_institutional(data, arch)

    # Weighted overall score
    total_weight = 0
    weighted_sum = 0.0
    for cat_name, result in cats.items():
        w = weights.get(cat_name, 0)
        if w == 0 or result.light == "gray":
            continue
        total_weight += w
        weighted_sum += result.score * w

    weighted_score = round(weighted_sum / total_weight, 1) if total_weight > 0 else 0.0

    # ── Macro adjustment (±0.5 max, driven by FRED data already in data["macro"]) ──
    weighted_score = _apply_macro_adjustment(weighted_score, arch, data)
    weighted_light = _light(weighted_score)

    # Aggregate bull / bear flags
    all_bull: list[tuple[float, str]] = []
    all_bear: list[tuple[float, str]] = []
    for cat_name, result in cats.items():
        w = weights.get(cat_name, 1)
        for flag in result.flags_bull:
            all_bull.append((result.score * w, flag))
        for flag in result.flags_bear:
            all_bear.append(((10 - result.score) * w, flag))

    all_bull.sort(key=lambda x: x[0], reverse=True)
    all_bear.sort(key=lambda x: x[0], reverse=True)
    top_bull = [f for _, f in all_bull[:3]]
    top_bear = [f for _, f in all_bear[:3]]

    thesis_data = {
        "weighted_score":  weighted_score,
        "revenue_growth":  _pct(data.get("revenue_growth_yoy")),
        "gross_margin":    _pct(data.get("gross_margin")),
        "fcf":             data.get("free_cashflow"),
        "pe_forward":      data.get("pe_forward"),
        "short_float":     _pct(data.get("short_float_pct")),
        "inst_ownership":  _pct(data.get("inst_ownership_pct")),
        "rsi":             data.get("rsi_14"),
        "analyst_upside":  _pct(data.get("analyst_upside")),
    }

    return TickerScore(
        ticker=data["ticker"],
        archetype=arch,
        weighted_score=weighted_score,
        weighted_light=weighted_light,
        categories=cats,
        bull_flags=top_bull,
        bear_flags=top_bear,
        thesis_data=thesis_data,
    )


# ── Macro adjustment ──────────────────────────────────────────────────────────

_RATE_SENSITIVE = {"fintech", "real estate", "insurtech", "banking", "financial"}
_GROWTH_ARCHS   = {"largeg", "smallg"}
_SPEC_ARCHS     = {"micro", "spec"}

def _apply_macro_adjustment(score: float, arch: str, data: dict) -> float:
    """
    Apply a contextual ±0.5 adjustment based on macro environment.
    Uses FRED data already fetched in data["macro"]. Max impact: ±0.5 pts.
    """
    macro = data.get("macro", {})
    if not macro:
        return score

    ffr    = macro.get("fed_funds_rate")
    spread = macro.get("yield_spread_10_2")     # 10Y − 2Y (negative = inverted)
    sector = (data.get("sector") or "").lower()

    adj = 0.0

    # Rate-sensitive sectors penalised in high-rate environment
    if ffr and ffr > 4.5 and any(s in sector for s in _RATE_SENSITIVE):
        adj -= 0.3

    # Inverted yield curve hurts growth stocks (signals recession ahead)
    if spread is not None and spread < -0.2 and arch in _GROWTH_ARCHS:
        adj -= 0.2

    # Risk-off (inverted curve + high FFR) is extra harsh for speculative positions
    if spread is not None and spread < -0.3 and ffr and ffr > 4.5 and arch in _SPEC_ARCHS:
        adj -= 0.4

    # Risk-on environment: steep positive curve + low rates → mild bonus
    if spread is not None and spread > 0.5 and ffr and ffr < 3.5:
        adj += 0.2

    # Cap total adjustment
    adj = max(-0.5, min(0.5, adj))
    return round(max(0.0, min(10.0, score + adj)), 1)


# ── Category scorers ──────────────────────────────────────────────────────────

def _score_fundamentals(data: dict, thesis: dict, arch: str) -> CategoryResult:
    score_parts: list[float] = []
    bull: list[str] = []
    bear: list[str] = []

    # If thesis has a manual score, use it as the anchor (weight 50%)
    manual = thesis.get("fundamentals")

    # Revenue growth
    rg = data.get("revenue_growth_yoy")
    if rg is not None:
        s = _bracket(rg, [(0.30, 10), (0.20, 8), (0.10, 6), (0.05, 4), (0.0, 2)], 0)
        score_parts.append(s)
        if s >= 8:
            bull.append(f"Revenue growing {_pct(rg)} YoY — strong top-line momentum")
        elif s <= 2:
            bear.append(f"Revenue declining {_pct(rg)} YoY — top-line under pressure")

    # Gross margin
    gm = data.get("gross_margin")
    if gm is not None:
        s = _bracket(gm, [(0.60, 10), (0.40, 8), (0.25, 6), (0.15, 4), (0.0, 2)], 1)
        score_parts.append(s)
        if s >= 8:
            bull.append(f"Gross margin {_pct(gm)} — strong pricing power")
        elif s <= 3:
            bear.append(f"Gross margin thin at {_pct(gm)} — limited pricing buffer")

    # Free cash flow
    fcf = data.get("free_cashflow")
    mcap = data.get("market_cap")
    if fcf is not None and mcap and mcap > 0:
        fcf_yield = fcf / mcap
        s = _bracket(fcf_yield, [(0.06, 10), (0.03, 8), (0.01, 6), (0, 4)], 1)
        score_parts.append(s)
        if fcf > 0 and s >= 7:
            bull.append(f"FCF yield {_pct(fcf_yield)} — self-funding growth")
        elif fcf is not None and fcf < 0:
            s = 2 if arch in ("spec", "micro") else 1
            bear.append("Negative free cash flow — burning cash")
    elif arch in ("spec", "micro"):
        # Pre-revenue / pre-profit: check cash runway instead
        cash = data.get("total_cash")
        if cash and cash > 0:
            score_parts.append(5)  # neutral — cash exists
            bull.append(f"Cash on hand: ${cash/1e6:.0f}M — check runway vs burn rate")

    # Debt/Equity
    de = data.get("debt_to_equity")
    if de is not None:
        s = _bracket(de, [(30, 10), (70, 8), (100, 6), (200, 4)], 1, reverse=True)
        score_parts.append(s)
        if de > 200:
            bear.append(f"D/E ratio {de:.0f}% — elevated leverage risk")
        elif de < 30:
            bull.append(f"Clean balance sheet — D/E {de:.0f}%")

    # Earnings consistency (beat rate proxy — yfinance + Finnhub)
    eh = data.get("earnings_history", [])
    if eh:
        beats = sum(1 for e in eh if e.get("surprise_pct", 0) > 0)
        beat_rate = beats / len(eh)
        s = _bracket(beat_rate, [(0.75, 9), (0.5, 7), (0.25, 4)], 2)
        score_parts.append(s)
        if beat_rate >= 0.75:
            bull.append(f"Beats estimates {int(beat_rate*100)}% of quarters — reliable guidance")
        elif beat_rate < 0.4:
            bear.append(f"Only {int(beat_rate*100)}% earnings beat rate — execution risk")

    # Finnhub earnings surprise (supplements yfinance when available)
    fh_beat = data.get("fh_beat")
    fh_miss = data.get("fh_miss")
    fh_streak = data.get("fh_consecutive_beats", 0)
    if fh_beat is not None:
        if fh_beat:
            score_parts.append(8.0)
            if fh_streak and fh_streak >= 3:
                bull.append(f"Finnhub: {fh_streak} consecutive earnings beats — execution track record")
            else:
                bull.append(f"Finnhub: beat estimates last quarter by {data.get('fh_eps_surprise_pct', 0):.1f}%")
        elif fh_miss:
            score_parts.append(3.0)
            bear.append(f"Finnhub: missed earnings by {abs(data.get('fh_eps_surprise_pct', 0)):.1f}% last quarter")

    computed = _avg(score_parts) if score_parts else None
    final = _blend(manual, computed, manual_weight=0.5)
    return CategoryResult(score=final, light=_light(final), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


def _score_valuation(data: dict, thesis: dict, arch: str) -> CategoryResult:
    score_parts = []
    bull: list[str] = []
    bear: list[str] = []
    manual = thesis.get("valuation")

    # Skip valuation for pre-revenue speculative / micro
    if arch in ("spec", "micro") and manual is None:
        return CategoryResult(score=5.0, light="gray", factors=[], flags_bull=[], flags_bear=[])

    # PEG ratio
    peg = data.get("peg_ratio")
    if peg and peg > 0:
        s = _bracket(peg, [(0.5, 10), (1.0, 8), (1.5, 6), (2.5, 4)], 2, reverse=True)
        score_parts.append(s)
        if peg < 1.0:
            bull.append(f"PEG {peg:.1f} — growth at a reasonable price")
        elif peg > 2.5:
            bear.append(f"PEG {peg:.1f} — expensive relative to growth rate")

    # Analyst upside
    upside = data.get("analyst_upside")
    if upside is not None:
        s = _bracket(upside, [(0.30, 10), (0.15, 8), (0.05, 6), (0, 4)], 2)
        score_parts.append(s)
        if upside > 0.25:
            bull.append(f"{_pct(upside)} upside to consensus analyst target")
        elif upside < -0.05:
            bear.append(f"Trading {_pct(-upside)} above analyst consensus target")

    # EV/Revenue (for growth)
    ev_rev = data.get("ev_to_revenue")
    if ev_rev and arch in ("largeg", "smallg"):
        s = _bracket(ev_rev, [(2, 10), (5, 8), (10, 6), (20, 4)], 2, reverse=True)
        score_parts.append(s)
        if ev_rev > 20:
            bear.append(f"EV/Revenue {ev_rev:.1f}x — priced for perfection")

    # P/E forward
    pe = data.get("pe_forward")
    if pe and pe > 0 and arch in ("mega", "largeg", "smallg"):
        s = _bracket(pe, [(12, 10), (20, 8), (30, 6), (50, 4)], 1, reverse=True)
        score_parts.append(s)

    computed = _avg(score_parts) if score_parts else None
    final = _blend(manual, computed, manual_weight=0.5)
    return CategoryResult(score=final, light=_light(final), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


def _score_technical(data: dict, arch: str) -> CategoryResult:
    score_parts = []
    bull: list[str] = []
    bear: list[str] = []

    # RSI
    rsi = data.get("rsi_14")
    if rsi is not None:
        if rsi < 30:
            score_parts.append(7)
            bull.append(f"RSI {rsi:.0f} — oversold, potential entry zone")
        elif 40 <= rsi <= 65:
            score_parts.append(8)
            bull.append(f"RSI {rsi:.0f} — healthy momentum range")
        elif rsi > 75:
            score_parts.append(3)
            bear.append(f"RSI {rsi:.0f} — overbought, elevated pullback risk")
        else:
            score_parts.append(5)

    # Price vs 50MA
    vs50 = data.get("price_vs_ma50")
    if vs50 is not None:
        if vs50 > 0.05:
            score_parts.append(8)
            bull.append(f"Price {_pct(vs50)} above 50-day MA — uptrend confirmed")
        elif vs50 > 0:
            score_parts.append(6)
        elif vs50 < -0.10:
            score_parts.append(2)
            bear.append(f"Price {_pct(-vs50)} below 50-day MA — downtrend in force")
        else:
            score_parts.append(4)

    # Price vs 200MA
    vs200 = data.get("price_vs_ma200")
    if vs200 is not None:
        if vs200 > 0:
            score_parts.append(7)
            bull.append("Trading above 200-day MA — long-term uptrend intact")
        else:
            score_parts.append(3)
            bear.append(f"Trading {_pct(-vs200)} below 200-day MA — long-term trend broken")

    # Volume ratio
    vr = data.get("vol_ratio")
    if vr is not None:
        if vr > 1.5:
            score_parts.append(8)
            bull.append(f"Volume {vr:.1f}x average — elevated institutional participation")
        elif vr < 0.5:
            score_parts.append(4)
            bear.append("Volume drying up — low conviction in current move")
        else:
            score_parts.append(6)

    # 20-day momentum
    mom = data.get("momentum_20d")
    if mom is not None:
        if mom > 0.15:
            bull.append(f"{_pct(mom)} price return over last 20 days — strong momentum")
        elif mom < -0.15:
            bear.append(f"{_pct(-mom)} price decline over last 20 days — negative momentum")

    # 52-week range position
    pct_range = data.get("pct_of_52w_range")
    if pct_range is not None:
        if pct_range > 0.85:
            bear.append(f"Near 52-week high ({int(pct_range*100)}% of range) — limited near-term upside")
        elif pct_range < 0.20:
            bull.append(f"Near 52-week low ({int(pct_range*100)}% of range) — potential mean reversion")

    score = _avg(score_parts) if score_parts else 5.0
    return CategoryResult(score=score, light=_light(score), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


def _score_sentiment(data: dict, arch: str) -> CategoryResult:
    score_parts = []
    bull: list[str] = []
    bear: list[str] = []

    # Short interest — context-aware by archetype
    si = data.get("short_float_pct")
    if si is not None:
        si_pct = si * 100
        if arch in ("spec", "micro"):
            # High short interest = squeeze fuel (positive for speculative)
            if si_pct > 20:
                score_parts.append(8)
                bull.append(f"Short float {si_pct:.1f}% — significant squeeze potential")
            elif si_pct > 10:
                score_parts.append(6)
                bull.append(f"Short float {si_pct:.1f}% — moderate squeeze fuel")
            else:
                score_parts.append(4)
        else:
            # For anchor / growth: high short = bearish risk
            if si_pct > 25:
                score_parts.append(3)
                bear.append(f"Short float {si_pct:.1f}% — heavy smart money short position")
            elif si_pct > 15:
                score_parts.append(5)
            elif si_pct < 5:
                score_parts.append(7)
                bull.append(f"Short float only {si_pct:.1f}% — low bearish conviction")
            else:
                score_parts.append(6)

    # Analyst consensus (1=Strong Buy → 5=Strong Sell)
    rec = data.get("recommendation")
    if rec is not None:
        s = _bracket(rec, [(1.5, 9), (2.0, 7), (2.5, 5), (3.0, 3)], 2, reverse=True)
        score_parts.append(s)
        if rec <= 1.5:
            bull.append(f"Strong Buy consensus ({data.get('analyst_count', 0)} analysts)")
        elif rec >= 3.0:
            bear.append(f"Analyst consensus neutral/negative — mean rating {rec:.1f}/5")

    # News sentiment
    ns = data.get("news_sentiment", {})
    ns_score = ns.get("score")
    if ns_score is not None:
        s = _bracket(ns_score, [(0.3, 9), (0.1, 7), (-0.1, 5), (-0.3, 3)], 2)
        score_parts.append(s)
        if ns_score > 0.2:
            bull.append(f"Positive news sentiment over last 7 days")
        elif ns_score < -0.2:
            bear.append(f"Negative news flow over last 7 days")

    score = _avg(score_parts) if score_parts else 5.0
    return CategoryResult(score=score, light=_light(score), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


def _score_management(data: dict, thesis: dict, arch: str) -> CategoryResult:
    """Largely thesis-driven — data contributes insider activity proxy."""
    manual = thesis.get("management")
    score_parts = []
    bull: list[str] = []
    bear: list[str] = []

    # Insider buy/sell from recent transactions
    txns = data.get("insider_txns", [])
    if txns:
        buys = sum(1 for t in txns if "buy" in str(t.get("transaction", "")).lower() or
                   "purchase" in str(t.get("transaction", "")).lower())
        sells = sum(1 for t in txns if "sell" in str(t.get("transaction", "")).lower() or
                    "sale" in str(t.get("transaction", "")).lower())
        if buys > sells and buys >= 2:
            score_parts.append(8)
            bull.append(f"{buys} insider buys vs {sells} sells (last 6 months) — alignment signal")
        elif sells > buys * 3:
            score_parts.append(3)
            bear.append(f"Heavy insider selling: {sells} transactions vs {buys} buys")
        else:
            score_parts.append(5)

    computed = _avg(score_parts) if score_parts else None
    final = _blend(manual, computed, manual_weight=0.8)  # management is mostly manual
    return CategoryResult(score=final, light=_light(final), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


def _score_moat(data: dict, thesis: dict, arch: str) -> CategoryResult:
    manual = thesis.get("moat")
    final = float(manual) if manual is not None else 5.0
    bull, bear = [], []
    if final >= 8:
        bull.append("Strong moat confirmed in thesis review (switching costs / network effect)")
    elif final <= 4:
        bear.append("Thin competitive moat — margin pressure risk from competition")
    return CategoryResult(score=final, light=_light(final), factors=[],
                          flags_bull=bull, flags_bear=bear)


def _score_partnerships(thesis: dict, arch: str) -> CategoryResult:
    manual = thesis.get("partnerships")
    final = float(manual) if manual is not None else 5.0
    bull, bear = [], []
    if final >= 8:
        bull.append("Strong strategic partnerships — distribution and revenue visibility")
    elif final <= 3:
        bear.append("Limited partnership ecosystem — growth dependent on direct sales only")
    return CategoryResult(score=final, light=_light(final), factors=[],
                          flags_bull=bull, flags_bear=bear)


def _score_macro(data: dict, arch: str) -> CategoryResult:
    if arch == "micro":
        return CategoryResult(score=5.0, light="gray", factors=[], flags_bull=[], flags_bear=[])

    score_parts = []
    bull: list[str] = []
    bear: list[str] = []
    macro = data.get("macro", {})

    # Yield curve — inverted = recession risk for cyclicals
    spread = macro.get("yield_spread_10_2")
    if spread is not None:
        if spread > 0.5:
            score_parts.append(7)
            bull.append(f"Yield curve healthy ({spread:+.2f}%) — growth environment supportive")
        elif spread < 0:
            score_parts.append(4)
            bear.append(f"Inverted yield curve ({spread:+.2f}%) — recession risk elevated")
        else:
            score_parts.append(5)

    # Beta vs rate sensitivity
    beta = data.get("beta")
    fed = macro.get("fed_funds_rate")
    if beta and fed and fed > 4.5 and beta > 1.5:
        bear.append(f"High beta ({beta:.1f}) in elevated rate environment ({fed:.1f}%) — rate-sensitive")
        score_parts.append(4)
    elif beta and beta > 0:
        score_parts.append(6)

    score = _avg(score_parts) if score_parts else 5.0
    return CategoryResult(score=score, light=_light(score), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


def _score_catalysts(data: dict, arch: str) -> CategoryResult:
    score_parts = []
    bull: list[str] = []
    bear: list[str] = []

    # Upcoming earnings proximity
    ed = data.get("earnings_date")
    if ed:
        from datetime import datetime, timedelta
        try:
            days_to = (datetime.strptime(ed, "%Y-%m-%d") - datetime.now()).days
            if 0 < days_to <= 14:
                score_parts.append(8)
                bull.append(f"Earnings in {days_to} days — catalyst window open")
            elif 0 < days_to <= 45:
                score_parts.append(6)
                bull.append(f"Earnings in {days_to} days — position ahead of catalyst")
            elif days_to > 90:
                score_parts.append(4)
        except Exception:
            pass

    # Lock-up expiry is not easily available — placeholder
    score = _avg(score_parts) if score_parts else 5.0
    return CategoryResult(score=score, light=_light(score), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


def _score_risk(data: dict, thesis: dict, arch: str) -> CategoryResult:
    manual = thesis.get("risk")
    score_parts = []
    bull: list[str] = []
    bear: list[str] = []

    # Cash runway for pre-profit
    if arch in ("spec", "micro"):
        cash = data.get("total_cash") or 0
        fcf = data.get("free_cashflow") or 0
        if cash > 0 and fcf < 0:
            quarters = cash / (-fcf / 4) if fcf != 0 else 99
            s = _bracket(quarters, [(8, 9), (4, 6), (2, 3)], 2)
            score_parts.append(s)
            if quarters > 8:
                bull.append(f"~{quarters:.0f} quarters of cash runway — existential risk low near-term")
            elif quarters < 3:
                bear.append(f"Only ~{quarters:.0f} quarters of runway — dilution risk imminent")
        elif cash > 0:
            score_parts.append(7)
            bull.append("Positive FCF — self-sustaining, no dilution risk")

    # Debt risk for larger cos
    de = data.get("debt_to_equity")
    if de and de > 300:
        score_parts.append(2)
        bear.append(f"D/E {de:.0f}% — high leverage, refinancing risk in current rate environment")
    elif de and de < 50:
        score_parts.append(8)

    computed = _avg(score_parts) if score_parts else None
    # Risk score: higher = LESS risk (for consistency with other categories)
    final = _blend(manual, computed, manual_weight=0.6)
    return CategoryResult(score=final, light=_light(final), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


def _score_institutional(data: dict, arch: str) -> CategoryResult:
    score_parts = []
    bull: list[str] = []
    bear: list[str] = []

    inst_pct = data.get("inst_ownership_pct")
    if inst_pct is not None:
        pct = inst_pct * 100
        if arch == "micro":
            # Any meaningful institutional ownership of a micro-cap is bullish
            if pct > 20:
                score_parts.append(9)
                bull.append(f"Institutional ownership {pct:.0f}% for a micro-cap — significant validation")
            elif pct > 5:
                score_parts.append(6)
            else:
                score_parts.append(3)
        else:
            # For larger caps: 50-80% is healthy
            if 50 <= pct <= 85:
                score_parts.append(8)
                bull.append(f"Institutional ownership {pct:.0f}% — well-supported demand floor")
            elif pct > 90:
                score_parts.append(5)
                bear.append(f"Institutional ownership {pct:.0f}% — potential crowding risk")
            elif pct < 30:
                score_parts.append(4)
                bear.append(f"Low institutional ownership {pct:.0f}% — undiscovered or avoided")
            else:
                score_parts.append(6)

    # Short change as proxy for institutional direction
    ss = data.get("shares_short")
    sp = data.get("shares_short_prior")
    if ss and sp and sp > 0:
        change = (ss - sp) / sp
        if change < -0.15:
            score_parts.append(8)
            bull.append(f"Short interest declining {_pct(-change)} MoM — shorts covering")
        elif change > 0.20:
            score_parts.append(4)
            bear.append(f"Short interest rising {_pct(change)} MoM — growing bearish bets")

    # Top holders count as breadth proxy
    holders = data.get("inst_holders", [])
    if len(holders) >= 8:
        score_parts.append(7)
        bull.append(f"{len(holders)}+ institutional holders — broad institutional ownership base")
    elif len(holders) <= 3 and arch not in ("micro",):
        score_parts.append(4)

    # Finnhub insider trading sentiment (supplements yfinance insider_txns)
    if data.get("fh_insider_bullish"):
        net = data.get("fh_insider_net_shares_90d", 0)
        score_parts.append(8.5)
        bull.append(f"Insider net buying {net:+,} shares (90d) — executives adding exposure")
    elif data.get("fh_insider_bearish"):
        net = data.get("fh_insider_net_shares_90d", 0)
        score_parts.append(3.0)
        bear.append(f"Insider net selling {net:+,} shares (90d) — executive distribution")

    score = _avg(score_parts) if score_parts else 5.0
    return CategoryResult(score=score, light=_light(score), factors=score_parts,
                          flags_bull=bull, flags_bear=bear)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _light(score: float) -> str:
    if score >= 7.0:
        return "green"
    if score >= 4.5:
        return "yellow"
    return "red"


def _avg(parts: list[float]) -> float:
    return round(sum(parts) / len(parts), 1) if parts else 5.0


def _blend(manual, computed, manual_weight: float = 0.6) -> float:
    if manual is not None and computed is not None:
        return round(manual_weight * float(manual) + (1 - manual_weight) * computed, 1)
    if manual is not None:
        return float(manual)
    if computed is not None:
        return computed
    return 5.0


def _bracket(value, thresholds: list[tuple], default: float, reverse: bool = False) -> float:
    """
    Map a value to a score using ordered thresholds.
    thresholds: list of (threshold, score) in descending order of threshold.
    reverse=True: lower value = higher score (e.g. P/E ratio).
    """
    for threshold, score in thresholds:
        if reverse:
            if value <= threshold:
                return float(score)
        else:
            if value >= threshold:
                return float(score)
    return float(default)


def _pct(val) -> str | None:
    if val is None:
        return None
    return f"{val * 100:.1f}%"
