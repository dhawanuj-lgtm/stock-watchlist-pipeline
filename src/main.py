"""
main.py — Orchestrator entry point.

Run order:
  1. Load watchlist.yaml + thesis_scores.yaml
  2. For each ticker: fetch → score → signal detect → generate thesis
  3. Generate detailed HTML report → public/index.html (GitHub Pages)
  4. Generate + send TLDR email
  5. Save signal cache (for tomorrow's flip detection)
  6. (Optional) Fetch + score radar_peers and render in report sidebar

Usage:
  python src/main.py                        # run all tickers
  python src/main.py --group owned          # run owned portfolio (Monday cadence)
  python src/main.py --group watchlist      # run watchlist candidates (Thursday cadence)
  python src/main.py --ticker MRAM          # single ticker (debug)
  python src/main.py --no-email             # skip sending email (dry-run)

Groups:
  owned     → 39 portfolio positions, scheduled Monday 06:00 UTC
  watchlist → 41 research candidates, scheduled Thursday 06:00 UTC
              (auto-skips any ticker that also appears in owned group)

Radar peers:
  Listed under radar_peers: in watchlist.yaml.
  Fetched with yfinance-only (same free-tier APIs, no extra cost).
  Displayed in a compact grid at the top of the report for sector context.
"""

import sys
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent))

from fetcher       import fetch_ticker
from dashboard     import generate_dashboard_json
from scorer        import score_ticker
from signals       import detect_signal, save_cache
from thesis_ai     import generate_thesis
from report        import generate_report
from emailer       import send_email
from database      import write_score_row, read_history, get_current_prices
from accuracy      import compute_accuracy_report, load_accuracy_report
from darkpool      import compute_darkpool_signal
from telegram_bot  import send_flip_alert, send_entry_signal, send_position_alert, send_weekly_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Config loaders ────────────────────────────────────────────────────────────

def load_watchlist(path: str = "config/watchlist.yaml") -> tuple[list[dict], list[dict]]:
    """
    Load tickers from simple text files (preferred) or YAML (fallback).

    Text files (config/owned.txt + config/watchlist.txt) are the easy way to edit:
      - One ticker per line
      - Optional archetype after the ticker: NVDA mega
      - Lines starting with # are comments
      - Archetype auto-detected from market cap if omitted

    YAML (config/watchlist.yaml) is still supported as fallback.
    """
    from pathlib import Path
    owned_txt     = Path("config/owned.txt")
    watchlist_txt = Path("config/watchlist.txt")

    if owned_txt.exists() or watchlist_txt.exists():
        tickers = []
        if owned_txt.exists():
            tickers += _parse_txt(owned_txt, group="owned")
            log.info(f"Loaded {sum(1 for t in tickers if t['group']=='owned')} owned tickers from owned.txt")
        if watchlist_txt.exists():
            tickers += _parse_txt(watchlist_txt, group="watchlist")
            log.info(f"Loaded {sum(1 for t in tickers if t['group']=='watchlist')} watchlist tickers from watchlist.txt")
        # Radar peers still come from YAML if it exists
        radar_peers = []
        if Path(path).exists():
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
            radar_peers = cfg.get("radar_peers", [])
        return tickers, radar_peers

    # Fallback: original YAML format
    log.info("Using config/watchlist.yaml (txt files not found)")
    with open(path) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("tickers", []), cfg.get("radar_peers", [])


def _parse_txt(path, group: str) -> list[dict]:
    """
    Parse a simple text file of tickers.
    Format per line:  TICKER [archetype]  # optional comment
    Archetype defaults to 'auto' — detected from market cap during fetch.
    """
    _VALID_ARCHETYPES = {"mega", "largeg", "smallg", "spec", "micro"}
    tickers = []
    with open(path) as f:
        for raw_line in f:
            line = raw_line.split("#")[0].strip()  # strip comments
            if not line:
                continue
            parts = line.split()
            ticker = parts[0].upper()
            arch   = parts[1].lower() if len(parts) > 1 and parts[1].lower() in _VALID_ARCHETYPES else "auto"
            tickers.append({
                "ticker":    ticker,
                "group":     group,
                "archetype": arch,
                "strategy":  [],
                "sector":    None,
                "notes":     "",
            })
    return tickers


def load_thesis_scores(path: str = "config/thesis_scores.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ── Group filter (owned vs watchlist, with auto-dedup) ────────────────────────

def filter_by_group(watchlist: list[dict], group: str | None) -> list[dict]:
    """
    Filter tickers by group ('owned' or 'watchlist').
    When group='watchlist', auto-skips any ticker that also appears in owned group
    to prevent double-processing and stay within free API rate limits.
    """
    if not group:
        return watchlist

    owned_tickers: set[str] = {
        w["ticker"] for w in watchlist if w.get("group") == "owned"
    }

    result = [w for w in watchlist if w.get("group") == group]

    if group == "watchlist":
        before = len(result)
        result = [w for w in result if w["ticker"] not in owned_tickers]
        skipped = before - len(result)
        if skipped:
            log.info(
                f"  Dedup: skipped {skipped} watchlist ticker(s) already in owned group."
            )

    return result


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(
    tickers_filter: list[str] | None = None,
    group: str | None = None,
    send: bool = True,
) -> None:
    # Pacific Time (UTC-8 standard / UTC-7 daylight) — San Ramon, CA
    _utc_now = datetime.now(timezone.utc)
    _pt_offset = -7 if _utc_now.month in range(3, 11) else -8  # rough DST: Mar–Oct
    _pt_now = _utc_now + timedelta(hours=_pt_offset)
    _tz_label = "PDT" if _pt_offset == -7 else "PST"
    run_date = _pt_now.strftime(f"%A %d %b %Y, %H:%M {_tz_label}")
    log.info(f"=== Watchlist pipeline starting — {run_date} ===")

    watchlist, radar_peers_cfg = load_watchlist()
    thesis_cfg   = load_thesis_scores()

    # Filter by group first (owned / watchlist cadence)
    if group:
        watchlist = filter_by_group(watchlist, group)
        log.info(f"Group '{group}': {len(watchlist)} tickers → {[w['ticker'] for w in watchlist]}")

    # Further filter to specific tickers if requested (debug mode)
    if tickers_filter:
        watchlist = [w for w in watchlist if w["ticker"] in tickers_filter]
        log.info(f"Filtered to: {[w['ticker'] for w in watchlist]}")

    all_results: list[dict] = []
    signal_cache: dict = {}
    errors: list[str] = []

    for entry in watchlist:
        ticker   = entry["ticker"]
        archetype = entry["archetype"]
        strategy  = entry.get("strategy", [])

        log.info(f"── Processing {ticker} ({archetype}) ──")
        try:
            # 1. Fetch all data
            data = fetch_ticker(ticker, archetype)

            # Auto-detect archetype from market cap if not specified in txt file
            if archetype == "auto":
                mcap = data.get("market_cap") or 0
                if mcap > 200e9:   archetype = "mega"
                elif mcap > 10e9:  archetype = "largeg"
                elif mcap > 1e9:   archetype = "smallg"
                elif mcap > 100e6: archetype = "spec"
                else:              archetype = "micro"
                data["archetype"] = archetype
                log.info(f"  Auto-archetype: {archetype} (market cap ${mcap/1e9:.1f}B)")

            # 2. Get thesis config for this ticker (stable scores)
            t_cfg = thesis_cfg.get(ticker, {})

            # 3. Score
            score_result = score_ticker(data, t_cfg)
            log.info(
                f"  Score: {score_result.weighted_score}/10 [{score_result.weighted_light}] "
                f"| Bulls: {len(score_result.bull_flags)} | Bears: {len(score_result.bear_flags)}"
            )

            # 4. Detect technical signal + flip
            signal_result = detect_signal(data, score_result.weighted_score)
            log.info(
                f"  Signal: {signal_result.signal}"
                + (f" [FLIPPED from {signal_result.previous}]" if signal_result.flipped else "")
            )

            # 5. Generate thesis one-liner
            thesis = generate_thesis(ticker, data, score_result, t_cfg)
            log.info(f"  Thesis: {thesis[:80]}...")

            # ── Institutional accumulation signal (darkpool proxy) ────────────
            dp = compute_darkpool_signal(ticker, data)
            log.info(f"  DarkPool: {dp.sentiment} ({dp.volume_signal}) confidence={dp.confidence:.2f}")

            all_results.append({
                "ticker":        ticker,
                "name":          data.get("name", ticker),
                "archetype":     archetype,
                "strategy":      strategy,
                "data":          data,
                "score_result":  score_result,
                "signal_result": signal_result,
                "thesis":        thesis,
                "thesis_config": t_cfg,
                "dp":            dp,
            })

            signal_cache[ticker] = signal_result

            # ── Persist weekly score to history CSV ───────────────────────────
            write_score_row(
                ticker      = ticker,
                score       = score_result.weighted_score,
                signal      = signal_result.signal,
                price       = data.get("price"),
                score_light = score_result.weighted_light,
                bull_count  = len(score_result.bull_flags),
                bear_count  = len(score_result.bear_flags),
            )

            # ── Score history: compare to last week for position alerts ───────
            ticker_history = read_history(ticker, n_weeks=2)
            prev_score = (
                ticker_history[-2]["score"] if len(ticker_history) >= 2 else None
            )

            # ── Telegram: instant alert on signal flip ────────────────────────
            if signal_result.flipped:
                send_flip_alert(
                    ticker      = ticker,
                    name        = data.get("name", ticker),
                    old_signal  = signal_result.previous,
                    new_signal  = signal_result.signal,
                    score       = score_result.weighted_score,
                    score_light = score_result.weighted_light,
                    thesis      = thesis,
                    price       = data.get("price"),
                    bull_flags  = score_result.bull_flags,
                    bear_flags  = score_result.bear_flags,
                )

            # ── Telegram: watchlist ENTRY SIGNAL ─────────────────────────────
            # Fires when a watchlist ticker hits all three gates:
            #   1. Thesis score ≥ 7.0 (green conviction)
            #   2. Technical signal is bullish (CONFLUENCE or SQUEEZE ON)
            #   3. Accumulation confirmed (OBV up, or volume spike, or Quiver data)
            elif group == "watchlist":
                score_gate   = score_result.weighted_score >= 7.0
                signal_gate  = signal_result.signal in ("CONFLUENCE", "SQUEEZE ON")
                dp_gate      = dp.sentiment == "bullish" or dp.volume_signal == "accumulation"
                if score_gate and signal_gate and dp_gate:
                    send_entry_signal(
                        ticker         = ticker,
                        name           = data.get("name", ticker),
                        score          = score_result.weighted_score,
                        signal         = signal_result.signal,
                        price          = data.get("price"),
                        analyst_target = data.get("analyst_target"),
                        low_52w        = data.get("52w_low"),
                        high_52w       = data.get("52w_high"),
                        dp_note        = dp.note,
                        dp_confidence  = dp.confidence,
                        vol_momentum   = data.get("vol_momentum_5d"),
                        bull_flags     = score_result.bull_flags,
                        archetype      = archetype,
                    )
                    log.info(f"  → Entry signal sent for {ticker} (watchlist)")

            # ── Telegram: owned POSITION ALERT ───────────────────────────────
            # Three sub-alerts, each with its own threshold to avoid fatigue:
            #   strengthen: score ≥ 7.5 AND improved ≥ 0.5 pts from last week
            #   concern:    score ≤ 4.5 OR signal = RISK WATCH
            #   recovery:   prev ≤ 5.0 AND now ≥ 6.0 (bounced from concern zone)
            elif group == "owned":
                cur  = score_result.weighted_score
                prev = prev_score

                is_strengthen = (cur >= 7.5 and prev is not None and cur - prev >= 0.5)
                is_concern    = (cur <= 4.5 or signal_result.signal == "RISK WATCH")
                is_recovery   = (prev is not None and prev <= 5.0 and cur >= 6.0)

                alert_type = None
                if is_strengthen:
                    alert_type = "strengthen"
                elif is_concern and not signal_result.flipped:   # flip alert already sent above
                    alert_type = "concern"
                elif is_recovery:
                    alert_type = "recovery"

                if alert_type:
                    send_position_alert(
                        ticker         = ticker,
                        name           = data.get("name", ticker),
                        score          = cur,
                        prev_score     = prev,
                        signal         = signal_result.signal,
                        price          = data.get("price"),
                        analyst_target = data.get("analyst_target"),
                        alert_type     = alert_type,
                        bull_flags     = score_result.bull_flags,
                        bear_flags     = score_result.bear_flags,
                        dp_note        = dp.note if dp.sentiment != "neutral" else "",
                    )
                    log.info(f"  → Position alert ({alert_type}) sent for {ticker} (owned)")

        except Exception as e:
            log.error(f"  FAILED for {ticker}: {e}", exc_info=True)
            errors.append(f"{ticker}: {e}")

    if not all_results:
        log.error("No results — aborting report generation.")
        return

    # ── Radar peers (lightweight, yfinance-only; errors are non-fatal) ────────
    radar_results: list[dict] = []
    if radar_peers_cfg:
        # Only show peers whose tickers are not already in the main run
        main_tickers = {r["ticker"] for r in all_results}
        log.info(f"\n=== Fetching {len(radar_peers_cfg)} radar peers ===")
        for peer in radar_peers_cfg:
            pticker = peer["ticker"]
            if pticker in main_tickers:
                continue   # skip dupes
            try:
                pdata = fetch_ticker(pticker, peer.get("archetype", "largeg"))
                t_cfg = thesis_cfg.get(pticker, {})
                pscore = score_ticker(pdata, t_cfg)
                psig   = detect_signal(pdata, pscore.weighted_score)
                radar_results.append({
                    "ticker":       pticker,
                    "name":         pdata.get("name", pticker),
                    "archetype":    peer.get("archetype", "largeg"),
                    "data":         pdata,
                    "score_result": pscore,
                    "signal_result":psig,
                    "reason":       peer.get("reason", peer.get("sector", "Sector peer")),
                })
                log.info(f"  Radar {pticker}: {pscore.weighted_score}/10 [{pscore.weighted_light}]")
            except Exception as e:
                log.warning(f"  Radar {pticker} failed (non-fatal): {e}")

    log.info(f"\n=== Generating outputs ({len(all_results)} tickers, {len(radar_results)} radar peers) ===")

    # ── Signal accuracy scorecard (60-day lookback) ───────────────────────────
    current_prices = get_current_prices(all_results)
    try:
        accuracy_report = compute_accuracy_report(current_prices)
        log.info("✓ Accuracy report computed")
    except Exception as e:
        log.warning(f"Accuracy report failed (non-fatal): {e}")
        accuracy_report = load_accuracy_report()   # use last good one if available

    # ── Score history map for sparklines ─────────────────────────────────────
    history_map = {r["ticker"]: read_history(r["ticker"], n_weeks=12) for r in all_results}

    # 6a. Detailed HTML report → public/report.html
    generate_report(
        all_results,
        run_date,
        radar_results=radar_results or None,
        history_map=history_map,
        accuracy_report=accuracy_report,
    )
    log.info("✓ Detailed report written to public/report.html")

    # 6b. Dashboard JSON data → public/data/*.json
    try:
        generate_dashboard_json(all_results, run_date, history_map=history_map)
        log.info("✓ Dashboard JSON written to public/data/")
    except Exception as e:
        log.warning(f"Dashboard JSON generation failed (non-fatal): {e}")

    # 7. TLDR email
    if send:
        send_email(all_results, run_date)
    else:
        log.info("Email send skipped (--no-email flag). Preview at public/email_preview.html")
        # Still generate preview
        from emailer import send_email as _se
        import os
        _orig_user = os.environ.get("GMAIL_USER", "")
        os.environ["GMAIL_USER"] = ""   # force preview-only mode
        _se(all_results, run_date)
        if _orig_user:
            os.environ["GMAIL_USER"] = _orig_user

    # ── Telegram: summary only when flips occurred ────────────────────────────
    flipped_results = [r for r in all_results if r["signal_result"].flipped]
    if flipped_results:
        send_weekly_summary(
            run_date = run_date,
            group    = group or "all",
            results  = all_results,
        )

    # 8. Save signal cache for tomorrow's flip comparison
    save_cache(signal_cache)
    log.info("✓ Signal cache saved")

    # Summary
    log.info(f"\n=== Complete ===")
    log.info(f"  Processed : {len(all_results)} tickers")
    log.info(f"  Errors    : {len(errors)}")
    log.info(f"  Flips     : {sum(1 for r in all_results if r['signal_result'].flipped)}")
    if errors:
        log.warning(f"  Failed tickers: {errors}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stock watchlist pipeline")
    parser.add_argument(
        "--group",
        choices=["owned", "watchlist"],
        help="Run only this group: 'owned' (Monday) or 'watchlist' (Thursday)",
    )
    parser.add_argument("--ticker",   nargs="+", help="Run for specific tickers only (debug)")
    parser.add_argument("--no-email", action="store_true", help="Skip sending email")
    args = parser.parse_args()

    run(
        tickers_filter=args.ticker,
        group=args.group,
        send=not args.no_email,
    )
