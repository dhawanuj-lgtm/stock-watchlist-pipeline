"""
emailer.py — TLDR email generator and sender.

Produces the two-layer email shown in the mockup:
  • Stable thesis score (lock icon — updates monthly)
  • Daily technical signal (updates every run)
  • Signal flip detection with divergence insight
  • Summary header with fleet-level stats
  • Sends via Gmail SMTP using an App Password (no OAuth needed)

Environment variables required to send:
  GMAIL_USER         — sender address (e.g. yourname@gmail.com)
  GMAIL_APP_PASSWORD — Gmail App Password (Settings → Security → App passwords)
  NOTIFY_EMAIL       — recipient address
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

log = logging.getLogger(__name__)

GMAIL_USER     = os.getenv("GMAIL_USER", "")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL", GMAIL_USER)
PAGES_URL      = os.getenv("CI_PAGES_URL", "https://your-gitlab-pages-url")


# ── Colours (inline CSS — email clients ignore stylesheets) ───────────────────
C = {
    "green_bg":    "#d4edda", "green_txt":  "#155724", "green_bdr":  "#c3e6cb",
    "yellow_bg":   "#fff3cd", "yellow_txt": "#856404", "yellow_bdr": "#ffeeba",
    "red_bg":      "#f8d7da", "red_txt":    "#721c24", "red_bdr":    "#f5c6cb",
    "gray_bg":     "#e9ecef", "gray_txt":   "#6c757d", "gray_bdr":   "#dee2e6",
    "flip_bg":     "#fff3cd", "flip_txt":   "#856404", "flip_bdr":   "#ffc107",
    "conf_bg":     "#28a745", "conf_txt":   "#fff",
    "squeeze_bg":  "#dc3545", "squeeze_txt":"#fff",
    "consol_bg":   "#007bff", "consol_txt": "#fff",
    "risk_bg":     "#fd7e14", "risk_txt":   "#fff",
    "row_alt":     "#f8f9fa",
    "border":      "#dee2e6",
    "body_bg":     "#f4f4f4",
    "card_bg":     "#ffffff",
    "hdr_bg":      "#1a1a2e",
    "link":        "#0066cc",
}

SIGNAL_COLOR = {
    "CONFLUENCE":    (C["conf_bg"],    C["conf_txt"]),
    "SQUEEZE ON":    (C["squeeze_bg"], C["squeeze_txt"]),
    "CONSOLIDATION": (C["consol_bg"],  C["consol_txt"]),
    "RISK WATCH":    (C["risk_bg"],    C["risk_txt"]),
}

LIGHT_COLOR = {
    "green":  (C["green_bg"],  C["green_txt"],  "●"),
    "yellow": (C["yellow_bg"], C["yellow_txt"], "●"),
    "red":    (C["red_bg"],    C["red_txt"],    "●"),
    "gray":   (C["gray_bg"],   C["gray_txt"],   "○"),
}

ARCHETYPE_LABEL = {
    "mega": "Mega", "largeg": "L-Growth",
    "smallg": "S-Growth", "spec": "Spec", "micro": "Micro",
}


# ── Public entry point ────────────────────────────────────────────────────────

def send_email(all_results: list[dict], run_date: str) -> None:
    html = _build_html(all_results, run_date)

    if not GMAIL_USER or not GMAIL_PASSWORD:
        log.warning("GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping email send.")
        # Still write the email HTML to public/ for debugging
        from pathlib import Path
        Path("public/email_preview.html").write_text(html)
        log.info("Email HTML saved to public/email_preview.html for preview.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 Watchlist digest — {run_date} · {_flip_count(all_results)} signal flip(s)"
    msg["From"]    = GMAIL_USER
    msg["To"]      = NOTIFY_EMAIL
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        log.info(f"Email sent to {NOTIFY_EMAIL}")
    except Exception as e:
        log.error(f"Email send failed: {e}")
        raise


# ── HTML builder — concise digest format ─────────────────────────────────────

def _build_html(all_results: list[dict], run_date: str) -> str:
    """
    Synthesised email: ~15 second read.
    Only three sections: signal flips (expanded), top green picks, cautions.
    Everything else is in the full report (linked at bottom).
    """
    sorted_r   = sorted(all_results, key=lambda x: x["score_result"].weighted_score, reverse=True)
    total      = len(sorted_r)
    flips_r    = [r for r in sorted_r if r["signal_result"].flipped]
    green_r    = [r for r in sorted_r if r["score_result"].weighted_light == "green" and not r["signal_result"].flipped]
    red_r      = [r for r in sorted_r if r["score_result"].weighted_light == "red"]
    avg_score  = round(sum(r["score_result"].weighted_score for r in sorted_r) / total, 1) if total else 0
    upgraded   = sum(1 for r in flips_r if r["signal_result"].flip_direction == "upgraded")
    downgraded = sum(1 for r in flips_r if r["signal_result"].flip_direction == "downgraded")

    # ── Section: Signal flips (most actionable) ────────────────────────────
    if flips_r:
        flip_rows = "\n".join(_flip_row(r) for r in flips_r)
        flips_section = f"""
<tr><td style="padding:12px 24px 4px;font-size:11px;font-weight:700;
               color:{C['flip_txt']};letter-spacing:.05em;border-top:1px solid {C['border']}">
  ⚡ SIGNAL FLIPS THIS RUN
</td></tr>
{flip_rows}"""
    else:
        flips_section = f"""
<tr><td style="padding:12px 24px 12px;font-size:12px;color:{C['gray_txt']};border-top:1px solid {C['border']}">
  No signal flips this run — all positions holding their signals.
</td></tr>"""

    # ── Section: Top green picks (up to 5) ────────────────────────────────
    top_green = green_r[:5]
    if top_green:
        green_rows = "\n".join(_summary_row(r, "green") for r in top_green)
        remaining = len(green_r) - len(top_green)
        more_str = f'<tr><td colspan="4" style="padding:4px 24px 12px;font-size:10px;color:{C["gray_txt"]}">+{remaining} more green tickers in full report</td></tr>' if remaining > 0 else ""
        green_section = f"""
<tr><td colspan="4" style="padding:12px 24px 4px;font-size:11px;font-weight:700;
                color:{C['green_txt']};letter-spacing:.05em;border-top:1px solid {C['border']}">
  🟢 HIGH CONVICTION ({len(green_r)} tickers)
</td></tr>
{green_rows}
{more_str}"""
    else:
        green_section = ""

    # ── Section: Cautions ─────────────────────────────────────────────────
    if red_r:
        red_rows = "\n".join(_summary_row(r, "red") for r in red_r)
        red_section = f"""
<tr><td colspan="4" style="padding:12px 24px 4px;font-size:11px;font-weight:700;
               color:{C['red_txt']};letter-spacing:.05em;border-top:1px solid {C['border']}">
  🔴 CAUTION — REVIEW NEEDED ({len(red_r)} tickers)
</td></tr>
{red_rows}"""
    else:
        red_section = ""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{C['body_bg']};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:{C['body_bg']};padding:20px 0">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="background:{C['card_bg']};border-radius:12px;overflow:hidden;border:1px solid {C['border']}">

<!-- Header -->
<tr><td style="background:{C['hdr_bg']};padding:20px 24px;">
  <p style="margin:0 0 4px;font-size:11px;color:rgba(255,255,255,.5)">
    {run_date} &nbsp;·&nbsp; Weekly watchlist digest &nbsp;·&nbsp; Not financial advice
  </p>
  <p style="margin:0;font-size:18px;font-weight:600;color:#fff">
    {total} tickers · avg {avg_score}/10 · {len(flips_r)} flip(s)
    {f"· ↑{upgraded} ↓{downgraded}" if flips_r else ""}
  </p>
</td></tr>

<!-- Content -->
<tr><td>
<table width="100%" cellpadding="0" cellspacing="0">
{flips_section}
{green_section}
{red_section}
</table>
</td></tr>

<!-- CTA footer -->
<tr><td style="padding:16px 24px;border-top:1px solid {C['border']};text-align:center">
  <a href="{PAGES_URL}"
     style="display:inline-block;background:{C['hdr_bg']};color:#fff;
            text-decoration:none;border-radius:8px;padding:10px 28px;
            font-size:13px;font-weight:600">
    View full report with charts &amp; metrics →
  </a>
  <p style="margin:10px 0 0;font-size:10px;color:{C['gray_txt']}">
    Data: yfinance · FRED · SEC EDGAR (all free tier)
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _flip_row(r: dict) -> str:
    """Expanded row for signal flip — shows old→new signal, score, top reason."""
    sr   = r["score_result"]
    sig  = r["signal_result"]
    data = r["data"]
    price = data.get("price")
    price_str = f"${price:.2f}" if price else "—"

    sig_bg, sig_txt = SIGNAL_COLOR.get(sig.signal, ("#6c757d", "#fff"))
    old_sig_bg, _ = SIGNAL_COLOR.get(sig.previous or "—", ("#6c757d", "#fff"))
    light_bg, light_txt, dot = LIGHT_COLOR.get(sr.weighted_light, LIGHT_COLOR["gray"])
    arrow = "↑" if sig.flip_direction == "upgraded" else "↓"
    top_reason = sr.bull_flags[0] if sig.flip_direction == "upgraded" and sr.bull_flags else \
                 (sr.bear_flags[0] if sr.bear_flags else sig.divergence or "")

    return f"""<tr><td style="padding:8px 24px 10px;border-bottom:1px solid {C['border']}">
  <table cellpadding="0" cellspacing="0" width="100%"><tr>
    <td style="width:70px;font-size:13px;font-weight:700">{r['ticker']}</td>
    <td style="width:60px;font-size:12px;color:#6c757d">{price_str}</td>
    <td style="padding:0 8px">
      <span style="background:{old_sig_bg};opacity:.5;color:#fff;border-radius:4px;padding:2px 6px;font-size:10px">{sig.previous or "—"}</span>
      &nbsp;{arrow}&nbsp;
      <span style="background:{sig_bg};color:{sig_txt};border-radius:4px;padding:2px 6px;font-size:10px;font-weight:700">{sig.signal}</span>
    </td>
    <td align="right" style="width:60px">
      <span style="background:{light_bg};color:{light_txt};border-radius:8px;padding:2px 8px;font-size:11px;font-weight:600">{dot} {sr.weighted_score}/10</span>
    </td>
  </tr>
  {"" if not top_reason else f'<tr><td colspan="4" style="padding:3px 0 0;font-size:11px;color:#6c757d">{top_reason[:120]}</td></tr>'}
  </table>
</td></tr>"""


def _summary_row(r: dict, light: str) -> str:
    """One-line summary row for green/red tickers — ticker, price, score, top flag."""
    sr   = r["score_result"]
    sig  = r["signal_result"]
    data = r["data"]
    price = data.get("price")
    price_str = f"${price:.2f}" if price else "—"

    light_bg, light_txt, dot = LIGHT_COLOR.get(light, LIGHT_COLOR["gray"])
    sig_bg, sig_txt = SIGNAL_COLOR.get(sig.signal, ("#6c757d", "#fff"))
    top_flag = sr.bull_flags[0] if light == "green" and sr.bull_flags else \
               (sr.bear_flags[0] if light == "red" and sr.bear_flags else "")

    return f"""<tr style="border-bottom:1px solid {C['border']}">
  <td style="padding:7px 24px;width:65px;font-size:12px;font-weight:700">{r['ticker']}</td>
  <td style="padding:7px 4px;width:60px;font-size:12px;color:#444">{price_str}</td>
  <td style="padding:7px 4px">
    <span style="background:{sig_bg};color:{sig_txt};border-radius:4px;padding:2px 6px;font-size:10px">{sig.signal}</span>
    {"" if not top_flag else f'<span style="font-size:10px;color:#6c757d;margin-left:6px">{top_flag[:80]}</span>'}
  </td>
  <td style="padding:7px 8px;width:55px;text-align:right">
    <span style="background:{light_bg};color:{light_txt};border-radius:8px;padding:2px 8px;font-size:11px;font-weight:600">{dot} {sr.weighted_score}/10</span>
  </td>
</tr>"""


def _row(r: dict, idx: int) -> str:
    sr   = r["score_result"]
    sig  = r["signal_result"]
    data = r["data"]

    row_bg = C["row_alt"] if idx % 2 == 1 else C["card_bg"]

    # Thesis score pill
    light_bg, light_txt, dot = LIGHT_COLOR.get(sr.weighted_light, LIGHT_COLOR["gray"])
    thesis_pill = (
        f'<span style="background:{light_bg};color:{light_txt};border:1px solid;'
        f'border-radius:10px;padding:3px 9px;font-size:11px;font-weight:600;white-space:nowrap">'
        f'{dot} {sr.weighted_score}/10</span>'
        f'<br><span style="font-size:10px;color:{C["gray_txt"]}">Stable</span>'
    )

    # Signal badge
    sig_bg, sig_txt = SIGNAL_COLOR.get(sig.signal, ("#6c757d", "#fff"))
    signal_html = (
        f'<span style="background:{sig_bg};color:{sig_txt};border-radius:5px;'
        f'padding:3px 7px;font-size:11px;font-weight:600;white-space:nowrap">{sig.signal}</span>'
        f'<br><span style="font-size:10px;color:{C["gray_txt"]}">{sig.subtitle}</span>'
    )

    # VS yesterday
    if sig.flipped:
        arrow = "↑" if sig.flip_direction == "upgraded" else "↓"
        vs_html = (
            f'<span style="background:{C["flip_bg"]};color:{C["flip_txt"]};'
            f'border:1px solid {C["flip_bdr"]};border-radius:8px;padding:2px 7px;'
            f'font-size:11px;font-weight:600">{arrow} Flipped</span>'
            f'<br><span style="font-size:10px;color:{C["gray_txt"]}">Was: {sig.previous}</span>'
        )
        row_highlight = f"border-left:3px solid {C['flip_bdr']}"
    else:
        days = "1d"
        vs_html = (
            f'<span style="background:{C["green_bg"]};color:{C["green_txt"]};'
            f'border-radius:8px;padding:2px 7px;font-size:11px">'
            f'= Same</span>'
            f'<br><span style="font-size:10px;color:{C["gray_txt"]}">Consistent</span>'
        )
        row_highlight = ""

    # Price
    price = data.get("price")
    change = data.get("price_change_1d_pct")
    chg_color = "#28a745" if change and change > 0 else "#dc3545"
    chg_str = (f'+{change*100:.2f}%' if change > 0 else f'{change*100:.2f}%') if change else "—"
    if price:
        price_html = (
            f'<strong style="font-size:12px">${price:.2f}</strong>'
            f'<br><span style="color:{chg_color};font-size:11px">{chg_str}</span>'
        )
    else:
        price_html = "—"

    # Ticker cell
    arch_label = ARCHETYPE_LABEL.get(sr.archetype, sr.archetype)
    next_event = data.get("earnings_date") or "—"

    return f"""<tr style="background:{row_bg};{row_highlight}">
  <td style="padding:10px 24px;vertical-align:top;border-bottom:1px solid {C['border']}">
    <strong style="font-size:13px">{r['ticker']}</strong><br>
    <span style="font-size:10px;color:{C['gray_txt']}">{arch_label} · {data.get('sector','—')}</span><br>
    <span style="font-size:10px;color:{C['gray_txt']}">Earnings: {next_event}</span>
  </td>
  <td style="padding:10px 8px;vertical-align:top;border-bottom:1px solid {C['border']}">{price_html}</td>
  <td style="padding:10px 8px;vertical-align:top;border-bottom:1px solid {C['border']}">{thesis_pill}</td>
  <td style="padding:10px 8px;vertical-align:top;border-bottom:1px solid {C['border']}">{signal_html}</td>
  <td style="padding:10px 8px;vertical-align:top;border-bottom:1px solid {C['border']}">{vs_html}</td>
  <td style="padding:10px 8px;vertical-align:top;border-bottom:1px solid {C['border']};font-size:11px;line-height:1.5;color:#212529">{sig.divergence}</td>
</tr>"""


def _stat_cell(value: str, label: str, color: str) -> str:
    return (
        f'<td align="center" style="padding:0 12px;border-right:1px solid {C["border"]}">'
        f'<div style="font-size:18px;font-weight:700;color:{color}">{value}</div>'
        f'<div style="font-size:10px;color:{C["gray_txt"]}">{label}</div></td>'
    )


def _stat_cell_named(value: str, label: str, color: str, names: str) -> str:
    """Stat cell that shows ticker names below the label."""
    names_html = (
        f'<div style="font-size:9px;color:{color};margin-top:3px;max-width:120px;word-break:break-word">{names}</div>'
        if names else ""
    )
    return (
        f'<td align="center" style="padding:0 12px;border-right:1px solid {C["border"]}">'
        f'<div style="font-size:18px;font-weight:700;color:{color}">{value}</div>'
        f'<div style="font-size:10px;color:{C["gray_txt"]}">{label}</div>'
        f'{names_html}</td>'
    )


def _flip_count(results: list[dict]) -> int:
    return sum(1 for r in results if r["signal_result"].flipped)
