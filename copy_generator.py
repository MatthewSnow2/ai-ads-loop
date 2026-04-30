"""Claude-powered ad copy generator.

Generates initial RSA headline/description pools and iterative variants
based on performance data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from . import config

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


@dataclass
class AdCopy:
    headlines: list[str]       # up to 15, max 30 chars each
    descriptions: list[str]    # up to 4, max 90 chars each
    keywords: list[str]
    rationale: str


_SYSTEM_PROMPT = """You are a direct-response Google Ads copywriter. You write Responsive Search Ads (RSAs).

Rules you never break:
- Headlines: max 30 characters each (including spaces). Count carefully.
- Descriptions: max 90 characters each (including spaces). Count carefully.
- Never duplicate headline meaning even with different words — each headline must add new value.
- Vary approaches across headlines: feature, benefit, CTA, social proof, urgency, question.
- Descriptions should be conversion-focused, specific, and end with implicit or explicit CTA.
- No exclamation marks in every headline — use sparingly for max impact.
- Write for the searcher's intent, not brand voice.

Output ONLY valid JSON — no markdown, no explanation."""

_INITIAL_POOL_PROMPT = """Generate an RSA ad copy pool for this campaign:

Campaign brief: {brief}
Target keywords: {keywords}

Return JSON exactly like this:
{{
  "headlines": ["headline 1", "headline 2", ...],
  "descriptions": ["description 1", "description 2", ...],
  "keywords": ["keyword 1", "keyword 2", ...],
  "rationale": "1-2 sentence explanation of strategy"
}}

Generate exactly {n_headlines} headlines (max 30 chars each) and {n_descriptions} descriptions (max 90 chars each).
Keywords: include 5-10 tightly targeted phrase-match keywords."""

_VARIANT_PROMPT = """You are optimizing a Google Ads RSA that needs better performance.

Current ad copy:
Headlines: {headlines}
Descriptions: {descriptions}

Performance data:
{performance_summary}

What to improve: {improvement_focus}

Generate an improved variant. Keep headlines that are working well. Replace or refine the weakest ones.

Return JSON exactly like this:
{{
  "headlines": ["headline 1", "headline 2", ...],
  "descriptions": ["description 1", "description 2", ...],
  "keywords": {keywords},
  "rationale": "Explain specifically what you changed and why based on the performance data"
}}

Generate exactly {n_headlines} headlines and {n_descriptions} descriptions.
Headline max: 30 chars. Description max: 90 chars."""


def _validate_lengths(copy: AdCopy) -> AdCopy:
    """Truncate any assets that exceed Google's character limits. Log warnings."""
    cleaned_headlines = []
    for h in copy.headlines:
        if len(h) > config.MAX_HEADLINE_CHARS:
            logger.warning(
                "Headline truncated (%d -> %d chars): '%s'",
                len(h), config.MAX_HEADLINE_CHARS, h,
            )
            h = h[: config.MAX_HEADLINE_CHARS]
        cleaned_headlines.append(h)

    cleaned_descriptions = []
    for d in copy.descriptions:
        if len(d) > config.MAX_DESCRIPTION_CHARS:
            logger.warning(
                "Description truncated (%d -> %d chars): '%s'",
                len(d), config.MAX_DESCRIPTION_CHARS, d,
            )
            d = d[: config.MAX_DESCRIPTION_CHARS]
        cleaned_descriptions.append(d)

    return AdCopy(
        headlines=cleaned_headlines,
        descriptions=cleaned_descriptions,
        keywords=copy.keywords,
        rationale=copy.rationale,
    )


def generate_initial_pool(campaign_brief: str, seed_keywords: list[str] | None = None) -> AdCopy:
    """Generate an initial RSA headline/description pool for a new campaign.

    Args:
        campaign_brief: Plain-language description of the campaign goal, product, audience.
        seed_keywords: Optional starting keywords to anchor the copy around.

    Returns:
        AdCopy with headlines, descriptions, keywords, and rationale.
    """
    keywords_str = ", ".join(seed_keywords) if seed_keywords else "derive from brief"

    prompt = _INITIAL_POOL_PROMPT.format(
        brief=campaign_brief,
        keywords=keywords_str,
        n_headlines=config.MAX_HEADLINES,
        n_descriptions=config.MAX_DESCRIPTIONS,
    )

    logger.info("Generating initial RSA copy pool for: %s", campaign_brief[:80])
    response = _get_client().messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse copy generator response as JSON: %s\nRaw: %s", e, raw[:500])
        raise ValueError(f"Copy generator returned invalid JSON: {e}") from e

    copy = AdCopy(
        headlines=data.get("headlines", []),
        descriptions=data.get("descriptions", []),
        keywords=data.get("keywords", seed_keywords or []),
        rationale=data.get("rationale", ""),
    )
    return _validate_lengths(copy)


def generate_variant(
    current_headlines: list[str],
    current_descriptions: list[str],
    current_keywords: list[str],
    performance_data: dict,
    improvement_focus: str = "CTR",
) -> AdCopy:
    """Generate an improved ad copy variant based on performance data.

    Args:
        current_headlines: Current RSA headlines.
        current_descriptions: Current RSA descriptions.
        current_keywords: Current keyword list (passed through unchanged).
        performance_data: Dict with keys like impressions, clicks, ctr, conversions, roas,
                          and optionally top_headlines (list of best-performing headline texts).
        improvement_focus: What metric to optimize for ("CTR", "conversions", "ROAS").

    Returns:
        AdCopy with improved headlines/descriptions and rationale explaining what changed.
    """
    perf_lines = []
    for k, v in performance_data.items():
        if k == "top_headlines" and isinstance(v, list):
            perf_lines.append(f"Top performing headlines: {', '.join(v[:5])}")
        else:
            perf_lines.append(f"{k}: {v}")
    performance_summary = "\n".join(perf_lines)

    prompt = _VARIANT_PROMPT.format(
        headlines=json.dumps(current_headlines),
        descriptions=json.dumps(current_descriptions),
        performance_summary=performance_summary,
        improvement_focus=improvement_focus,
        keywords=json.dumps(current_keywords),
        n_headlines=config.MAX_HEADLINES,
        n_descriptions=config.MAX_DESCRIPTIONS,
    )

    logger.info("Generating RSA variant (focus: %s)", improvement_focus)
    response = _get_client().messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Variant generator returned invalid JSON: %s\nRaw: %s", e, raw[:500])
        raise ValueError(f"Copy generator returned invalid JSON: {e}") from e

    copy = AdCopy(
        headlines=data.get("headlines", current_headlines),
        descriptions=data.get("descriptions", current_descriptions),
        keywords=data.get("keywords", current_keywords),
        rationale=data.get("rationale", ""),
    )
    return _validate_lengths(copy)
