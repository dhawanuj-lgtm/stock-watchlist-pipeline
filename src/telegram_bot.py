"""
telegram_bot.py — Telegram bot for real-time signal flip alerts.

Requires two environment variables (add as GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN — from @BotFather on Telegram (free, 2 min to create)
  TELEGRAM_CHAT_ID   — your personal chat ID (get from @userinfobot)

Falls back gracefully — logs debug message and returns False if not configured.

Setup (one-time):
  1. Open Telegram, search @BotFather
  2. /newbot → follow prompts → copy the token
  3. Search @userinfobot, start it → copy your chat ID
  4. Add both as GitHub Actions secrets
"""

import logging
import os

import requests

log = logging.getLogger(__name__)

_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Signal → emoji mapping (matches scoring framework vocabulary)
_SIGNAL_EMOJI = {
    "CONFLUENCE":    "🟢",
    "SQUEEZE ON":    "🔴",
    "CONSOLIDATION": "🔵",
    "RISK WATCH":    "🟠",
}

_LIGHT_EMOJI = {
    "green":  "🟢",
    "yellow": "🟡",
    "red":    "🔴",
    "gray":   "⚪",
}


def _send(text: str) -> bool:
    """POST a message to Telegram. Returns True on success."""
    if not _TOKEN or not _CHAT_ID:
        log.debug("Telegram: credentials not set — skipping alert (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID secrets)")
        return False
    try:
        r = requests.post(
            _API_URL.format(token=_TOKEN),
            json={
                "chat_id":    _CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"Telegram send failed (non-fatal): {e}")
        return False


# ── Alert types ───────────────────────────────────────────────────────────────

def send_flip_alert(
    ticker:     str,
    name:       str,
    old_signal: str,
    new_signal: str,
    score:      float,
    score_light:str,
    thesis:     str,
) -> bool:
    """Sent immediately when a signal flips (up or down)."""
    old_em = _SIGNAL_EMOJI.get(old_signal, "⚪")
    new_em = _SIGNAL_EMOJI.get(new_signal, "⚪")
    light  = _LIGHT_EMOJI.get(score_light, "⚪")
    direction = "📈 Upgraded" if _signal_rank(new_signal) > _signal_rank(old_signal) else "📉 Downgraded"

    text = (
        f"<b>⚡ Signal Flip — {ticker}</b>\n"
        f"{direction}: {old_em} {old_signal} → {new_em} <b>{new_signal}</b>\n"
        f"{light} Score: <b>{score}/10</b> · {name}\n"
        f"\n<i>{thesis[:160]}</i>"
    )
    return _send(text)


def send_weekly_summary(
    run_date:    str,
    group:       str,
    results:     list[dict],
) -> bool:
    """Sent at end of each pipeline run — brief digest of signal distribution."""
    from collections import Counter
    signal_counts = Counter(r["signal_result"].signal for r in results)
    light_counts  = Counter(r["score_result"].weighted_light for r in results)
    flips = [r for r in results if r["signal_result"].flipped]

    top_picks = sorted(
        [r for r in results if r["score_result"].weighted_light == "green"],
        key=lambda x: x["score_result"].weighted_score,
        reverse=True,
    )[:3]

    lines = [
        f"<b>📊 {group.upper()} Weekly Digest</b>",
        f"<i>{run_date}</i>",
        "",
        f"🟢 Green: {light_counts['green']}  🟡 Yellow: {light_counts['yellow']}  🔴 Red: {light_counts['red']}",
        f"CONFLUENCE: {signal_counts.get('CONFLUENCE',0)}  "
        f"SQUEEZE: {signal_counts.get('SQUEEZE ON',0)}  "
        f"CONSOLIDATION: {signal_counts.get('CONSOLIDATION',0)}  "
        f"RISK WATCH: {signal_counts.get('RISK WATCH',0)}",
    ]

    if flips:
        flip_str = ", ".join(
            f"{r['ticker']} ({'↑' if r['signal_result'].flip_direction == 'upgraded' else '↓'})"
            for r in flips
        )
        lines.append(f"\n⚡ Flips: {flip_str}")

    if top_picks:
        picks_str = " · ".join(
            f"{r['ticker']} {r['score_result'].weighted_score}/10"
            for r in top_picks
        )
        lines.append(f"\n🏆 Top picks: {picks_str}")

    return _send("\n".join(lines))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _signal_rank(signal: str) -> int:
    return {"CONFLUENCE": 4, "SQUEEZE ON": 3, "CONSOLIDATION": 2, "RISK WATCH": 1}.get(signal, 0)
