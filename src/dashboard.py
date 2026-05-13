"""
dashboard.py — JSON data exporter for the interactive HTML dashboard.

Generates per-ticker JSON + a manifest for the dashboard to consume.
The dashboard JS fetches these files client-side — no backend required.

Output:
  public/data/manifest.json      — all tickers with summary scores
  public/data/{TICKER}.json      — full analysis including pre-rendered card HTML
"""

import json
from pathlib import Path

from report import _ticker_card, _compute_grok_logic, _situation_summary, ARCHETYPE_LABEL


def generate_dashboard_json(
    all_results: list[dict],
    run_date: str,
    history_map: dict | None = None,
) -> None:
    """
    Export per-ticker JSON and manifest for the interactive dashboard.
    Reuses the existing _ticker_card renderer — zero duplication.
    """
    history_map = history_map or {}
    out_dir = Path("public/data")
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_tickers = []

    for r in all_results:
        sr   = r["score_result"]
        sig  = r["signal_result"]
        data = r["data"]
        history = history_map.get(r["ticker"], [])
        price = data.get("price")

        # Full rendered card HTML — reuse existing Python renderer, zero JS duplication
        card_html = _ticker_card(r, history=history)

        # Grok panel
        gl = _compute_grok_logic(sr)
        grok_signal_short = (
            gl["signal"]
            .replace("BUY on Dip / Strong Hold", "Buy on Dip")
            .replace("Reduce / Sell", "Reduce")
        )
        if price and "pullback" in gl["action"].lower():
            _dip = f"${price * 0.95:.2f}"
            gl["action"] = gl["action"].replace("Add on pullbacks.", f"Add on pullbacks at {_dip}.")

        # Situation text
        sit_text, sit_bg, sit_fg = _situation_summary(
            sr.weighted_score, sig.signal, sig.score_trend, sr.archetype, price=price
        )

        # Score trend display string
        score_delta_str = ""
        if sig.score_trend == "up" and sig.score_delta:
            score_delta_str = f"+{sig.score_delta}"
        elif sig.score_trend == "down" and sig.score_delta:
            score_delta_str = str(sig.score_delta)

        # History (last 12 weeks for sparkline)
        hist_points: list[dict] = []
        for row in history[-12:]:
            try:
                hist_points.append({
                    "date":  str(row.get("date", ""))[:10],
                    "score": float(row["score"]),
                })
            except (KeyError, ValueError, TypeError):
                pass

        # Category summary
        cat_scores = {
            cat_id: {"score": cr.score, "light": cr.light}
            for cat_id, cr in (sr.categories or {}).items()
        }

        ticker_summary = {
            "ticker":          r["ticker"],
            "name":            r.get("name", ""),
            "score":           sr.weighted_score,
            "light":           sr.weighted_light,
            "signal":          sig.signal,
            "signal_flipped":  sig.flipped,
            "score_trend":     sig.score_trend,
            "score_delta":     score_delta_str,
            "price":           price,
            "price_change_1d": data.get("price_change_1d_pct"),
            "archetype":       sr.archetype,
            "archetype_label": ARCHETYPE_LABEL.get(sr.archetype, sr.archetype),
            "sector":          data.get("sector", "—"),
            "grok_score":      gl["overall"],
            "grok_signal":     grok_signal_short,
        }
        manifest_tickers.append(ticker_summary)

        ticker_json = {
            **ticker_summary,
            "thesis":     r.get("thesis", ""),
            "card_html":  card_html,
            "history":    hist_points,
            "run_date":   run_date,
            "bull_flags": sr.bull_flags or [],
            "bear_flags": sr.bear_flags or [],
            "insight":    sig.divergence or "",
            "situation":  {"text": sit_text, "bg": sit_bg, "fg": sit_fg},
            "grok": {
                "overall":    gl["overall"],
                "signal":     gl["signal"],
                "confidence": gl["confidence"],
                "horizon":    gl["horizon"],
                "action":     gl["action"],
                "conv_label": gl["conv_label"],
                "sig_color":  gl["sig_color"],
            },
            "cat_scores": cat_scores,
            "metrics": {
                "price":              price,
                "price_change_1d":    data.get("price_change_1d_pct"),
                "rsi":                data.get("rsi_14"),
                "pe_forward":         data.get("pe_forward"),
                "revenue_growth_yoy": data.get("revenue_growth_yoy"),
                "gross_margin":       data.get("gross_margin") or data.get("edgar_gross_margin"),
                "op_margin":          data.get("operating_margin"),
                "fcf":                data.get("free_cashflow") or data.get("edgar_fcf"),
                "total_revenue":      data.get("total_revenue"),
                "de":                 data.get("debt_to_equity"),
                "analyst_target":     data.get("analyst_target"),
                "analyst_upside":     data.get("analyst_upside"),
                "short_float":        data.get("short_float_pct"),
                "inst_ownership":     data.get("inst_ownership_pct"),
                "earnings_date":      data.get("earnings_date"),
            },
        }

        (out_dir / f"{r['ticker']}.json").write_text(
            json.dumps(ticker_json, default=str, ensure_ascii=False)
        )

    manifest_tickers.sort(key=lambda x: x["score"], reverse=True)
    manifest = {
        "run_date": run_date,
        "total":    len(manifest_tickers),
        "tickers":  manifest_tickers,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False)
    )
    print(f"[dashboard] {len(manifest_tickers)} tickers → public/data/")
