"""
report.py — Detailed HTML report generator for GitHub Pages.

Enterprise-grade self-contained public/index.html with:
  • Signal accuracy scorecard (60-day hit rate vs SPY)
  • Macro environment panel (FRED yield curve + rates)
  • Portfolio summary header with conviction distribution
  • Per-ticker scorecard: category breakdown, traffic lights,
    bull/bear flags, thesis, key metrics, 12-week score sparkline
  • Radar peers sidebar
  • Sorted by weighted conviction score descending
"""

from pathlib import Path
from datetime import datetime


LIGHT_CSS = {
    "green":  ("background:#d4edda;color:#155724;border-color:#c3e6cb", "●"),
    "yellow": ("background:#fff3cd;color:#856404;border-color:#ffeeba", "●"),
    "red":    ("background:#f8d7da;color:#721c24;border-color:#f5c6cb", "●"),
    "gray":   ("background:#e2e3e5;color:#6c757d;border-color:#d6d8db", "○"),
}

SIGNAL_CSS = {
    "CONFLUENCE":    "background:#28a745;color:#fff",
    "SQUEEZE ON":    "background:#dc3545;color:#fff",
    "CONSOLIDATION": "background:#007bff;color:#fff",
    "RISK WATCH":    "background:#fd7e14;color:#fff",
}

ARCHETYPE_LABEL = {
    "mega":   "Mega-cap",
    "largeg": "Large growth",
    "smallg": "Small growth",
    "spec":   "Speculative",
    "micro":  "Micro-cap",
}

CAT_LABELS = {
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
    "institutional": "Institutional / 13-F",
}


def generate_report(
    all_results:     list[dict],
    run_date:        str,
    radar_results:   list[dict] | None = None,
    history_map:     dict | None       = None,    # ticker → list of history rows
    accuracy_report: dict | None       = None,    # from accuracy.py
) -> str:
    """
    all_results: list of dicts, each with keys:
      ticker, name, archetype, strategy, data, score_result, signal_result, thesis
    radar_results: optional list of peer tickers for the radar section.
    history_map:   {ticker: [{date, score, signal, ...}]} — for sparklines.
    accuracy_report: output of accuracy.compute_accuracy_report() — for scorecard.
    Returns the HTML string and writes it to public/index.html.
    """
    history_map     = history_map or {}
    accuracy_report = accuracy_report or {}
    sorted_results = sorted(all_results, key=lambda x: x["score_result"].weighted_score, reverse=True)

    # Summary stats
    total = len(sorted_results)
    green_results  = [r for r in sorted_results if r["score_result"].weighted_light == "green"]
    yellow_results = [r for r in sorted_results if r["score_result"].weighted_light == "yellow"]
    red_results    = [r for r in sorted_results if r["score_result"].weighted_light == "red"]
    green_count  = len(green_results)
    yellow_count = len(yellow_results)
    red_count    = len(red_results)
    flip_count   = sum(1 for r in sorted_results if r["signal_result"].flipped)
    avg_score    = round(sum(r["score_result"].weighted_score for r in sorted_results) / total, 1) if total else 0

    # Build clickable ticker lists for summary cards
    def _ticker_links(results):
        return " &nbsp;".join(
            f'<a href="#{r["ticker"]}" style="color:inherit;font-weight:600;text-decoration:none">{r["ticker"]}</a>'
            for r in results
        )

    green_links  = _ticker_links(green_results)
    red_links    = _ticker_links(red_results)

    # New enterprise sections
    radar_html    = _radar_section(radar_results) if radar_results else ""
    accuracy_html = _accuracy_section(accuracy_report)
    macro_html    = _macro_panel(all_results)

    cards_html = "\n".join(
        _ticker_card(r, history=history_map.get(r["ticker"], []))
        for r in sorted_results
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Watchlist Report — {run_date}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f8f9fa; color: #212529; margin: 0; padding: 0; }}
  .hdr {{ background: #1a1a2e; color: #fff; padding: 2rem; }}
  .hdr h1 {{ margin: 0 0 .25rem; font-size: 1.5rem; font-weight: 600; }}
  .hdr p  {{ margin: 0; opacity: .7; font-size: .85rem; }}
  .summary {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1.5rem 2rem; }}
  .stat {{ background: #fff; border: 1px solid #dee2e6; border-radius: 8px;
           padding: .75rem 1.25rem; min-width: 110px; text-align: center; }}
  .stat-n {{ font-size: 1.6rem; font-weight: 700; }}
  .stat-l {{ font-size: .75rem; color: #6c757d; margin-top: 2px; }}
  .stat details summary {{ cursor: pointer; list-style: none; }}
  .stat details summary::-webkit-details-marker {{ display: none; }}
  .stat-tickers {{ font-size: .7rem; margin-top: .4rem; line-height: 1.8; text-align: left; }}
  .legend {{ display: flex; gap: 1.25rem; flex-wrap: wrap; margin: 0 2rem 1rem;
             padding: .5rem 1rem; background: #fff; border: 1px solid #dee2e6;
             border-radius: 8px; font-size: .75rem; color: #495057; }}
  .legend span {{ white-space: nowrap; }}
  .trend-up   {{ font-size: .75rem; font-weight: 700; color: #28a745; }}
  .trend-down {{ font-size: .75rem; font-weight: 700; color: #dc3545; }}
  .trend-flat {{ font-size: .75rem; color: #6c757d; }}
  .trend-new  {{ font-size: .7rem; color: #adb5bd; }}
  .radar-section {{ margin: 0 2rem 3rem; }}
  .radar-hdr {{ font-size: 1rem; font-weight: 700; color: #1a1a2e; margin-bottom: .25rem; }}
  .radar-sub {{ font-size: .8rem; color: #6c757d; margin-bottom: 1rem; }}
  .radar-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px,1fr)); gap: .75rem; }}
  .radar-card {{ background: #fff; border: 1px solid #dee2e6; border-radius: 10px;
                 padding: .75rem 1rem; }}
  .radar-ticker {{ font-weight: 700; font-size: .95rem; }}
  .radar-name   {{ font-size: .75rem; color: #6c757d; }}
  .radar-score  {{ font-size: .85rem; font-weight: 600; margin-top: .35rem; }}
  .radar-reason {{ font-size: .75rem; color: #495057; margin-top: .3rem; line-height: 1.4; }}
  .cards  {{ padding: 0 2rem 3rem; display: flex; flex-direction: column; gap: 1.5rem; }}
  .card   {{ background: #fff; border: 1px solid #dee2e6; border-radius: 12px; overflow: hidden; }}
  .card-hdr {{ display: flex; align-items: center; flex-wrap: wrap; gap: .75rem;
               padding: 1rem 1.25rem; border-bottom: 1px solid #f0f0f0; }}
  .ticker-name {{ font-size: 1.1rem; font-weight: 700; }}
  .ticker-sub  {{ font-size: .8rem; color: #6c757d; }}
  .score-badge {{ font-size: 1rem; font-weight: 700; padding: .3rem .8rem;
                  border-radius: 20px; border: 2px solid; }}
  .signal-badge {{ font-size: .75rem; font-weight: 600; padding: .3rem .75rem;
                   border-radius: 6px; white-space: nowrap; }}
  .flip-badge {{ background: #fff3cd; color: #856404; border: 1px solid #ffc107;
                 font-size: .7rem; padding: .2rem .6rem; border-radius: 4px; }}
  .thesis {{ margin: .75rem 1.25rem; padding: .75rem 1rem;
             background: #f8f9fa; border-left: 3px solid #6c757d;
             border-radius: 0 6px 6px 0; font-size: .875rem; line-height: 1.5; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;
              padding: 1rem 1.25rem; }}
  @media (max-width: 640px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
  .section-title {{ font-size: .7rem; font-weight: 600; text-transform: uppercase;
                    letter-spacing: .07em; color: #6c757d; margin-bottom: .6rem; }}
  .cat-grid {{ display: flex; flex-direction: column; gap: 5px; }}
  .cat-row  {{ display: flex; align-items: center; gap: 8px; font-size: .8rem; }}
  .cat-dot  {{ font-size: .9rem; flex-shrink: 0; }}
  .cat-name {{ flex: 1; color: #495057; }}
  .cat-score {{ font-weight: 600; min-width: 28px; text-align: right; }}
  .cat-bar-wrap {{ flex: 1; height: 6px; background: #e9ecef; border-radius: 3px; overflow: hidden; }}
  .cat-bar {{ height: 100%; border-radius: 3px; }}
  .flags {{ display: flex; flex-direction: column; gap: 5px; }}
  .flag {{ font-size: .8rem; padding: .3rem .6rem; border-radius: 5px; line-height: 1.4; }}
  .flag-bull {{ background: #d4edda; color: #155724; }}
  .flag-bear {{ background: #f8d7da; color: #721c24; }}
  .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
                   gap: 8px; padding: 0 1.25rem 1rem; }}
  .metric {{ background: #f8f9fa; border-radius: 6px; padding: .5rem .75rem; }}
  .metric-label {{ font-size: .7rem; color: #6c757d; margin-bottom: 2px; }}
  .metric-value {{ font-size: .9rem; font-weight: 600; }}
  .divergence {{ margin: 0 1.25rem .75rem; padding: .6rem .9rem;
                 background: #fff3cd; border: 1px solid #ffc107;
                 border-radius: 6px; font-size: .8rem; line-height: 1.5; }}
  .updated {{ font-size: .65rem; color: #adb5bd; margin-top: 3px; }}
  footer {{ text-align: center; padding: 1.5rem; font-size: .75rem; color: #adb5bd; }}
  /* Accuracy scorecard */
  .accuracy-section {{ margin: 0 2rem 1.5rem; }}
  .accuracy-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px,1fr)); gap: .75rem; }}
  .acc-card {{ background: #fff; border: 1px solid #dee2e6; border-radius: 10px; padding: 1rem 1.25rem; }}
  .acc-title {{ font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: #6c757d; margin-bottom: .5rem; }}
  .acc-rate  {{ font-size: 2rem; font-weight: 800; line-height: 1; }}
  .acc-sub   {{ font-size: .75rem; color: #6c757d; margin-top: .25rem; }}
  .acc-note  {{ font-size: .7rem; color: #adb5bd; margin-top: .35rem; font-style: italic; }}
  .acc-table {{ width: 100%; border-collapse: collapse; font-size: .75rem; margin-top: .5rem; }}
  .acc-table th {{ color: #6c757d; font-weight: 600; text-align: left; padding: 3px 6px; border-bottom: 1px solid #dee2e6; }}
  .acc-table td {{ padding: 3px 6px; }}
  .acc-table tr:nth-child(even) {{ background: #f8f9fa; }}
  /* Macro panel */
  .macro-panel {{ margin: 0 2rem 1.5rem; background: #fff; border: 1px solid #dee2e6;
                  border-radius: 10px; padding: .75rem 1.25rem; }}
  .macro-title {{ font-size: .7rem; font-weight: 700; text-transform: uppercase;
                  letter-spacing: .06em; color: #6c757d; margin-bottom: .6rem; }}
  .macro-grid  {{ display: flex; gap: 1.5rem; flex-wrap: wrap; align-items: center; }}
  .macro-item  {{ text-align: center; }}
  .macro-val   {{ font-size: 1.1rem; font-weight: 700; }}
  .macro-lbl   {{ font-size: .65rem; color: #6c757d; margin-top: 1px; }}
  .macro-regime{{ font-size: .75rem; font-weight: 600; padding: .2rem .7rem;
                  border-radius: 12px; margin-left: auto; }}
  /* Sparkline */
  .sparkline-wrap {{ padding: 0 1.25rem .75rem; }}
  .sparkline-label {{ font-size: .65rem; color: #6c757d; margin-bottom: 3px; }}
</style>
</head>
<body>

<div class="hdr">
  <h1>Watchlist Intelligence Report</h1>
  <p>Generated {run_date} · {total} tickers · Not financial advice</p>
</div>

<div class="summary">
  <div class="stat"><div class="stat-n">{total}</div><div class="stat-l">Tickers</div></div>
  <div class="stat">
    <details>
      <summary>
        <div class="stat-n" style="color:#155724">{green_count}</div>
        <div class="stat-l">High conviction ▾</div>
      </summary>
      <div class="stat-tickers" style="color:#155724">{green_links}</div>
    </details>
  </div>
  <div class="stat"><div class="stat-n" style="color:#856404">{yellow_count}</div><div class="stat-l">Watch</div></div>
  <div class="stat">
    <details>
      <summary>
        <div class="stat-n" style="color:#721c24">{red_count}</div>
        <div class="stat-l">Caution ▾</div>
      </summary>
      <div class="stat-tickers" style="color:#721c24">{red_links}</div>
    </details>
  </div>
  <div class="stat"><div class="stat-n">{avg_score}</div><div class="stat-l">Avg score</div></div>
  <div class="stat"><div class="stat-n" style="color:#856404">{flip_count}</div><div class="stat-l">Signal flips</div></div>
</div>

{accuracy_html}

{macro_html}

<div class="legend">
  <span>🟢 <strong>≥ 7.0</strong> High conviction</span>
  <span>🟡 <strong>5.0 – 6.9</strong> Watch</span>
  <span>🔴 <strong>&lt; 5.0</strong> Caution</span>
  <span style="color:#adb5bd">|</span>
  <span>↑ Score improved vs last run &nbsp; ↓ Declined &nbsp; → Flat</span>
</div>

<div class="cards">
{cards_html}
</div>

{radar_html}

<footer>Scores update on archetype-weighted framework. Thesis scores refresh monthly. Technical signals weekly.<br>
Data: yfinance · SEC EDGAR · FRED · NewsAPI · Finnhub — all free tier &nbsp;|&nbsp; Accuracy: 60-day signal hit rate vs SPY+2%<br>
Activate Finnhub / Telegram by adding secrets to GitHub Actions.</footer>
</body>
</html>"""

    out = Path("public/index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return html


def _ticker_card(r: dict, history: list[dict] | None = None) -> str:
    sr     = r["score_result"]
    sig    = r["signal_result"]
    data   = r["data"]
    thesis = r["thesis"]

    light_style, dot = LIGHT_CSS.get(sr.weighted_light, LIGHT_CSS["gray"])
    score_color = {"green": "#155724", "yellow": "#856404", "red": "#721c24", "gray": "#6c757d"}[sr.weighted_light]
    score_border = {"green": "#28a745", "yellow": "#ffc107", "red": "#dc3545", "gray": "#6c757d"}[sr.weighted_light]

    # Score trend arrow
    trend = sig.score_trend
    delta = sig.score_delta
    if trend == "up":
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        trend_html = f'<span class="trend-up">↑ {delta_str}</span>'
    elif trend == "down":
        trend_html = f'<span class="trend-down">↓ {delta}</span>'
    elif trend == "flat":
        trend_html = f'<span class="trend-flat">→</span>'
    else:
        trend_html = f'<span class="trend-new">new</span>'

    # Signal badge
    sig_style = SIGNAL_CSS.get(sig.signal, "background:#6c757d;color:#fff")
    flip_html = ""
    if sig.flipped:
        arrow = "↑" if sig.flip_direction == "upgraded" else "↓"
        flip_html = f'<span class="flip-badge">{arrow} Flipped from {sig.previous}</span>'

    # Category rows
    cat_rows = ""
    for cat_id, cat_label in CAT_LABELS.items():
        cat_result = sr.categories.get(cat_id)
        if not cat_result:
            continue
        _, dot_ch = LIGHT_CSS.get(cat_result.light, LIGHT_CSS["gray"])
        dot_color = {"green": "#28a745", "yellow": "#856404", "red": "#dc3545", "gray": "#adb5bd"}[cat_result.light]
        bar_color = dot_color
        bar_w = int(cat_result.score * 10)
        cat_rows += f"""
    <div class="cat-row">
      <span class="cat-dot" style="color:{dot_color}">{dot_ch}</span>
      <span class="cat-name">{cat_label}</span>
      <div class="cat-bar-wrap"><div class="cat-bar" style="width:{bar_w}%;background:{bar_color}"></div></div>
      <span class="cat-score" style="color:{dot_color}">{cat_result.score:.1f}</span>
    </div>"""

    # Bull / bear flags
    bull_html = "".join(f'<div class="flag flag-bull">▲ {f}</div>' for f in sr.bull_flags) or '<div class="flag flag-bull" style="opacity:.5">No bull flags detected</div>'
    bear_html = "".join(f'<div class="flag flag-bear">▼ {f}</div>' for f in sr.bear_flags) or '<div class="flag flag-bear" style="opacity:.5">No bear flags detected</div>'

    # Key metrics
    price = data.get("price")
    change = data.get("price_change_1d_pct")
    change_str = (f'+{change*100:.2f}%' if change and change > 0 else f'{change*100:.2f}%') if change else "—"
    change_color = "#28a745" if change and change > 0 else "#dc3545"

    # EDGAR-sourced values (shown when yfinance was None)
    gm_raw  = data.get("gross_margin") or data.get("edgar_gross_margin")
    fcf_raw = data.get("free_cashflow") or data.get("edgar_fcf")
    fcf_str = f'${fcf_raw/1e6:.0f}M' if fcf_raw else "—"
    gm_str  = f'{gm_raw*100:.1f}%' if gm_raw else "—"
    edgar_tag = " <sup style='font-size:.55rem;color:#adb5bd'>EDGAR</sup>" if (
        data.get("edgar_gross_margin") and not data.get("gross_margin")
        or data.get("edgar_fcf") and not data.get("free_cashflow")
    ) else ""

    arch = sr.archetype

    # FCF margin (FCF ÷ revenue) — operational quality lens
    rev_raw = data.get("total_revenue")
    fcf_margin_str = "—"
    if fcf_raw and rev_raw and rev_raw > 0:
        fcf_margin_pct = fcf_raw / rev_raw * 100
        fcf_margin_str = f'{fcf_margin_pct:.1f}%'

    # Operating margin
    op_margin = data.get("operating_margin")
    op_margin_str = f'{op_margin*100:.1f}%' if op_margin is not None else "—"

    # D/E ratio
    de = data.get("debt_to_equity")
    de_str = f'{de:.0f}%' if de is not None else "—"

    # Analyst upside
    analyst_upside = data.get("analyst_upside")

    # ── Metric color rules (archetype-aware) ─────────────────────────────────
    # Each tuple: (value, label_override_or_None, color_hint)
    # color_hint: "green" | "yellow" | "red" | None
    def _color(hint):
        return {
            "green":  "border-bottom:2px solid #28a745",
            "yellow": "border-bottom:2px solid #ffc107",
            "red":    "border-bottom:2px solid #dc3545",
        }.get(hint, "")

    def _rg_hint(rg):      # revenue growth — context-sensitive
        if rg is None: return None
        if arch in ("spec", "micro"):
            return "green" if rg > 0.30 else "yellow" if rg > 0.10 else "red"
        if arch == "smallg":
            return "green" if rg > 0.20 else "yellow" if rg > 0.08 else "red"
        return "green" if rg > 0.12 else "yellow" if rg > 0.04 else "red"  # mega/largeg

    def _pe_hint(pe):
        if pe is None or pe <= 0: return None
        return "green" if pe < 18 else "yellow" if pe < 35 else "red"

    def _rsi_hint(rsi):
        if rsi is None: return None
        if rsi < 30: return "yellow"   # oversold — not bad, but watch
        if rsi > 80: return "red"
        if rsi > 70: return "yellow"
        return "green"

    def _short_hint(sf):
        if sf is None: return None
        return "green" if sf < 0.05 else "yellow" if sf < 0.15 else "red"

    def _inst_hint(io):
        if io is None: return None
        return "green" if io > 0.70 else "yellow" if io > 0.40 else "red"

    def _gm_hint(gm):
        if gm is None: return None
        return "green" if gm > 0.60 else "yellow" if gm > 0.35 else "red"

    def _fcf_hint(fcf):
        if fcf is None: return None
        return "green" if fcf > 0 else "red"

    def _fcfm_hint(fcf, rev):
        if fcf is None or rev is None or rev == 0: return None
        m = fcf / rev
        return "green" if m > 0.12 else "yellow" if m > 0.03 else "red"

    def _opm_hint(opm):
        if opm is None: return None
        return "green" if opm > 0.20 else "yellow" if opm > 0.05 else "red"

    def _de_hint(de):
        if de is None: return None
        return "green" if de < 50 else "yellow" if de < 150 else "red"

    def _upside_hint(u):
        if u is None: return None
        return "green" if u > 0.20 else "yellow" if u > 0.05 else "red"

    rg_raw = data.get("revenue_growth_yoy")
    rsi_raw = data.get("rsi_14")
    sf_raw  = data.get("short_float_pct")
    io_raw  = data.get("inst_ownership_pct")
    pe_raw  = data.get("pe_forward") if data.get("pe_forward") and data["pe_forward"] > 0 else None

    # (display_value, hint_fn_result, tooltip)
    metrics_raw = [
        ("Price",         f"${price:.2f}" if price else "—",
         None, "Current market price"),
        ("1D Change",     f'<span style="color:{change_color}">{change_str}</span>',
         "green" if change and change > 0 else ("red" if change and change < -0.02 else None),
         "Price change vs prior close"),
        ("RSI",           str(rsi_raw or "—"),
         _rsi_hint(rsi_raw),
         "RSI 14 · <30 oversold, >70 overbought, >80 extended"),
        ("Short Float",   f'{sf_raw*100:.1f}%' if sf_raw else "—",
         _short_hint(sf_raw),
         "% of float sold short · <5% clean, >15% elevated"),
        ("Inst. Own",     f'{io_raw*100:.0f}%' if io_raw else "—",
         _inst_hint(io_raw),
         "Institutional ownership · >70% strong backing"),
        ("Fwd P/E",       f'{pe_raw:.1f}x' if pe_raw else "—",
         _pe_hint(pe_raw),
         "Forward P/E · <18 value, >35 expensive — compare to sector"),
        ("Rev Growth",    f'{rg_raw*100:.1f}%' if rg_raw else "—",
         _rg_hint(rg_raw),
         f"Revenue growth YoY · threshold varies by archetype ({arch})"),
        (f"Gross Margin{edgar_tag}", gm_str,
         _gm_hint(gm_raw),
         "Gross margin · >60% excellent, <35% thin — depends on business model"),
        ("Op Margin",     op_margin_str,
         _opm_hint(op_margin),
         "Operating margin · >20% strong, <5% early-stage"),
        ("FCF Margin",    fcf_margin_str,
         _fcfm_hint(fcf_raw, rev_raw),
         "FCF ÷ Revenue · >12% high-quality compounder, <0% burning cash"),
        (f"FCF{edgar_tag}", fcf_str,
         _fcf_hint(fcf_raw),
         "Free cash flow (absolute) · positive = self-funding"),
        ("D/E",           de_str,
         _de_hint(de),
         "Debt-to-equity · <50% clean, >150% elevated leverage"),
        ("Analyst Target",f'${data["analyst_target"]:.2f}' if data.get("analyst_target") else "—",
         _upside_hint(analyst_upside),
         f'Consensus price target · {f"+{analyst_upside*100:.0f}% upside" if analyst_upside else ""}'),
        ("Earnings",      data.get("earnings_date", "—") or "—",
         None, "Next earnings date"),
    ]

    metrics_html = "".join(
        f'<div class="metric" style="{_color(hint)}" title="{tip}">'
        f'<div class="metric-label">{k}</div>'
        f'<div class="metric-value">{v}</div>'
        f'</div>'
        for k, v, hint, tip in metrics_raw
    )

    # Divergence block (only if flipped or thesis ≠ signal)
    divergence_html = ""
    if sig.flipped or sig.divergence:
        divergence_html = f'<div class="divergence"><strong>Insight:</strong> {sig.divergence}</div>'

    archetype_label = ARCHETYPE_LABEL.get(sr.archetype, sr.archetype)
    strategies = ", ".join(r.get("strategy", []))
    thesis_updated = r.get("thesis_config", {}).get("last_updated", "")

    return f"""<div class="card" id="{r['ticker']}" style="scroll-margin-top:1rem">
  <div class="card-hdr">
    <div>
      <div class="ticker-name">{r['ticker']} &nbsp;<span style="font-weight:400;font-size:.9rem;color:#6c757d">{r.get('name','')}</span></div>
      <div class="ticker-sub">{archetype_label} · {data.get('sector','—')} · {strategies}</div>
    </div>
    <div style="display:flex;align-items:center;gap:.4rem">
      <div class="score-badge" style="color:{score_color};border-color:{score_border}">
        {dot} {sr.weighted_score}/10
      </div>
      {trend_html}
    </div>
    <span class="signal-badge" style="{sig_style}">{sig.signal}</span>
    {flip_html}
    <div style="flex:1"></div>
    <div style="text-align:right">
      <div style="font-size:.7rem;color:#adb5bd">Thesis updated</div>
      <div style="font-size:.75rem;font-weight:600">{thesis_updated}</div>
    </div>
  </div>

  <div class="thesis"><strong>Thesis:</strong> {thesis}</div>

  {divergence_html}

  {_sparkline_html(history)}

  <div class="metrics-grid">{metrics_html}</div>

  <div class="two-col">
    <div>
      <div class="section-title">Category breakdown</div>
      <div class="cat-grid">{cat_rows}</div>
    </div>
    <div>
      <div class="section-title">Top bull flags</div>
      <div class="flags" style="margin-bottom:.75rem">{bull_html}</div>
      <div class="section-title">Top bear flags</div>
      <div class="flags">{bear_html}</div>
    </div>
  </div>
</div>"""


# ── Radar section ─────────────────────────────────────────────────────────────

def _radar_section(radar_results: list[dict]) -> str:
    """
    Renders a compact radar grid of peer/sector tickers not in the owned portfolio.
    Each card shows ticker, name, score+light, signal, and reason for watching.
    """
    if not radar_results:
        return ""

    cards = []
    for r in sorted(radar_results, key=lambda x: x["score_result"].weighted_score, reverse=True):
        sr   = r["score_result"]
        sig  = r["signal_result"]
        data = r["data"]

        light_color = {"green": "#155724", "yellow": "#856404", "red": "#721c24", "gray": "#6c757d"}[sr.weighted_light]
        light_bg    = {"green": "#d4edda", "yellow": "#fff3cd", "red": "#f8d7da", "gray": "#e2e3e5"}[sr.weighted_light]
        dot         = {"green": "●", "yellow": "●", "red": "●", "gray": "○"}[sr.weighted_light]
        sig_bg, sig_txt = {
            "CONFLUENCE":    ("#28a745", "#fff"),
            "SQUEEZE ON":    ("#dc3545", "#fff"),
            "CONSOLIDATION": ("#007bff", "#fff"),
            "RISK WATCH":    ("#fd7e14", "#fff"),
        }.get(sig.signal, ("#6c757d", "#fff"))

        price = data.get("price")
        price_str = f"${price:.2f}" if price else "—"
        reason = r.get("reason", r.get("data", {}).get("sector", "Sector peer"))

        cards.append(f"""
  <div class="radar-card">
    <div style="display:flex;justify-content:space-between;align-items:flex-start">
      <div>
        <div class="radar-ticker">{r['ticker']}</div>
        <div class="radar-name">{r.get('name', data.get('name', ''))}</div>
      </div>
      <div style="text-align:right">
        <span style="background:{light_bg};color:{light_color};border-radius:10px;padding:2px 8px;font-size:.75rem;font-weight:600">{dot} {sr.weighted_score}/10</span>
      </div>
    </div>
    <div style="margin-top:.35rem;display:flex;gap:.4rem;align-items:center">
      <span style="background:{sig_bg};color:{sig_txt};border-radius:4px;padding:2px 7px;font-size:.7rem;font-weight:600">{sig.signal}</span>
      <span style="font-size:.75rem;color:#6c757d">{price_str}</span>
    </div>
    <div class="radar-reason">{reason}</div>
  </div>""")

    cards_html = "\n".join(cards)
    return f"""
<div class="radar-section">
  <div class="radar-hdr">📡 Sector Radar — Stocks to Watch</div>
  <div class="radar-sub">Peer tickers outside your portfolio, scored on the same framework. Not owned — included for context and potential opportunity spotting.</div>
  <div class="radar-grid">
{cards_html}
  </div>
</div>"""


# ── Accuracy scorecard section ────────────────────────────────────────────────

def _accuracy_section(accuracy_report: dict) -> str:
    """
    Renders the 60-day signal accuracy scorecard.
    Shows buy/avoid hit rates, avg returns, and top performers table.
    Returns empty string if no data yet (< 8 weeks of history).
    """
    if not accuracy_report:
        return ""

    buy   = accuracy_report.get("buy_signals", {})
    avoid = accuracy_report.get("avoid_signals", {})
    spy   = accuracy_report.get("spy_return_pct")
    note  = accuracy_report.get("data_note", "")
    gen   = accuracy_report.get("generated_at", "")

    # If no signals to show yet, render a placeholder note
    buy_count   = buy.get("count", 0)
    avoid_count = avoid.get("count", 0)
    if buy_count == 0 and avoid_count == 0:
        return f"""
<div class="accuracy-section">
  <div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#6c757d;margin-bottom:.5rem">📊 Signal Accuracy — 60-Day Scorecard</div>
  <div style="background:#fff;border:1px solid #dee2e6;border-radius:10px;padding:1rem 1.25rem;font-size:.8rem;color:#6c757d">{note or 'Accuracy data will appear after 8+ weeks of weekly runs.'}</div>
</div>"""

    buy_rate  = buy.get("hit_rate_pct")
    buy_ret   = buy.get("avg_return_pct")
    buy_exc   = buy.get("avg_excess_pct")
    av_rate   = avoid.get("hit_rate_pct")
    av_ret    = avoid.get("avg_return_pct")

    def _rate_color(r):
        if r is None: return "#6c757d"
        if r >= 60:   return "#155724"
        if r >= 45:   return "#856404"
        return "#721c24"

    def _fmt(v, suffix=""):
        return f"{v}{suffix}" if v is not None else "—"

    buy_rate_html  = f'<span style="color:{_rate_color(buy_rate)}">{_fmt(buy_rate, "%")}</span>'
    avd_rate_html  = f'<span style="color:{_rate_color(av_rate)}">{_fmt(av_rate, "%")}</span>'

    # Top performers table (buy signals)
    top_rows = ""
    for row in (buy.get("top_performers") or [])[:8]:
        ret = row.get("ticker_return_pct", 0)
        exc = row.get("excess_return_pct", 0)
        correct = row.get("correct")
        badge = "✓" if correct else ("✗" if correct is False else "?")
        badge_color = "#155724" if correct else ("#721c24" if correct is False else "#6c757d")
        ret_color = "#155724" if ret >= 0 else "#dc3545"
        exc_color = "#155724" if exc >= 0 else "#dc3545"
        top_rows += f"""<tr>
          <td style="font-weight:600">{row.get('ticker','')}</td>
          <td>{row.get('date','')[:7]}</td>
          <td style="color:{ret_color}">{ret:+.1f}%</td>
          <td style="color:{exc_color}">{exc:+.1f}%</td>
          <td style="color:{badge_color}">{badge}</td>
        </tr>"""

    spy_str = f"{spy:+.1f}%" if spy is not None else "—"

    return f"""
<div class="accuracy-section">
  <div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#6c757d;margin-bottom:.5rem">📊 Signal Accuracy — 60-Day Scorecard &nbsp;<span style="font-weight:400;font-style:italic">as of {gen}</span></div>
  <div class="accuracy-grid">

    <div class="acc-card">
      <div class="acc-title">Buy Signal Hit Rate</div>
      <div class="acc-rate">{buy_rate_html}</div>
      <div class="acc-sub">of {buy_count} buy signals beat SPY +2%</div>
      <div class="acc-sub">Avg return: {_fmt(buy_ret, '%')} &nbsp;|&nbsp; Avg excess: {_fmt(buy_exc, '%')}</div>
      <div class="acc-sub">SPY 60d: {spy_str}</div>
    </div>

    <div class="acc-card">
      <div class="acc-title">Avoid Signal Hit Rate</div>
      <div class="acc-rate">{avd_rate_html}</div>
      <div class="acc-sub">of {avoid_count} avoid signals underperformed SPY</div>
      <div class="acc-sub">Avg return: {_fmt(av_ret, '%')}</div>
      <div class="acc-note">{note}</div>
    </div>

    <div class="acc-card" style="grid-column: span 2">
      <div class="acc-title">Top Buy Signal Performers (60d)</div>
      <table class="acc-table">
        <thead><tr><th>Ticker</th><th>Signal Date</th><th>Return</th><th>vs SPY</th><th>✓</th></tr></thead>
        <tbody>{top_rows}</tbody>
      </table>
    </div>

  </div>
</div>"""


# ── Macro environment panel ───────────────────────────────────────────────────

def _macro_panel(all_results: list[dict]) -> str:
    """
    Renders compact FRED macro overlay panel.
    Extracts data from first result's data["macro"] dict.
    Returns empty string if no macro data available.
    """
    if not all_results:
        return ""

    macro = {}
    for r in all_results:
        m = r.get("data", {}).get("macro", {})
        if m:
            macro = m
            break

    if not macro:
        return ""

    ffr      = macro.get("fed_funds_rate")
    cpi      = macro.get("cpi_yoy")
    ten_yr   = macro.get("ten_yr_yield")
    two_yr   = macro.get("two_yr_yield")
    spread   = macro.get("yield_spread_10_2")
    unemp    = macro.get("unemployment")

    def _fmt_rate(v):
        return f"{v:.2f}%" if v is not None else "—"

    def _spread_color(s):
        if s is None:   return "#6c757d"
        if s < -0.2:    return "#dc3545"
        if s < 0.2:     return "#856404"
        return "#28a745"

    def _ffr_color(v):
        if v is None:   return "#6c757d"
        if v > 5.0:     return "#dc3545"
        if v > 3.5:     return "#856404"
        return "#28a745"

    spread_color = _spread_color(spread)
    ffr_color    = _ffr_color(ffr)

    # Macro regime badge
    if ffr is not None and spread is not None:
        if ffr > 4.5 and spread < -0.2:
            regime = ("⚠ Restrictive + Inverted Curve", "#f8d7da", "#721c24")
        elif ffr > 4.5:
            regime = ("Restrictive Rates", "#fff3cd", "#856404")
        elif spread < -0.2:
            regime = ("Inverted Yield Curve", "#fff3cd", "#856404")
        elif ffr < 3.0 and spread > 0.5:
            regime = ("✓ Accommodative", "#d4edda", "#155724")
        else:
            regime = ("Neutral", "#e2e3e5", "#495057")
    else:
        regime = ("Data unavailable", "#e2e3e5", "#6c757d")

    regime_label, regime_bg, regime_fg = regime

    items = [
        ("Fed Funds Rate",  _fmt_rate(ffr),    ffr_color),
        ("10-Yr Yield",     _fmt_rate(ten_yr),  "#212529"),
        ("2-Yr Yield",      _fmt_rate(two_yr),  "#212529"),
        ("10y−2y Spread",   _fmt_rate(spread),  spread_color),
        ("Unemployment",    _fmt_rate(unemp),   "#212529"),
    ]

    items_html = "".join(f"""
    <div class="macro-item">
      <div class="macro-val" style="color:{color}">{val}</div>
      <div class="macro-lbl">{label}</div>
    </div>""" for label, val, color in items)

    return f"""
<div class="macro-panel">
  <div class="macro-title">🏛 Macro Environment (FRED)</div>
  <div class="macro-grid">
    {items_html}
    <span class="macro-regime" style="background:{regime_bg};color:{regime_fg}">{regime_label}</span>
  </div>
</div>"""


# ── Sparkline SVG ─────────────────────────────────────────────────────────────

def _sparkline_html(history: list[dict] | None) -> str:
    """
    Renders a compact inline SVG polyline showing the last 12 weeks of scores.
    history: list of row dicts sorted oldest→newest (as returned by read_history).
    Returns empty string if fewer than 2 data points.
    """
    if not history or len(history) < 2:
        return ""

    rows   = history[-12:]   # cap at 12 weeks
    scores = []
    dates  = []
    for row in rows:
        try:
            scores.append(float(row["score"]))
            dates.append(str(row.get("date", ""))[:10])
        except (KeyError, ValueError, TypeError):
            continue

    if len(scores) < 2:
        return ""

    # SVG geometry
    W, H   = 260, 36
    pad_x  = 4
    pad_y  = 4
    min_s  = max(0.0, min(scores) - 0.5)
    max_s  = min(10.0, max(scores) + 0.5)
    rng    = max_s - min_s if max_s > min_s else 1.0
    n      = len(scores)

    def _px(i):
        return pad_x + (i / (n - 1)) * (W - 2 * pad_x)

    def _py(s):
        return pad_y + (1 - (s - min_s) / rng) * (H - 2 * pad_y)

    points = " ".join(f"{_px(i):.1f},{_py(s):.1f}" for i, s in enumerate(scores))

    # Colour the line by the last score
    last = scores[-1]
    line_color = "#28a745" if last >= 7.0 else ("#ffc107" if last >= 5.0 else "#dc3545")

    # Optional: shade fill
    close_path = (
        f"M{_px(0):.1f},{_py(scores[0]):.1f} "
        + " ".join(f"L{_px(i):.1f},{_py(s):.1f}" for i, s in enumerate(scores))
        + f" L{_px(n-1):.1f},{H-pad_y:.1f} L{_px(0):.1f},{H-pad_y:.1f} Z"
    )

    latest_dot_x = _px(n - 1)
    latest_dot_y = _py(last)

    first_date = dates[0] if dates else ""
    last_date  = dates[-1] if dates else ""

    return f"""
<div class="sparkline-wrap">
  <div class="sparkline-label">12-week score trend &nbsp;
    <span style="color:#adb5bd">{first_date} → {last_date}</span>
  </div>
  <svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" style="display:block;overflow:visible">
    <!-- Fill -->
    <path d="{close_path}" fill="{line_color}" fill-opacity="0.12"/>
    <!-- Line -->
    <polyline points="{points}" fill="none" stroke="{line_color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
    <!-- Latest dot -->
    <circle cx="{latest_dot_x:.1f}" cy="{latest_dot_y:.1f}" r="3" fill="{line_color}"/>
    <!-- Score labels at first and last -->
    <text x="{_px(0):.1f}" y="{H}" font-size="7" fill="#adb5bd" text-anchor="middle">{scores[0]:.1f}</text>
    <text x="{_px(n-1):.1f}" y="{H}" font-size="7" fill="{line_color}" text-anchor="middle" font-weight="bold">{last:.1f}</text>
  </svg>
</div>"""
