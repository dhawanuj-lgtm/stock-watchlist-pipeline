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

import math
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
  .cat-grid {{ display: flex; flex-direction: column; gap: 3px; }}
  .cat-details {{ border-radius: 6px; overflow: hidden; background: #1a1a2e; }}
  .cat-details summary {{ display: flex; align-items: center; gap: 8px; font-size: .8rem;
                          cursor: pointer; padding: 6px 8px; border-radius: 6px;
                          list-style: none; user-select: none; color: #c8c8e0; }}
  .cat-details summary::-webkit-details-marker {{ display: none; }}
  .cat-details summary:hover {{ background: rgba(255,255,255,.07); }}
  .cat-details[open] summary {{ background: rgba(255,255,255,.07); border-radius: 6px 6px 0 0; }}
  .cat-expand-icon {{ font-size: .55rem; color: #7070a0; flex-shrink: 0; width: 12px;
                      display: inline-block; transition: transform .15s; }}
  .cat-details[open] .cat-expand-icon {{ transform: rotate(90deg); }}
  .cat-body {{ background: #12122a; border-radius: 0 0 6px 6px;
               padding: .4rem .6rem .5rem 1.5rem; font-size: .75rem;
               border-top: 1px solid rgba(255,255,255,.08); }}
  .cat-factor {{ color: #8888aa; line-height: 1.5; padding: 1px 0; }}
  .cat-factor::before {{ content: "·"; margin-right: .3rem; color: #5050a0; }}
  .cat-flag-bull {{ color: #7ec89e; line-height: 1.5; padding: 1px 0; }}
  .cat-flag-bear {{ color: #f08888; line-height: 1.5; padding: 1px 0; }}
  .cat-flag-bull::before {{ content: "▲"; margin-right: .3rem; font-size: .65rem; }}
  .cat-flag-bear::before {{ content: "▼"; margin-right: .3rem; font-size: .65rem; }}
  .cat-dot  {{ font-size: .75rem; flex-shrink: 0; }}
  .cat-name {{ flex: 0 0 128px; color: #c0c0d8; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }}
  .cat-score {{ font-weight: 700; min-width: 28px; text-align: right; font-size: .85rem; }}
  .cat-bar-wrap {{ flex: 1; height: 5px; background: rgba(255,255,255,.1); border-radius: 3px; overflow: hidden; }}
  .cat-bar {{ height: 100%; border-radius: 3px; }}
  .flags {{ display: flex; flex-direction: column; gap: 5px; }}
  .flag {{ font-size: .8rem; padding: .3rem .6rem; border-radius: 5px; line-height: 1.4; }}
  .flag-bull {{ background: #d4edda; color: #155724; }}
  .flag-bear {{ background: #f8d7da; color: #721c24; }}
  /* Grok Logic panel */
  .card-body-grid {{ display: grid; grid-template-columns: 1fr 420px; gap: 0;
                     align-items: start; }}
  @media (max-width: 900px) {{ .card-body-grid {{ grid-template-columns: 1fr; }} }}
  .grok-panel {{ border-left: 2px solid #e9ecef; padding: 1rem 1.25rem;
                 background: #fafbfc; }}
  @media (max-width: 900px) {{ .grok-panel {{ border-left: none; border-top: 2px solid #e9ecef; }} }}
  .grok-hdr  {{ display: flex; align-items: center; justify-content: space-between;
                margin-bottom: .75rem; padding-bottom: .6rem;
                border-bottom: 1px solid #e9ecef; }}
  .grok-title{{ font-size: .65rem; font-weight: 700; text-transform: uppercase;
                letter-spacing: .08em; color: #6c757d; }}
  .grok-badge{{ font-size: .7rem; font-weight: 700; padding: .2rem .65rem;
                border-radius: 10px; color: #fff; white-space: nowrap; }}
  .grok-score{{ font-size: 1.5rem; font-weight: 800; line-height: 1; }}
  .grok-signal{{ font-size: .9rem; font-weight: 700; margin: .5rem 0 .75rem; }}
  .grok-breakdown {{ display: grid; grid-template-columns: 1fr 1fr; gap: 3px 12px;
                     font-size: .75rem; margin-bottom: .75rem; }}
  .grok-row  {{ display: flex; justify-content: space-between; align-items: center;
                padding: 2px 0; }}
  .grok-cat  {{ color: #6c757d; }}
  .grok-val  {{ font-weight: 700; min-width: 28px; text-align: right; }}
  .grok-wt   {{ font-size: .65rem; color: #adb5bd; margin-left: 3px; }}
  .grok-action {{ font-size: .78rem; line-height: 1.5; margin-bottom: .6rem;
                  padding: .5rem .7rem; border-radius: 6px; background: #f8f9fa; }}
  .grok-reason {{ font-size: .75rem; line-height: 1.5; padding: 2px 0; }}
  .grok-reason::before {{ margin-right: .35rem; font-size: .65rem; }}
  .grok-reason.bull::before {{ content: "▲"; color: #28a745; }}
  .grok-reason.bear::before {{ content: "▼"; color: #dc3545; }}
  .grok-footer {{ font-size: .72rem; color: #6c757d; padding-top: .6rem;
                  margin-top: .6rem; border-top: 1px solid #e9ecef; }}
  .grok-toggle {{ display: none; }}
  @media (max-width: 900px) {{
    .grok-toggle {{ display: block; background: none; border: 1px solid #dee2e6;
                    border-radius: 6px; padding: .3rem .8rem; font-size: .75rem;
                    color: #6c757d; cursor: pointer; margin: .5rem 1.25rem; }}
    .grok-collapsible {{ display: none; }}
    .grok-collapsible.open {{ display: block; }}
  }}
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
  /* Score mini-cards in header */
  .score-cards {{ display:flex; gap:.5rem; flex-shrink:0; align-items:flex-start; }}
  .score-card-claude {{ background:#d4edda; }}
  .score-card-grok   {{ background:#dbeafe; }}
  .scard-header {{ display:flex; align-items:baseline; gap:.35rem; margin-bottom:.1rem; }}
  .scard-num {{ font-size:1.25rem; font-weight:700; }}
  .scard-lbl {{ font-size:.7rem; font-weight:600; opacity:.65; }}
  .scard-signal {{ font-size:.78rem; font-weight:600; margin-bottom:.15rem; }}
  .scard-meta   {{ font-size:.68rem; opacity:.8; }}
  .scard-action {{ font-size:.68rem; margin-top:.25rem; font-style:italic;
                   opacity:.85; line-height:1.35; }}
  /* arch-tag (small chip in header sub-row) */
  .arch-tag {{ font-size:.72rem; padding:.2rem .65rem; border-radius:12px;
               border:1px solid #dee2e6; color:#6c757d; white-space:nowrap; }}
  /* Info panel — Thesis / Situation / Action / Insight */
  .info-panel {{ margin:.75rem 1.25rem .25rem; border:1px solid #e9ecef;
                 border-radius:8px; overflow:hidden; font-size:.875rem; }}
  .info-thesis {{ padding:.55rem .9rem; background:#f8f9fa;
                  border-bottom:1px solid #e9ecef; line-height:1.5; }}
  .info-sit-act {{ display:grid; grid-template-columns:1fr 1fr; }}
  @media (max-width:640px) {{ .info-sit-act {{ grid-template-columns:1fr; }} }}
  .info-sit {{ padding:.5rem .9rem; line-height:1.45; }}
  .info-act {{ padding:.5rem .9rem; line-height:1.45; border-left:1px solid #e9ecef; }}
  .info-insight {{ padding:.45rem .9rem; background:#fff3cd;
                   border-top:1px solid #e9ecef; line-height:1.45; }}
  .info-kw {{ font-size:.65rem; font-weight:700; text-transform:uppercase;
              letter-spacing:.06em; color:#6c757d; display:block; margin-bottom:3px; }}
  /* Right flags panel */
  .flags-panel {{ padding:.9rem 1.25rem; }}
  .flags-panel-hdr {{ font-size:.7rem; font-weight:600; text-transform:uppercase;
                      letter-spacing:.07em; color:#6c757d; margin-bottom:.6rem; }}
  /* Legacy (kept for compatibility) */
  .score-pills {{ display:flex; gap:.45rem; flex-shrink:0; align-items:center; }}
  .flags-strip {{ display:none; }}
  .grok-meta-top  {{ display:flex; gap:1.5rem; margin-bottom:.75rem;
                     padding-bottom:.6rem; border-bottom:1px solid #e9ecef; }}
  .grok-meta-item {{ display:flex; flex-direction:column; gap:1px; }}
  .grok-meta-lbl  {{ font-size:.62rem; font-weight:600; text-transform:uppercase;
                     letter-spacing:.06em; color:#adb5bd; }}
  .grok-meta-val  {{ font-size:.9rem; font-weight:700; }}
  /* Horizontal semantic metrics strip */
  .metrics-strip  {{ display:flex; flex-wrap:wrap; padding:.45rem 1.25rem .3rem;
                     border-bottom:1px solid #f0f0f0; gap:0; align-items:center; }}
  .ms-price-area  {{ display:inline-flex; align-items:center; gap:.5rem;
                     padding:.25rem .9rem .25rem 0; margin-right:.3rem;
                     border-right:2px solid #e9ecef; }}
  .ms-price       {{ font-size:1.05rem; font-weight:700; color:#212529; }}
  .ms-change      {{ font-size:.82rem; font-weight:600; }}
  .ms-item        {{ display:inline-flex; align-items:center; gap:.3rem;
                     padding:.25rem .8rem; border-right:1px solid #f0f0f0;
                     white-space:nowrap; }}
  .ms-item:last-child {{ border-right:none; }}
  .ms-key         {{ color:#adb5bd; font-size:.7rem; }}
  .ms-val         {{ font-weight:600; font-size:.82rem; }}
  .ms-tag         {{ font-size:.63rem; padding:.1rem .4rem; border-radius:8px;
                     font-weight:500; letter-spacing:.01em; }}
  .ms-tag-green   {{ background:#d4edda; color:#155724; }}
  .ms-tag-yellow  {{ background:#fff3cd; color:#856404; }}
  .ms-tag-red     {{ background:#f8d7da; color:#721c24; }}
  /* Score card polish */
  .score-card     {{ padding:.55rem .9rem; border-radius:10px; min-width:148px;
                     max-width:195px; position:relative; overflow:hidden; }}
  .score-card::before {{ content:''; position:absolute; top:0; left:0; right:0;
                          height:3px; border-radius:10px 10px 0 0; }}
  .score-card-claude::before {{ background:#28a745; }}
  .score-card-grok::before   {{ background:#3b82f6; }}
  /* ── Dark card body ──────────────────────────────────────────────── */
  .dark-body {{ background: #1a1a2e; }}
  /* Dark metrics strip */
  .dark-body .metrics-strip {{ background:#12122a; border-bottom:1px solid rgba(255,255,255,.07); }}
  .dark-body .ms-price      {{ color:#e0e0f0; }}
  .dark-body .ms-key        {{ color:#6060a0; }}
  .dark-body .ms-val        {{ color:#c8c8e0; }}
  .dark-body .ms-change     {{ /* kept inline */ }}
  .dark-body .ms-item       {{ border-right-color:rgba(255,255,255,.07); }}
  .dark-body .ms-price-area {{ border-right-color:rgba(255,255,255,.15); }}
  .dark-body .ms-tag-green  {{ background:rgba(40,167,69,.28); color:#7ec89e; }}
  .dark-body .ms-tag-yellow {{ background:rgba(255,193,7,.22); color:#ffc107; }}
  .dark-body .ms-tag-red    {{ background:rgba(220,53,69,.28); color:#f08888; }}
  /* Dark info panel */
  .dark-body .info-panel    {{ border-color:rgba(255,255,255,.1); background:#1a1a2e; }}
  .dark-body .info-thesis   {{ border-bottom-color:rgba(255,255,255,.08); color:#c8c8e0; }}
  .dark-body .info-sit-act  {{ /* grid, unchanged */ }}
  .dark-body .info-act      {{ border-left-color:rgba(255,255,255,.1); }}
  .dark-body .info-insight  {{ background:rgba(255,193,7,.12); color:#ffc107;
                                border-top-color:rgba(255,255,255,.08); }}
  .dark-body .info-kw       {{ color:#6060a0; }}
  /* Dark category & flags */
  .dark-body .section-title {{ color:#6060a0; }}
  .dark-body .flags-panel-hdr {{ color:#6060a0; }}
  .dark-body .flag-bull     {{ background:rgba(40,167,69,.18); color:#7ec89e;
                                border:1px solid rgba(40,167,69,.25); }}
  .dark-body .flag-bear     {{ background:rgba(220,53,69,.18); color:#f08888;
                                border:1px solid rgba(220,53,69,.25); }}
  /* Dark sparkline */
  .dark-body .sparkline-wrap  {{ padding:0 1.25rem 1rem; }}
  .dark-body .sparkline-label {{ color:#6060a0; }}
  /* Key signals right column in dark body */
  .dark-signals {{ border-left:1px solid rgba(255,255,255,.07); padding:.9rem 1.25rem;
                   display:flex; flex-direction:column; }}
  /* Dark card header */
  .card-hdr-dark {{ background: #0f0f24 !important; border-bottom:1px solid rgba(255,255,255,.07); }}
  .card-hdr-dark .ticker-name {{ color: #e0e0f0; }}
  .card-hdr-dark .ticker-sub  {{ color: #8080a8; }}
  .card-hdr-dark .arch-tag    {{ border-color:rgba(255,255,255,.12); color:#8080a8;
                                  background:rgba(255,255,255,.04); }}
  .card-hdr-dark .flip-badge  {{ background:rgba(255,193,7,.15); color:#ffc107;
                                  border-color:rgba(255,193,7,.3); }}
  .card-hdr-dark .score-card-claude {{ background:#1b3224; }}
  .card-hdr-dark .score-card-grok   {{ background:#192040; }}
  .card-hdr-dark .scard-lbl   {{ opacity:.5; }}
  .card-hdr-dark .scard-meta  {{ color:#8080a8 !important; }}
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
Activate Finnhub / Telegram by adding secrets to GitHub Actions.<br>
<span style="font-size:.65rem;color:#ced4da">Built {run_date} · pipeline v3</span></footer>
</body>
</html>"""

    out = Path("public/report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return html


def _compute_grok_logic(sr) -> dict:
    """
    Second-opinion scoring panel using Grok Logic weights.
    Derived entirely from existing CategoryResult objects — no external API.

    Weights: Fundamentals 30% · Valuation 20% · Technical 20%
             Moat+Catalysts+Management 10% · Macro+Risk 10% · Sentiment+Inst 10%
    """
    cats = sr.categories

    def _s(cat_id: str) -> float:
        cr = cats.get(cat_id)
        return cr.score if cr else 5.0

    f   = _s("fundamentals")
    v   = _s("valuation")
    t   = _s("technical")
    mct = round((_s("moat") + _s("catalysts") + _s("management")) / 3, 1)
    mr  = round((_s("macro")  + _s("risk"))          / 2, 1)
    si  = round((_s("sentiment") + _s("institutional")) / 2, 1)

    overall = round(f*0.30 + v*0.20 + t*0.20 + mct*0.10 + mr*0.10 + si*0.10, 1)

    # Signal + conviction
    if overall >= 7.5 and f >= 8.0:
        signal, sig_color = "BUY on Dip / Strong Hold", "#28a745"
        conv_label, conv_bg = "High Conviction", "#28a745"
    elif overall >= 7.5:
        signal, sig_color = "Strong Hold", "#28a745"
        conv_label, conv_bg = "High Conviction", "#28a745"
    elif overall >= 6.5:
        signal, sig_color = "Hold / Watch", "#856404"
        conv_label, conv_bg = "Watch / Hold", "#e0a800"
    elif overall >= 5.5:
        signal, sig_color = "Hold / Watch", "#856404"
        conv_label, conv_bg = "Watch / Hold", "#e0a800"
    else:
        signal, sig_color = "Reduce / Sell", "#dc3545"
        conv_label, conv_bg = "Caution", "#dc3545"

    # Confidence: score-derived, capped
    confidence = min(95, max(30, int(overall * 10 + 2)))

    # Horizon by archetype
    arch = sr.archetype
    horizon = {
        "mega":   "Long-term (3–7 years)",
        "largeg": "Long-term (3–5 years)",
        "smallg": "Medium-term (2–4 years)",
        "spec":   "Speculative (1–3 years)",
        "micro":  "Speculative (1–2 years)",
    }.get(arch, "Long-term (3–5 years)")

    # Action recommendation
    if "BUY" in signal:
        action = f"Strong conviction — high-quality {arch} with excellent fundamentals. Add on pullbacks."
    elif signal == "Strong Hold":
        action = "High-conviction position. Hold and monitor for next entry signal before adding more."
    elif "Watch" in signal:
        action = "Mid-conviction. Hold existing position — wait for clearer signal before sizing up."
    else:
        action = "Below conviction threshold. Review position size or await fundamental improvement."

    # Score color helper
    def _sc(val: float) -> str:
        return "#28a745" if val >= 7.5 else ("#856404" if val >= 5.5 else "#dc3545")

    return {
        "overall":    overall,
        "signal":     signal,
        "sig_color":  sig_color,
        "conv_label": conv_label,
        "conv_bg":    conv_bg,
        "confidence": confidence,
        "horizon":    horizon,
        "action":     action,
        "bull_flags": (sr.bull_flags or [])[:3],
        "bear_flags": (sr.bear_flags or [])[:2],
        "breakdown": [
            ("Fundamentals",    f,   "30%", _sc(f)),
            ("Valuation",       v,   "20%", _sc(v)),
            ("Technical",       t,   "20%", _sc(t)),
            ("Moat + Catalysts", mct, "10%", _sc(mct)),
            ("Macro / Risk",    mr,  "10%", _sc(mr)),
            ("Sentiment / Inst", si, "10%", _sc(si)),
        ],
    }


def _situation_summary(score: float, signal: str, trend: str, arch: str,
                       price: float | None = None) -> tuple:
    """
    Returns (text, bg_color, text_color) — a plain-English action prompt
    derived from score + signal combination, with archetype-aware phrasing.
    When price is provided, concrete dip-price targets are injected.
    """
    trend_note = ""
    if trend == "up":
        trend_note = " Score improving — momentum building."
    elif trend == "down":
        trend_note = " Score declining — watch closely."

    # Concrete price targets: 5% and 7% pullback from current price
    _dip5 = f" at ${price * 0.95:.2f}" if price else ""
    _dip7 = f" at ${price * 0.93:.2f}" if price else ""

    dca_phrase = (
        f"Good for adding in tranches on dips{_dip5}"
        if arch in ("mega", "largeg") else
        f"Consider a starter position{_dip7}"
    )

    if score >= 7.0:
        if signal == "CONFLUENCE":
            return (
                f"Thesis and technicals aligned — strongest setup for new or larger positions.{trend_note}",
                "#d4edda", "#155724",
            )
        if signal == "CONSOLIDATION":
            return (
                f"High-quality business in a holding pattern. {dca_phrase}, no urgency to chase.{trend_note}",
                "#e8f4f8", "#0c5460",
            )
        if signal == "SQUEEZE ON":
            return (
                f"Strong fundamentals under technical pressure. Hold existing position — avoid adding until pressure clears.{trend_note}",
                "#fff3cd", "#856404",
            )
        if signal == "RISK WATCH":
            return (
                f"High-conviction name sending a caution signal. Review position size — something is breaking down.{trend_note}",
                "#f8d7da", "#721c24",
            )

    if score >= 5.0:
        if signal == "CONFLUENCE":
            return (
                f"Technicals improving but thesis still developing. Small entry or keep on watchlist.{trend_note}",
                "#e8f4f8", "#0c5460",
            )
        if signal == "CONSOLIDATION":
            return (
                f"Mid-conviction, range-bound. Wait for a score improvement or technical breakout before adding.{trend_note}",
                "#f8f9fa", "#495057",
            )
        if signal == "SQUEEZE ON":
            return (
                f"Mid-conviction under technical pressure. Not a strong entry — wait for stabilization.{trend_note}",
                "#fff3cd", "#856404",
            )
        if signal == "RISK WATCH":
            return (
                f"Moderate conviction with a caution signal. Hold off on adding — watch for stabilization.{trend_note}",
                "#f8d7da", "#721c24",
            )

    # score < 5.0
    if signal == "RISK WATCH":
        return (
            "Below conviction threshold with deteriorating signal. Avoid adding — consider reducing.",
            "#f8d7da", "#721c24",
        )
    return (
        f"Below conviction threshold. Monitor for fundamental improvement before committing capital.{trend_note}",
        "#f8f9fa", "#6c757d",
    )


def _svg_gauge(score: float, light: str) -> str:
    """
    Pure-SVG semicircle gauge for the score (0–10).
    No external libraries — fully self-contained in the HTML.
    """
    cx, cy, r = 75, 78, 55
    nl = 46  # needle length

    color = {"green": "#22c55e", "yellow": "#eab308", "red": "#ef4444", "gray": "#6b7280"}[light]

    def pt(deg: float):
        rad = math.radians(deg)
        return cx + r * math.cos(rad), cy - r * math.sin(rad)

    def arc(d_start: float, d_end: float, stroke: str, width: int = 9, opacity: float = 1.0) -> str:
        """Arc from d_start° to d_end° (math convention, counter-clockwise = sweep=0 in SVG y-down)."""
        sx, sy = pt(d_start)
        ex, ey = pt(d_end)
        span = abs(d_start - d_end)
        large = 1 if span > 180 else 0
        # sweep=0 means counter-clockwise in SVG y-down coords → goes through the TOP of the semicircle
        return (
            f'<path d="M {sx:.2f} {sy:.2f} A {r} {r} 0 {large} 0 {ex:.2f} {ey:.2f}" '
            f'fill="none" stroke="{stroke}" stroke-width="{width}" '
            f'stroke-linecap="round" opacity="{opacity}"/>'
        )

    # Background track: two quarter-arcs avoids the degenerate 180° case
    tx, ty = pt(90)  # top of semicircle = (75, 23)
    lx, ly = pt(180)
    rx_p, ry_p = pt(0)
    bg = (
        f'<path d="M {lx:.2f} {ly:.2f} A {r} {r} 0 0 0 {tx:.2f} {ty:.2f} '
        f'A {r} {r} 0 0 0 {rx_p:.2f} {ry_p:.2f}" '
        f'fill="none" stroke="#1e293b" stroke-width="11" stroke-linecap="round"/>'
    )

    # Colored zone bands (dim tint showing red / yellow / green regions)
    # Red  zone: score 0 – 4.5  → degrees 180° – 99°
    # Yellow zone: score 4.5 – 7.0 → degrees 99° – 54°
    # Green  zone: score 7.0 – 10  → degrees 54° – 0°
    z_red    = arc(180, 99,  "#ef4444", 8, 0.28)
    z_yellow = arc(99,  54,  "#eab308", 8, 0.28)
    z_green  = arc(54,  0,   "#22c55e", 8, 0.28)

    # Active filled arc (from 180° down to score position)
    score_angle = max(0.5, 180 - score * 18)  # prevent 0° ambiguity at score=10
    active = arc(180, score_angle, color, 8, 1.0) if score > 0 else ""

    # Needle
    na = math.radians(score_angle)
    nx = cx + nl * math.cos(na)
    ny = cy - nl * math.sin(na)
    needle = (
        f'<line x1="{cx}" y1="{cy}" x2="{nx:.2f}" y2="{ny:.2f}" '
        f'stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>'
    )
    dot = f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="{color}"/>'
    inner_dot = f'<circle cx="{cx}" cy="{cy}" r="2" fill="#0f172a"/>'

    # Text labels
    score_t = (
        f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" fill="{color}" '
        f'font-size="18" font-weight="800" font-family="system-ui,sans-serif">{score}</text>'
    )
    label_t = (
        f'<text x="{cx}" y="{cy + 31}" text-anchor="middle" fill="#475569" '
        f'font-size="9" font-family="system-ui,sans-serif">/ 10</text>'
    )
    l0  = (f'<text x="{lx - 5:.0f}" y="{cy + 13}" text-anchor="end" fill="#475569" '
           f'font-size="8" font-family="system-ui,sans-serif">0</text>')
    l10 = (f'<text x="{rx_p + 5:.0f}" y="{cy + 13}" text-anchor="start" fill="#475569" '
           f'font-size="8" font-family="system-ui,sans-serif">10</text>')

    return (
        f'<svg viewBox="0 0 150 108" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:140px;height:101px;display:block;">'
        f'{bg}{z_red}{z_yellow}{z_green}{active}'
        f'{needle}{dot}{inner_dot}'
        f'{score_t}{label_t}{l0}{l10}'
        f'</svg>'
    )


def _svg_radar(sr) -> str:
    """
    Pure-SVG hexagonal spider/radar chart for the 6 Grok Logic factors.
    No external libraries required.
    """
    cats = sr.categories

    def _s(cat_id: str) -> float:
        cr = cats.get(cat_id)
        return cr.score if cr else 5.0

    factors = [
        ("Fund.",   _s("fundamentals")),
        ("Val.",    _s("valuation")),
        ("Tech.",   _s("technical")),
        ("Moat",    _s("moat")),
        ("Macro",   _s("macro")),
        ("Senti.",  _s("sentiment")),
    ]

    W, H   = 160, 160
    cx, cy = 80, 82
    max_r  = 55
    n      = len(factors)

    def vertex(i: int, radius: float):
        angle = math.radians(-90 + i * 360 / n)
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle)

    # Grid polygons (25%, 50%, 75%, 100%)
    grids = []
    for pct in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{vertex(i, max_r * pct)[0]:.1f},{vertex(i, max_r * pct)[1]:.1f}" for i in range(n))
        grids.append(f'<polygon points="{pts}" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="1"/>')

    # Axis lines
    axes = []
    for i in range(n):
        vx, vy = vertex(i, max_r)
        axes.append(f'<line x1="{cx}" y1="{cy}" x2="{vx:.1f}" y2="{vy:.1f}" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>')

    # Score polygon
    score_pts_list = []
    for i, (label, score) in enumerate(factors):
        vx, vy = vertex(i, (score / 10) * max_r)
        score_pts_list.append(f"{vx:.1f},{vy:.1f}")
    score_poly = (
        f'<polygon points="{" ".join(score_pts_list)}" '
        f'fill="rgba(99,102,241,0.22)" stroke="#6366f1" stroke-width="1.5" stroke-linejoin="round"/>'
    )

    # Labels + score dots
    labels = []
    for i, (label, score) in enumerate(factors):
        angle = math.radians(-90 + i * 360 / n)
        lx = cx + (max_r + 13) * math.cos(angle)
        ly = cy + (max_r + 13) * math.sin(angle)
        # Anchor: left/right/center
        if lx < cx - 4:   anchor = "end"
        elif lx > cx + 4: anchor = "start"
        else:              anchor = "middle"
        lc = "#22c55e" if score >= 7 else ("#eab308" if score >= 4.5 else "#ef4444")
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'dominant-baseline="middle" fill="{lc}" font-size="9" '
            f'font-weight="600" font-family="system-ui,sans-serif">{label} {score:.1f}</text>'
        )
        # dot on polygon vertex
        dvx, dvy = vertex(i, (score / 10) * max_r)
        labels.append(f'<circle cx="{dvx:.1f}" cy="{dvy:.1f}" r="2.5" fill="{lc}"/>')

    return (
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:{W}px;height:{H}px;display:block;">'
        f'{"".join(grids)}{"".join(axes)}{score_poly}{"".join(labels)}'
        f'</svg>'
    )


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

    # Category rows — expandable <details> showing factors per category
    cat_rows = ""
    for cat_id, cat_label in CAT_LABELS.items():
        cat_result = sr.categories.get(cat_id)
        if not cat_result:
            continue
        _, dot_ch = LIGHT_CSS.get(cat_result.light, LIGHT_CSS["gray"])
        dot_color = {"green": "#28a745", "yellow": "#856404", "red": "#dc3545", "gray": "#adb5bd"}[cat_result.light]
        bar_color = dot_color
        bar_w = int(cat_result.score * 10)

        # Build the expanded body: per-cat bull/bear flags only
        # (cat_result.factors are float sub-scores, not human-readable text)
        body_lines = ""
        for f in (cat_result.flags_bull or []):
            body_lines += f'<div class="cat-flag-bull">{f}</div>'
        for f in (cat_result.flags_bear or []):
            body_lines += f'<div class="cat-flag-bear">{f}</div>'

        cat_body_html = (
            f'<div class="cat-body">{body_lines}</div>'
            if body_lines else ""
        )
        # Arrow always left-aligned; invisible spacer when no expandable content
        expand_icon = (
            '<span class="cat-expand-icon">▶</span>'
            if body_lines else
            '<span class="cat-expand-icon" style="opacity:0">▶</span>'
        )

        cat_rows += f"""
    <details class="cat-details">
      <summary>
        {expand_icon}
        <span class="cat-name">{cat_label}</span>
        <div class="cat-bar-wrap"><div class="cat-bar" style="width:{bar_w}%;background:{bar_color}"></div></div>
        <span class="cat-score" style="color:{dot_color}">{cat_result.score:.1f}</span>
      </summary>
      {cat_body_html}
    </details>"""

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

    # ── Horizontal semantic metrics strip ────────────────────────────────────
    def _sem(hint, tags: dict) -> str:
        return tags.get(hint or "", "")

    _strip_spec = [
        ("RSI",          str(rsi_raw) if rsi_raw else "—",
         _rsi_hint(rsi_raw),
         {"green": "neutral", "yellow": "watch", "red": "extended"}),
        ("P/E",          f'{pe_raw:.1f}x' if pe_raw else "—",
         _pe_hint(pe_raw),
         {"green": "value", "yellow": "fair", "red": "rich"}),
        ("Rev Growth",   f'{rg_raw*100:+.1f}%' if rg_raw else "—",
         _rg_hint(rg_raw),
         {"green": "strong", "yellow": "moderate", "red": "low"}),
        ("Gross Margin", gm_str,
         _gm_hint(gm_raw),
         {"green": "excellent", "yellow": "good", "red": "thin"}),
        ("FCF Margin",   fcf_margin_str,
         _fcfm_hint(fcf_raw, rev_raw),
         {"green": "solid", "yellow": "lean", "red": "negative"}),
        ("Op Margin",    op_margin_str,
         _opm_hint(op_margin),
         {"green": "strong", "yellow": "moderate", "red": "thin"}),
        ("Short Float",  f'{sf_raw*100:.1f}%' if sf_raw else "—",
         _short_hint(sf_raw),
         {"green": "clean", "yellow": "watch", "red": "elevated"}),
        ("Inst. Own",    f'{io_raw*100:.0f}%' if io_raw else "—",
         _inst_hint(io_raw),
         {"green": "strong", "yellow": "moderate", "red": "low"}),
        ("D/E",          de_str,
         _de_hint(de),
         {"green": "lean", "yellow": "moderate", "red": "leveraged"}),
    ]

    # Analyst target: build separately showing actual % upside/downside
    _analyst_item_html = ""
    if data.get("analyst_target") and analyst_upside is not None:
        _upside_pct = analyst_upside * 100
        _upside_tag = f"{_upside_pct:+.0f}%"
        _upside_cls = (
            "ms-tag-green"  if _upside_pct > 20 else
            "ms-tag-yellow" if _upside_pct > 5  else
            "ms-tag-red"
        )
        _analyst_item_html = (
            f'<div class="ms-item">'
            f'<span class="ms-key">Analyst Tgt</span>'
            f'<span class="ms-val">${data["analyst_target"]:.0f}</span>'
            f'<span class="ms-tag {_upside_cls}">{_upside_tag}</span>'
            f'</div>'
        )

    _price_area_html = ""
    if price:
        _price_area_html = (
            f'<div class="ms-price-area">'
            f'<span class="ms-price">${price:.2f}</span>'
            f'<span class="ms-change" style="color:{change_color}">{change_str}</span>'
            f'</div>'
        )

    _strip_items_html = "".join(
        f'<div class="ms-item">'
        f'<span class="ms-key">{k}</span>'
        f'<span class="ms-val">{v}</span>'
        + (f'<span class="ms-tag ms-tag-{hint}">{_sem(hint, tags)}</span>'
           if hint and _sem(hint, tags) else "")
        + '</div>'
        for k, v, hint, tags in _strip_spec
        if v not in ("—", "")
    )

    _earnings_val = data.get("earnings_date", "") or ""
    _earnings_item = (
        f'<div class="ms-item">'
        f'<span class="ms-key">Earnings</span>'
        f'<span class="ms-val">{_earnings_val}</span>'
        f'</div>'
    ) if _earnings_val and _earnings_val != "—" else ""

    metrics_strip_html = (
        f'<div class="metrics-strip">'
        f'{_price_area_html}{_strip_items_html}{_analyst_item_html}{_earnings_item}'
        f'</div>'
    )

    # ── Situation summary (score + signal → plain-English action prompt) ─────────
    situation_text, sit_bg, sit_fg = _situation_summary(
        sr.weighted_score, sig.signal, sig.score_trend, sr.archetype, price=price
    )
    situation_html = (
        f'<div style="margin:.5rem 1.25rem .25rem;padding:.55rem .9rem;'
        f'background:{sit_bg};border-radius:6px;font-size:.8rem;'
        f'color:{sit_fg};line-height:1.5">'
        f'<strong>Situation:</strong> {situation_text}</div>'
    )

    # Divergence block (only if flipped or thesis ≠ signal)
    divergence_html = ""
    if sig.flipped or sig.divergence:
        divergence_html = f'<div class="divergence"><strong>Insight:</strong> {sig.divergence}</div>'

    archetype_label = ARCHETYPE_LABEL.get(sr.archetype, sr.archetype)
    strategies = ", ".join(r.get("strategy", []))
    thesis_updated = r.get("thesis_config", {}).get("last_updated", "")

    # ── Grok Logic panel ─────────────────────────────────────────────────────
    gl = _compute_grok_logic(sr)

    # Inject concrete pullback price into Grok action text
    if price and "pullback" in gl["action"].lower():
        _dip_px = f"${price * 0.95:.2f}"
        gl["action"] = gl["action"].replace(
            "Add on pullbacks.", f"Add on pullbacks at {_dip_px}."
        ).replace(
            "add on pullbacks.", f"add on pullbacks at {_dip_px}."
        )

    breakdown_html = "".join(
        f'<div class="grok-row">'
        f'<span class="grok-cat">{cat}</span>'
        f'<span><span class="grok-val" style="color:{color}">{val:.1f}</span>'
        f'<span class="grok-wt">{wt}</span></span>'
        f'</div>'
        for cat, val, wt, color in gl["breakdown"]
    )

    reasons_html = "".join(
        f'<div class="grok-reason bull">{f}</div>' for f in gl["bull_flags"]
    ) + "".join(
        f'<div class="grok-reason bear">{f}</div>' for f in gl["bear_flags"]
    )

    # ── Claude conviction + signal text ──────────────────────────────────────
    _claude_conv = {
        "green":  "High Conviction",
        "yellow": "Watch / Hold",
        "red":    "Caution",
        "gray":   "Monitoring",
    }.get(sr.weighted_light, "Hold")

    _claude_sig_map = {
        ("green",  "CONFLUENCE"):    "Strong Buy",
        ("green",  "CONSOLIDATION"): "Buy on Dip",
        ("green",  "SQUEEZE ON"):    "Hold — Pressure",
        ("green",  "RISK WATCH"):    "Hold — Risk Alert",
        ("yellow", "CONFLUENCE"):    "Watch / Add",
        ("yellow", "CONSOLIDATION"): "Hold / Watch",
        ("yellow", "SQUEEZE ON"):    "Hold / Watch",
        ("yellow", "RISK WATCH"):    "Reduce / Watch",
        ("red",    "CONFLUENCE"):    "Speculative",
        ("red",    "CONSOLIDATION"): "Avoid / Reduce",
        ("red",    "SQUEEZE ON"):    "Reduce",
        ("red",    "RISK WATCH"):    "Reduce / Exit",
    }
    claude_sig_text = _claude_sig_map.get((sr.weighted_light, sig.signal), "Hold")

    # Grok short signal (single line)
    grok_sig_short = (
        gl["signal"]
        .replace("BUY on Dip / Strong Hold", "Buy on Dip")
        .replace("Reduce / Sell", "Reduce")
    )

    # ── Dark-palette score colors (for dark card header) ─────────────────────
    _score_color_dark = {
        "green":  "#7ec89e",
        "yellow": "#ffc107",
        "red":    "#f08888",
        "gray":   "#9090b0",
    }[sr.weighted_light]
    _grok_sig_dark = {
        "#28a745": "#7ec89e",
        "#856404": "#ffc107",
        "#dc3545": "#f08888",
    }.get(gl["sig_color"], "#9090b0")

    # ── Score mini-cards (replace old pills) ─────────────────────────────────
    score_cards_html = f"""<div class="score-cards">
  <div class="score-card score-card-claude">
    <div class="scard-header">
      <span class="scard-num" style="color:{_score_color_dark}">{sr.weighted_score}</span>
      <span class="scard-lbl" style="color:{_score_color_dark}">Claude</span>
    </div>
    <div class="scard-signal" style="color:{_score_color_dark}">{claude_sig_text}</div>
    <div class="scard-meta">{_claude_conv}</div>
  </div>
  <div class="score-card score-card-grok">
    <div class="scard-header">
      <span class="scard-num" style="color:{_grok_sig_dark}">{gl['overall']}</span>
      <span class="scard-lbl" style="color:{_grok_sig_dark}">Grok</span>
    </div>
    <div class="scard-signal" style="color:{_grok_sig_dark}">{grok_sig_short}</div>
    <div class="scard-meta">{gl['confidence']}% · {gl['horizon']}</div>
  </div>
</div>"""

    # ── Dark theme color mappings for info panel ─────────────────────────────
    # Thesis: tinted by overall conviction
    _thesis_dark = {
        "green":  ("rgba(40,167,69,.15)",   "#7ec89e",  "#28a745"),
        "yellow": ("rgba(255,193,7,.12)",   "#e6b800",  "#ffc107"),
        "red":    ("rgba(220,53,69,.15)",   "#f08888",  "#dc3545"),
        "gray":   ("rgba(255,255,255,.05)", "#9090b0",  "#5050a0"),
    }
    thesis_dbg, thesis_dfg, thesis_dborder = _thesis_dark.get(
        sr.weighted_light, _thesis_dark["gray"]
    )

    # Situation: map light palette → dark
    _sit_dark_map = {
        "#d4edda": ("rgba(40,167,69,.15)",   "#7ec89e"),   # green
        "#e8f4f8": ("rgba(0,123,255,.12)",   "#7ec8e8"),   # teal/blue
        "#fff3cd": ("rgba(255,193,7,.12)",   "#ffc107"),   # yellow
        "#f8d7da": ("rgba(220,53,69,.15)",   "#f08888"),   # red
        "#f8f9fa": ("rgba(255,255,255,.05)", "#9090b0"),   # gray
    }
    sit_dbg, sit_dfg = _sit_dark_map.get(sit_bg, ("rgba(255,255,255,.05)", "#9090b0"))

    # Action · Grok: tinted by grok signal
    _grok_dark_map = {
        "#28a745": ("rgba(40,167,69,.12)",   "#7ec89e"),
        "#856404": ("rgba(255,193,7,.10)",   "#ffc107"),
        "#dc3545": ("rgba(220,53,69,.12)",   "#f08888"),
    }
    grok_dbg, grok_dfg = _grok_dark_map.get(gl["sig_color"], ("rgba(255,255,255,.05)", "#9090b0"))

    # Insight
    insight_dbg, insight_dfg = "rgba(255,193,7,.12)", "#ffc107"

    # ── Info panel: Thesis / Situation / Action / Insight ────────────────────
    insight_row = (
        f'<div class="info-insight" style="background:{insight_dbg};color:{insight_dfg};'
        f'border-top-color:rgba(255,255,255,.08)">'
        f'<span class="info-kw" style="color:{insight_dfg};opacity:.7">Insight</span>'
        f'{sig.divergence}</div>'
    ) if sig.divergence else ""

    info_panel_html = f"""<div class="info-panel" style="border-color:rgba(255,255,255,.1);background:#1a1a2e">
  <div class="info-thesis" style="background:{thesis_dbg};border-left:3px solid {thesis_dborder};color:#c8c8e0;border-bottom-color:rgba(255,255,255,.08)">
    <span class="info-kw" style="color:{thesis_dborder};opacity:.9">Thesis</span>{thesis}
  </div>
  <div class="info-sit-act">
    <div class="info-sit" style="background:{sit_dbg};color:{sit_dfg}">
      <span class="info-kw" style="color:{sit_dfg};opacity:.7">Situation</span>
      {situation_text}
    </div>
    <div class="info-act" style="background:{grok_dbg};border-left-color:rgba(255,255,255,.1)">
      <span class="info-kw" style="color:{grok_dfg};opacity:.7">Action · Grok</span>
      <span style="color:{grok_dfg}">{gl['action']}</span>
    </div>
  </div>
  {insight_row}
</div>"""

    # ── Key signals + sparkline in right column ───────────────────────────────
    bull_flags_panel = "".join(
        f'<div class="flag flag-bull" style="margin-bottom:5px">▲ {f}</div>'
        for f in (sr.bull_flags or [])
    ) or '<div class="flag flag-bull" style="opacity:.4">No bull flags detected</div>'
    bear_flags_panel = "".join(
        f'<div class="flag flag-bear" style="margin-bottom:5px">▼ {f}</div>'
        for f in (sr.bear_flags or [])
    ) or '<div class="flag flag-bear" style="opacity:.4">No bear flags detected</div>'

    flags_panel_html = f"""<div class="dark-signals">
  <div class="flags-panel-hdr">Key Signals</div>
  {bull_flags_panel}
  {bear_flags_panel}
  <div style="flex:1"></div>
  {_sparkline_html(history)}
</div>"""

    # ── Gauge + Radar + Analyst Consensus row ────────────────────────────────
    gauge_svg = _svg_gauge(sr.weighted_score, sr.weighted_light)
    radar_svg = _svg_radar(sr)

    # Analyst consensus from yfinance recommendationMean (1=Strong Buy … 5=Strong Sell)
    rec_mean = data.get("recommendation")  # float 1–5 or None
    _rec_labels = {
        (1.0, 1.5): ("Strong Buy",  "#22c55e"),
        (1.5, 2.5): ("Buy",         "#86efac"),
        (2.5, 3.5): ("Hold",        "#eab308"),
        (3.5, 4.5): ("Sell",        "#f97316"),
        (4.5, 5.1): ("Strong Sell", "#ef4444"),
    }
    rec_label, rec_color = "—", "#6b7280"
    if rec_mean is not None:
        for (lo, hi), (lbl, clr) in _rec_labels.items():
            if lo <= rec_mean < hi:
                rec_label, rec_color = lbl, clr
                break

    # Analyst target + upside block
    tgt_html = ""
    if data.get("analyst_target"):
        upside_pct = (analyst_upside or 0) * 100
        upside_c   = "#22c55e" if upside_pct > 15 else ("#eab308" if upside_pct > 0 else "#ef4444")
        tgt_html = (
            f'<div style="margin-top:8px;font-size:.75rem;">'
            f'<span style="color:#6060a0;">Target &nbsp;</span>'
            f'<span style="color:#c8c8e0;font-weight:700;">${data["analyst_target"]:.0f}</span>'
            f'&nbsp;&nbsp;'
            f'<span style="color:{upside_c};font-weight:700;">{upside_pct:+.0f}% upside</span>'
            f'</div>'
        )

    # Number of analysts (yfinance stores this as numberOfAnalystOpinions)
    n_analysts = data.get("number_of_analyst_opinions") or data.get("numberOfAnalystOpinions") or ""
    n_label = f"Based on {n_analysts} analysts" if n_analysts else "Wall St. consensus"

    analyst_html = f"""<div style="background:#12122a;border:1px solid rgba(255,255,255,.08);
        border-radius:8px;padding:.75rem .9rem;min-width:160px;">
  <div style="font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
       color:#6060a0;margin-bottom:.5rem;">ANALYST CONSENSUS</div>
  <div style="font-size:1.35rem;font-weight:800;color:{rec_color};line-height:1.1;">{rec_label}</div>
  <div style="font-size:.72rem;color:#6060a0;margin:2px 0 6px;">{n_label}</div>
  <div style="height:4px;border-radius:2px;background:linear-gradient(to right,#ef4444 0%,#eab308 45%,#22c55e 80%,#22c55e 100%);margin-bottom:4px;position:relative;">
    {'<div style="position:absolute;top:-3px;left:' + f'{max(0, min(100, (5 - (rec_mean or 3)) / 4 * 100)):.0f}' + '%;transform:translateX(-50%);width:8px;height:8px;background:#fff;border-radius:50%;border:2px solid #1a1a2e;"></div>' if rec_mean else ''}
  </div>
  <div style="display:flex;justify-content:space-between;font-size:.65rem;color:#475569;">
    <span>Buy</span><span>Hold</span><span>Sell</span>
  </div>
  {tgt_html}
</div>"""

    viz_row_html = f"""<div style="display:flex;flex-wrap:wrap;gap:.75rem;padding:.6rem 1.25rem;
    border-bottom:1px solid rgba(255,255,255,.07);align-items:flex-start;">
  <div style="text-align:center;">
    <div style="font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
         color:#6060a0;margin-bottom:2px;">SCORE</div>
    {gauge_svg}
  </div>
  <div style="text-align:center;">
    <div style="font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
         color:#6060a0;margin-bottom:2px;">FACTORS</div>
    {radar_svg}
  </div>
  {analyst_html}
</div>"""

    return f"""<div class="card" id="{r['ticker']}" style="scroll-margin-top:1rem;border-left:4px solid {score_border}">
  <div class="card-hdr card-hdr-dark" style="flex-wrap:wrap;gap:.5rem;align-items:flex-start">
    <div style="min-width:0;flex:1">
      <div class="ticker-name">{r['ticker']} &nbsp;<span style="font-weight:400;font-size:.9rem;color:#6c757d">{r.get('name','')}</span></div>
      <div style="display:flex;align-items:center;gap:.4rem;margin-top:.3rem;flex-wrap:wrap">
        <span class="arch-tag">{archetype_label} · {data.get('sector','—')}</span>
        <span class="signal-badge" style="{sig_style}">{sig.signal}</span>
        {flip_html}
        {trend_html}
        <span style="font-size:.7rem;color:#adb5bd">{thesis_updated}</span>
      </div>
    </div>
    {score_cards_html}
  </div>

  <div class="dark-body">
    {info_panel_html}

    {metrics_strip_html}

    {viz_row_html}

    <div class="card-body-grid">
      <div>
        <div style="padding:.75rem 1.25rem 1rem">
          <div class="section-title">Category breakdown</div>
          <div class="cat-grid">{cat_rows}</div>
        </div>
      </div>
      {flags_panel_html}
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
    Renders compact macro overlay panel.
    Data from FRED (if key set), Alpha Vantage fallback, or yfinance yields.
    Always renders; shows partial data with source badge.
    Returns empty string only if result list is empty.
    """
    if not all_results:
        return ""

    macro = {}
    for r in all_results:
        m = r.get("data", {}).get("macro", {})
        if m:
            macro = m
            break

    # Render with partial/empty data — show a setup note if no data at all
    ffr      = macro.get("fed_funds_rate")
    cpi      = macro.get("cpi_yoy")
    ten_yr   = macro.get("ten_yr_yield")
    two_yr   = macro.get("two_yr_yield")
    spread   = macro.get("yield_spread_10_2")
    unemp    = macro.get("unemployment")
    source   = macro.get("_source", "")
    spread_proxy = macro.get("_spread_proxy", False)

    has_any = any(v is not None for v in [ffr, ten_yr, two_yr, spread, unemp])

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
    elif ten_yr is not None and spread is not None:
        # partial: have yields but no FFR
        if spread < -0.2:
            regime = ("Inverted Yield Curve", "#fff3cd", "#856404")
        elif spread > 0.5:
            regime = ("Normal Curve", "#d4edda", "#155724")
        else:
            regime = ("Flat Curve", "#e2e3e5", "#495057")
    elif not has_any:
        regime = ("No data — see setup note ↓", "#e2e3e5", "#6c757d")
    else:
        regime = ("Partial data", "#e2e3e5", "#6c757d")

    regime_label, regime_bg, regime_fg = regime

    # Data source badge
    source_badge = ""
    if source:
        src_colors = {
            "FRED":          ("#e8f4f8", "#0077aa"),
            "Alpha Vantage": ("#f0f0ff", "#5050cc"),
            "yfinance":      ("#f0fff0", "#228822"),
        }
        bg, fg = src_colors.get(source, ("#f8f9fa", "#6c757d"))
        source_badge = (
            f'<span style="font-size:.65rem;padding:.15rem .5rem;border-radius:8px;'
            f'background:{bg};color:{fg};white-space:nowrap">via {source}</span>'
        )

    # Spread label (note if using 10y–5y proxy)
    spread_label = "10y−5y Spread*" if spread_proxy else "10y−2y Spread"

    items = [
        ("Fed Funds Rate", _fmt_rate(ffr),    ffr_color),
        ("10-Yr Yield",    _fmt_rate(ten_yr),  "#212529"),
        ("2-Yr Yield",     _fmt_rate(two_yr),  "#212529"),
        (spread_label,     _fmt_rate(spread),  spread_color),
        ("Unemployment",   _fmt_rate(unemp),   "#212529"),
        ("CPI YoY",        _fmt_rate(cpi),     "#212529"),
    ]

    items_html = "".join(f"""
    <div class="macro-item">
      <div class="macro-val" style="color:{color}">{val}</div>
      <div class="macro-lbl">{label}</div>
    </div>""" for label, val, color in items)

    # Setup note when no data
    setup_note = ""
    if not has_any:
        setup_note = """
  <div style="margin-top:.75rem;padding:.6rem .9rem;background:#fff3cd;border:1px solid #ffc107;
              border-radius:6px;font-size:.75rem;line-height:1.5;color:#856404">
    <strong>Setup:</strong> Add a free <code>FRED_API_KEY</code> secret to GitHub Actions for full macro data
    (register free at <a href="https://fred.stlouisfed.org/docs/api/api_key.html" target="_blank"
    style="color:#856404">fred.stlouisfed.org</a>). Treasury yields are fetched via yfinance as fallback —
    if you see all dashes, the yfinance fetch may have timed out in the Action. FFR, CPI, and unemployment
    require a FRED key or <code>ALPHA_VANTAGE_KEY</code>.
  </div>"""
    elif spread_proxy:
        setup_note = """
  <div style="margin-top:.6rem;font-size:.68rem;color:#adb5bd">
    * 2yr yield unavailable — spread shown is 10y−5y (directional proxy only).
    Add free <code>FRED_API_KEY</code> for accurate 2yr series.
  </div>"""

    # ── Plain-English interpretation ─────────────────────────────────────────
    interp_lines = []
    if has_any:
        # Rate environment
        if ffr is not None:
            if ffr <= 3.0:
                interp_lines.append(f"Rates at {ffr:.2f}% — accommodative. Strong tailwind for growth and spec names.")
            elif ffr <= 4.0:
                interp_lines.append(f"Rates at {ffr:.2f}% — moderately restrictive but declining from peak. Growth headwinds are easing.")
            elif ffr <= 5.0:
                interp_lines.append(f"Rates at {ffr:.2f}% — still elevated. Spec and small-cap names face a higher discount rate hurdle.")
            else:
                interp_lines.append(f"Rates at {ffr:.2f}% — restrictive. High-growth and pre-revenue names are most exposed.")

        # Yield curve
        if spread is not None:
            if spread < -0.2:
                interp_lines.append(f"Yield curve inverted ({spread:+.2f}%) — historically precedes recession 12–18 months out. Favor quality over spec.")
            elif spread < 0.2:
                interp_lines.append(f"Yield curve flat ({spread:+.2f}%) — transitioning. Watch for steepening as a green light for risk-on.")
            else:
                interp_lines.append(f"Yield curve normal (+{spread:.2f}%) — no recession signal. Credit conditions are healthy.")

        # Portfolio-specific impact: group tickers by archetype from all_results
        arch_map: dict[str, list[str]] = {}
        for _r in all_results:
            _arch = _r.get("score_result").archetype if _r.get("score_result") else "spec"
            arch_map.setdefault(_arch, []).append(_r["ticker"])

        rate_sensitive = arch_map.get("spec", [])[:5] + arch_map.get("micro", [])[:2]
        mega_names     = arch_map.get("mega", [])[:4]

        if rate_sensitive and ffr is not None:
            tickers_str = ", ".join(f"${t}" for t in rate_sensitive[:5])
            if ffr <= 4.0:
                interp_lines.append(f"Your most rate-sensitive names ({tickers_str}) benefit most as rates normalise.")
            else:
                interp_lines.append(f"Most rate-sensitive in your portfolio: {tickers_str} — these feel high rates hardest.")

        if mega_names:
            mega_str = ", ".join(f"${t}" for t in mega_names)
            interp_lines.append(f"Mega-cap names ({mega_str}) are less rate-sensitive and anchored by fundamentals.")

    interp_html = ""
    if interp_lines:
        bullets = "".join(f'<div style="padding:2px 0">· {l}</div>' for l in interp_lines)
        interp_html = (
            f'<div style="margin-top:.75rem;padding:.6rem .9rem;background:#f8f9fa;'
            f'border-radius:6px;font-size:.78rem;color:#495057;line-height:1.6">'
            f'{bullets}</div>'
        )

    return f"""
<div class="macro-panel">
  <div class="macro-title" style="display:flex;align-items:center;gap:.5rem">
    🏛 Macro Environment &nbsp;{source_badge}
  </div>
  <div class="macro-grid">
    {items_html}
    <span class="macro-regime" style="background:{regime_bg};color:{regime_fg}">{regime_label}</span>
  </div>
  {interp_html}
  {setup_note}
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
