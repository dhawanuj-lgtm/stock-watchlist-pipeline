"""
main.py — Orchestrator entry point.

Run order:
  1. Load watchlist.yaml + thesis_scores.yaml
  2. For each ticker: fetch → score → signal detect → generate thesis
  3. Generate detailed HTML report → public/index.html (GitLab Pages)
  4. Generate + send TLDR email
  5. Save signal cache (for tomorrow's flip detection)

Usage:
  python src/main.py                   # run all tickers
  python src/main.py --ticker MRAM     # single ticker (debug)
  python src/main.py --no-email        # skip sending email (dry-run)
"""

import sys
import logging
import argparse
from datetime import datetime
from pathlib import Path

import yaml

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent))

from fetcher    import fetch_ticker
from scorer     import score_ticker
from signals    import detect_signal, save_cache
from thesis_ai  import generate_thesis
from report     import generate_report
from emailer    import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Config loaders ────────────────────────────────────────────────────────────

def load_watchlist(path: str = "config/watchlist.yaml") -> list[dict]:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("tickers", [])


def load_thesis_scores(path: str = "config/thesis_scores.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(tickers_filter: list[str] | None = None, send: bool = True) -> None:
    run_date = datetime.utcnow().strftime("%A %d %b %Y, %H:%M UTC")
    log.info(f"=== Watchlist pipeline starting — {run_date} ===")

    watchlist    = load_watchlist()
    thesis_cfg   = load_thesis_scores()

    # Filter to specific tickers if requested (debug mode)
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
            })

            signal_cache[ticker] = signal_result

        except Exception as e:
            log.error(f"  FAILED for {ticker}: {e}", exc_info=True)
            errors.append(f"{ticker}: {e}")

    if not all_results:
        log.error("No results — aborting report generation.")
        return

    log.info(f"\n=== Generating outputs ({len(all_results)} tickers) ===")

    # 6. Detailed HTML report → public/index.html
    generate_report(all_results, run_date)
    log.info("✓ Detailed report written to public/index.html")

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
    parser.add_argument("--ticker",   nargs="+", help="Run for specific tickers only (debug)")
    parser.add_argument("--no-email", action="store_true", help="Skip sending email")
    args = parser.parse_args()

    run(
        tickers_filter=args.ticker,
        send=not args.no_email,
    )
