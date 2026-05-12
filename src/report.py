"""
report.py — Detailed HTML report generator for GitLab Pages.

Generates a self-contained public/index.html with:
  • Portfolio summary header
  • Per-ticker scorecard: category breakdown, traffic lights,
    bull/bear flags, thesis, key metrics table, technical factors
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


def generate_report(all_results: list[dict], run_date: str, radar_results: list[dict] | None = None) -> str:
    """
    all_results: list of dicts, each with keys:
      ticker, name, archetype, strategy, data, score_result, signal_result, thesis
    radar_results: optional list of peer tickers to show in a separate radar section.
    Returns the HTML string and writes it to public/index.html.
    """
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

    # Radar section HTML
    radar_html = _radar_section(radar_results) if radar_results else ""

    cards_html = "\n".join(_ticker_card(r) for r in sorted_results)

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

<footer>Scores update on archetype-weighted framework. Thesis scores refresh monthly. Technical signals daily.<br>
Data: yfinance · NewsAPI · FRED · SEC EDGAR — all free tier.</footer>
</body>
</html>"""

    out = Path("public/index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return html


def _ticker_card(r: dict) -> str:
    sr  = r["score_result"]
    sig = r["signal_result"]
    data = r["data"]
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

    metrics = {
        "Price":         f"${price:.2f}" if price else "—",
        "1D Change":     f'<span style="color:{change_color}">{change_str}</span>',
        "RSI":           str(data.get("rsi_14") or "—"),
        "Short Float":   f'{data["short_float_pct"]*100:.1f}%' if data.get("short_float_pct") else "—",
        "Inst. Own":     f'{data["inst_ownership_pct"]*100:.0f}%' if data.get("inst_ownership_pct") else "—",
        "Fwd P/E":       f'{data["pe_forward"]:.1f}x' if data.get("pe_forward") and data["pe_forward"] > 0 else "—",
        "Rev Growth":    f'{data["revenue_growth_yoy"]*100:.1f}%' if data.get("revenue_growth_yoy") else "—",
        "Analyst Target":f'${data["analyst_target"]:.2f}' if data.get("analyst_target") else "—",
        "Earnings":      data.get("earnings_date", "—") or "—",
    }
    metrics_html = "".join(
        f'<div class="metric"><div class="metric-label">{k}</div><div class="metric-value">{v}</div></div>'
        for k, v in metrics.items()
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
