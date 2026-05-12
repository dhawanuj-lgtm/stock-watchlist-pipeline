"""
thesis_ai.py — One-line thesis summary generator.

Primary:  Claude Haiku via Anthropic API (fractions of a cent per ticker).
Fallback: Rule-based template if ANTHROPIC_API_KEY is not set.

The thesis is generated ONCE and cached alongside thesis_scores.yaml.
It only regenerates when the thesis score was manually updated (last_updated changed).
"""

import os
import logging

log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


def generate_thesis(ticker: str, data: dict, score_result, thesis_config: dict) -> str:
    """
    Generate a one-line investment thesis for a ticker.

    If thesis_config has a non-null thesis_one_liner, that always wins (user's own words).
    Otherwise: try Haiku → fallback to rule-based.
    """
    # User's own words always win
    manual = thesis_config.get("thesis_one_liner")
    if manual:
        return manual

    if ANTHROPIC_API_KEY:
        return _ai_thesis(ticker, data, score_result, thesis_config)
    else:
        return _rule_thesis(ticker, data, score_result, thesis_config)


# ── AI thesis (Claude Haiku) ──────────────────────────────────────────────────

def _ai_thesis(ticker: str, data: dict, score_result, thesis_config: dict) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        archetype_desc = {
            "mega":   "mega-cap anchor stock",
            "largeg": "large growth stock",
            "smallg": "small-cap growth stock",
            "spec":   "speculative pre-profit stock",
            "micro":  "micro-cap niche stock",
        }.get(data.get("archetype", ""), "stock")

        # Build a rich prompt with actual data so the thesis is specific
        td = score_result.thesis_data
        prompt = f"""Write ONE sentence (max 25 words) that captures the investment thesis for {ticker} ({data.get('name', ticker)}), a {archetype_desc}.

Key facts:
- Sector: {data.get('sector', 'unknown')}
- Revenue growth: {td.get('revenue_growth', 'N/A')}
- Gross margin: {td.get('gross_margin', 'N/A')}
- Weighted conviction score: {td.get('weighted_score', 'N/A')}/10
- Top bull flags: {'; '.join(score_result.bull_flags[:2]) if score_result.bull_flags else 'none'}
- Top bear flags: {'; '.join(score_result.bear_flags[:1]) if score_result.bear_flags else 'none'}
- Analyst upside: {td.get('analyst_upside', 'N/A')}
- Notes: {thesis_config.get('notes', '')}

Be specific, data-driven, and actionable. Do NOT use generic phrases like "strong fundamentals". One sentence only, no preamble."""

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        thesis = message.content[0].text.strip().strip('"').strip("'")
        log.info(f"AI thesis for {ticker}: {thesis}")
        return thesis

    except Exception as e:
        log.warning(f"AI thesis failed for {ticker}: {e}. Falling back to rule-based.")
        return _rule_thesis(ticker, data, score_result, thesis_config)


# ── Rule-based fallback ───────────────────────────────────────────────────────

def _rule_thesis(ticker: str, data: dict, score_result, thesis_config: dict) -> str:
    """
    Construct a specific, data-driven thesis sentence without an LLM.
    Avoids templated boilerplate by selecting the single strongest signal.
    """
    arch = data.get("archetype", "largeg")
    score = score_result.weighted_score
    bulls = score_result.bull_flags
    bears = score_result.bear_flags
    td = score_result.thesis_data

    # Lead with the strongest bull flag if conviction is high
    if score >= 7.0 and bulls:
        lead = bulls[0]
        bear_caveat = f"; key risk: {bears[0].split(' — ')[0].lower()}" if bears else ""
        return f"{lead}{bear_caveat}."

    # For weak thesis, lead with the risk
    if score < 5.0 and bears:
        lead = bears[0]
        bull_caveat = f"; potential upside if {bulls[0].split(' — ')[0].lower()}" if bulls else ""
        return f"Caution — {lead.lower()}{bull_caveat}."

    # Mid-conviction: lead with archetype context
    archetype_lead = {
        "mega":   "Durable franchise",
        "largeg": "Growth compounder",
        "smallg": "Emerging growth",
        "spec":   "Speculative catalyst play",
        "micro":  "Niche micro-cap",
    }.get(arch, "Position")

    rev = td.get("revenue_growth")
    margin = td.get("gross_margin")
    if rev and margin:
        return f"{archetype_lead} with {rev} revenue growth and {margin} gross margin — score {score}/10."
    elif bulls:
        return f"{archetype_lead}: {bulls[0].lower()} — conviction score {score}/10."
    else:
        return f"{archetype_lead} under review — conviction score {score}/10, monitor for catalyst."
