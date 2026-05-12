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
    ticker:      str,
    name:        str,
    old_signal:  str,
    new_signal:  str,
    score:       float,
    score_light: str,
    thesis:      str,
    price:       float | None = None,
    bull_flags:  list[str] | None = None,
    bear_flags:  list[str] | None = None,
) -> bool:
    """Sent ONLY when a signal flips. Includes price, top flags, and thesis."""
    old_em = _SIGNAL_EMOJI.get(old_signal, "⚪")
    new_em = _SIGNAL_EMOJI.get(new_signal, "⚪")
    light  = _LIGHT_EMOJI.get(score_light, "⚪")
    upgraded = _signal_rank(new_signal) > _signal_rank(old_signal)
    direction = "📈 UPGRADED" if upgraded else "📉 DOWNGRADED"

    price_str = f"  Price: <b>${price:,.2f}</b>\n" if price else ""

    # Top reason (single most important flag)
    if upgraded and bull_flags:
        reason = f"\n✅ {bull_flags[0]}"
    elif not upgraded and bear_flags:
        reason = f"\n⚠️ {bear_flags[0]}"
    else:
        reason = ""

    text = (
        f"<b>{direction} · {ticker}</b> ({name})\n"
        f"{old_em} {old_signal}  →  {new_em} <b>{new_signal}</b>\n"
        f"{price_str}"
        f"{light} Composite score: <b>{score}/10</b>"
        f"{reason}\n"
        f"\n<i>{thesis[:200]}</i>"
    )
    return _send(text)


def send_entry_signal(
    ticker:       str,
    name:         str,
    score:        float,
    signal:       str,
    price:        float | None,
    analyst_target: float | None,
    low_52w:      float | None,
    high_52w:     float | None,
    dp_note:      str,
    dp_confidence: float,
    vol_momentum: float | None,
    bull_flags:   list[str] | None = None,
    archetype:    str = "largeg",
) -> bool:
    """
    Watchlist entry signal: fired when a watchlist ticker hits a bullish threshold
    (score ≥ 7.0 + CONFLUENCE + accumulation detected).
    Includes price context and estimated entry zone so you can act immediately.
    """
    sig_em  = _SIGNAL_EMOJI.get(signal, "⚪")
    upside  = f"+{((analyst_target - price) / price * 100):.1f}%" if analyst_target and price else "N/A"
    target  = f"${analyst_target:,.2f}" if analyst_target else "N/A"

    # Entry zone: ±2% around current price (tight band for limit orders)
    if price:
        entry_low  = price * 0.98
        entry_high = price * 1.02
        entry_zone = f"${entry_low:,.2f}–${entry_high:,.2f}"
    else:
        entry_zone = "N/A"

    # 52-week context
    range_str = ""
    if low_52w and high_52w and price:
        pct_from_low = (price - low_52w) / low_52w * 100
        range_str = f"  52w: ${low_52w:,.2f}–${high_52w:,.2f} (+{pct_from_low:.0f}% from low)\n"

    vol_str = f"  Volume: {vol_momentum:.1f}x avg" if vol_momentum and vol_momentum > 1.0 else ""
    top_bull = f"\n✅ {bull_flags[0]}" if bull_flags else ""
    conf_bar = "●" * round(dp_confidence * 5) + "○" * (5 - round(dp_confidence * 5))

    text = (
        f"<b>🎯 ENTRY SIGNAL · ${ticker}</b>  ({name})\n"
        f"{sig_em} {signal} · Score: <b>{score}/10</b> 🟢\n"
        f"\n"
        f"  Price:      <b>${price:,.2f}</b>\n"
        f"  Entry zone: {entry_zone}  (limit order band)\n"
        f"  Target:     {target}  ({upside} upside)\n"
        f"{range_str}"
        f"\n"
        f"📊 Accumulation [{conf_bar}]  {dp_note}\n"
        f"{vol_str}"
        f"{top_bull}\n"
        f"\n<i>Watchlist signal — confirm with your own research before acting.</i>"
    )
    return _send(text)


def send_position_alert(
    ticker:       str,
    name:         str,
    score:        float,
    prev_score:   float | None,
    signal:       str,
    price:        float | None,
    analyst_target: float | None,
    alert_type:   str,          # "strengthen" | "concern" | "recovery"
    bull_flags:   list[str] | None = None,
    bear_flags:   list[str] | None = None,
    dp_note:      str = "",
) -> bool:
    """
    Owned position alert: fired when a position's score crosses a meaningful threshold.
    - strengthen: score ≥ 7.5 and improved ≥ 0.5 pts — thesis intact, consider adding
    - concern:    score ≤ 4.5 or RISK WATCH — thesis weakening, review position
    - recovery:   was in concern zone, now back above 6.0 — re-check thesis
    """
    sig_em = _SIGNAL_EMOJI.get(signal, "⚪")
    upside = f"+{((analyst_target - price) / price * 100):.1f}%" if analyst_target and price else "N/A"
    target = f"${analyst_target:,.2f}" if analyst_target else "N/A"
    price_str = f"${price:,.2f}" if price else "N/A"

    # Score change arrow
    if prev_score is not None:
        delta = score - prev_score
        delta_str = f"  (was {prev_score}/10, {'+' if delta >= 0 else ''}{delta:.1f})"
    else:
        delta_str = ""

    if alert_type == "strengthen":
        header = f"💪 POSITION STRENGTHENING · ${ticker}"
        emoji  = "🟢"
        flag   = f"\n✅ {bull_flags[0]}" if bull_flags else ""
        body   = (
            f"Thesis strengthening: Score <b>{score}/10</b>{delta_str}\n"
            f"  Price: {price_str} · Target: {target} ({upside})\n"
            f"  Signal: {sig_em} {signal}"
            f"{flag}"
        )
    elif alert_type == "concern":
        header = f"⚠️ POSITION WATCH · ${ticker}"
        emoji  = "🔴"
        flag   = f"\n⚠️ {bear_flags[0]}" if bear_flags else ""
        body   = (
            f"Thesis weakening: Score <b>{score}/10</b>{delta_str}\n"
            f"  Price: {price_str} · Signal: {sig_em} {signal}"
            f"{flag}\n"
            f"  Consider reviewing your thesis and position size."
        )
    else:  # recovery
        header = f"🔄 POSITION RECOVERING · ${ticker}"
        emoji  = "🟡"
        flag   = f"\n✅ {bull_flags[0]}" if bull_flags else ""
        body   = (
            f"Score recovering: <b>{score}/10</b>{delta_str}\n"
            f"  Price: {price_str} · Signal: {sig_em} {signal}"
            f"{flag}"
        )

    dp_line = f"\n📊 {dp_note}" if dp_note else ""
    text = f"<b>{header}</b>  ({name})\n\n{body}{dp_line}"
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
