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


# ── HTML builder ──────────────────────────────────────────────────────────────

def _build_html(all_results: list[dict], run_date: str) -> str:
    sorted_r = sorted(all_results, key=lambda x: x["score_result"].weighted_score, reverse=True)

    total       = len(sorted_r)
    flip_count  = _flip_count(sorted_r)
    green_count = sum(1 for r in sorted_r if r["score_result"].weighted_light == "green")
    avg_score   = round(sum(r["score_result"].weighted_score for r in sorted_r) / total, 1) if total else 0
    upgraded    = sum(1 for r in sorted_r if r["signal_result"].flip_direction == "upgraded")
    downgraded  = sum(1 for r in sorted_r if r["signal_result"].flip_direction == "downgraded")

    rows_html = "\n".join(_row(r, i) for i, r in enumerate(sorted_r))

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{C['body_bg']};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:{C['body_bg']};padding:20px 0">
<tr><td align="center">
<table width="680" cellpadding="0" cellspacing="0" style="background:{C['card_bg']};border-radius:12px;overflow:hidden;border:1px solid {C['border']}">

<!-- Header -->
<tr><td style="background:{C['hdr_bg']};padding:20px 24px;">
  <p style="margin:0 0 4px;font-size:11px;color:rgba(255,255,255,.5)">
    {run_date} &nbsp;·&nbsp; Weekly watchlist digest &nbsp;·&nbsp; Not financial advice
  </p>
  <p style="margin:0;font-size:18px;font-weight:600;color:#fff">
    Watchlist digest — {total} tickers, {flip_count} signal flip(s)
  </p>
</td></tr>

<!-- Stats bar -->
<tr><td style="padding:16px 24px;border-bottom:1px solid {C['border']}">
  <table cellpadding="0" cellspacing="0" width="100%"><tr>
    {_stat_cell(str(green_count), "High conviction", C['green_txt'])}
    {_stat_cell(str(flip_count),  "Signal flips",    C['flip_txt'])}
    {_stat_cell("↑ " + str(upgraded),   "Upgraded",  "#28a745")}
    {_stat_cell("↓ " + str(downgraded), "Downgraded","#dc3545")}
    {_stat_cell(str(avg_score),  "Avg score",        "#212529")}
  </tr></table>
</td></tr>

<!-- Legend -->
<tr><td style="padding:10px 24px;background:{C['gray_bg']};border-bottom:1px solid {C['border']}">
  <p style="margin:0;font-size:10px;color:{C['gray_txt']}">
    <strong>🔒 Thesis score</strong> — stable, updates monthly after earnings &nbsp;&nbsp;
    <strong>⚡ Tech signal</strong> — updates every run &nbsp;&nbsp;
    <strong>↕ Flipped</strong> — signal changed since last run
  </p>
</td></tr>

<!-- Column headers -->
<tr style="background:{C['gray_bg']}">
  <td style="padding:8px 24px;font-size:10px;font-weight:600;color:{C['gray_txt']};width:130px">TICKER</td>
  <td style="padding:8px 8px;font-size:10px;font-weight:600;color:{C['gray_txt']};width:75px">PRICE</td>
  <td style="padding:8px 8px;font-size:10px;font-weight:600;color:{C['gray_txt']};width:85px">🔒 THESIS</td>
  <td style="padding:8px 8px;font-size:10px;font-weight:600;color:{C['gray_txt']};width:100px">⚡ SIGNAL</td>
  <td style="padding:8px 8px;font-size:10px;font-weight:600;color:{C['gray_txt']};width:90px">VS YESTERDAY</td>
  <td style="padding:8px 8px;font-size:10px;font-weight:600;color:{C['gray_txt']}">DIVERGENCE INSIGHT</td>
</tr>

{rows_html}

<!-- Footer -->
<tr><td style="padding:16px 24px;border-top:1px solid {C['border']}">
  <table cellpadding="0" cellspacing="0" width="100%"><tr>
    <td style="font-size:11px;color:{C['gray_txt']}">
      Data: yfinance · NewsAPI · FRED · SEC EDGAR (all free tier)
    </td>
    <td align="right">
      <a href="{PAGES_URL}" style="font-size:11px;color:{C['link']};text-decoration:none">
        View full report →
      </a>
    </td>
  </tr></table>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


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
    chg_str = (f'+{change*100:.2f}%' if change and change > 0 else f'{change*100:.2f}%') if change else "—"
    price_html = (
        f'<strong style="font-size:12px">${price:.2f}</strong>' if price else "—"
        f'<br><span style="color:{chg_color};font-size:11px">{chg_str}</span>'
    )

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


def _flip_count(results: list[dict]) -> int:
    return sum(1 for r in results if r["signal_result"].flipped)
