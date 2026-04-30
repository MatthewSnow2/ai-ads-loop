"""Evaluator — compares two ad generations and determines a winner.

Mirrors auto_research/evaluator.py pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import config
from .ads_client import CampaignMetrics

logger = logging.getLogger(__name__)


@dataclass
class GenerationComparison:
    campaign_id: str
    baseline_gen_id: int
    variant_gen_id: int
    metric_used: str                # "ctr", "conversions", or "roas"
    baseline_value: float
    variant_value: float
    improvement_pct: float
    is_winner: bool
    is_valid: bool                  # Had enough impressions/clicks
    guardrail_passed: bool          # Cost didn't spike unexpectedly
    winner: str                     # "baseline", "variant", or "inconclusive"
    reason: str


def compare_generations(
    campaign_id: str,
    baseline_gen_id: int,
    variant_gen_id: int,
    baseline_metrics: CampaignMetrics,
    variant_metrics: CampaignMetrics,
    metric: str = "ctr",
    min_impressions: int | None = None,
    min_clicks: int | None = None,
    improvement_threshold: float | None = None,
) -> GenerationComparison:
    """Compare two generations. Returns a GenerationComparison.

    Rules:
    1. Both sides must meet minimum data thresholds.
    2. Variant must beat baseline by improvement_threshold.
    3. Cost guardrail: variant CPC must not be more than 50% higher than baseline.
    """
    min_imp = min_impressions if min_impressions is not None else config.MIN_IMPRESSIONS_FOR_EVAL
    min_clk = min_clicks if min_clicks is not None else config.MIN_CLICKS_FOR_EVAL
    threshold = improvement_threshold if improvement_threshold is not None else config.IMPROVEMENT_THRESHOLD

    # Select metric values
    def _get_value(m: CampaignMetrics, metric_name: str) -> float:
        if metric_name == "ctr":
            return m.ctr
        if metric_name == "conversions":
            return float(m.conversions)
        if metric_name == "roas":
            return m.roas
        raise ValueError(f"Unknown metric: {metric_name}")

    # Validate data thresholds
    if baseline_metrics.impressions < min_imp:
        return GenerationComparison(
            campaign_id=campaign_id,
            baseline_gen_id=baseline_gen_id,
            variant_gen_id=variant_gen_id,
            metric_used=metric,
            baseline_value=_get_value(baseline_metrics, metric),
            variant_value=_get_value(variant_metrics, metric),
            improvement_pct=0.0,
            is_winner=False,
            is_valid=False,
            guardrail_passed=True,
            winner="inconclusive",
            reason=f"Baseline only has {baseline_metrics.impressions} impressions (need {min_imp})",
        )

    if variant_metrics.impressions < min_imp:
        return GenerationComparison(
            campaign_id=campaign_id,
            baseline_gen_id=baseline_gen_id,
            variant_gen_id=variant_gen_id,
            metric_used=metric,
            baseline_value=_get_value(baseline_metrics, metric),
            variant_value=_get_value(variant_metrics, metric),
            improvement_pct=0.0,
            is_winner=False,
            is_valid=False,
            guardrail_passed=True,
            winner="inconclusive",
            reason=f"Variant only has {variant_metrics.impressions} impressions (need {min_imp})",
        )

    baseline_val = _get_value(baseline_metrics, metric)
    variant_val = _get_value(variant_metrics, metric)

    # Improvement calculation
    if baseline_val > 0:
        improvement = (variant_val - baseline_val) / baseline_val
    elif variant_val > 0:
        improvement = 1.0
    else:
        improvement = 0.0

    # Cost guardrail: flag if CPC spiked more than 50%
    guardrail_passed = True
    guardrail_note = ""
    if baseline_metrics.avg_cpc_cents > 0 and variant_metrics.avg_cpc_cents > 0:
        cpc_change = (
            (variant_metrics.avg_cpc_cents - baseline_metrics.avg_cpc_cents)
            / baseline_metrics.avg_cpc_cents
        )
        if cpc_change > 0.50:
            guardrail_passed = False
            guardrail_note = (
                f" CPC guardrail: {baseline_metrics.avg_cpc_cents}c -> "
                f"{variant_metrics.avg_cpc_cents}c (+{cpc_change:.0%})."
            )

    is_winner = improvement >= threshold and guardrail_passed

    if not guardrail_passed:
        winner = "baseline"
        reason = (
            f"{metric} improved {improvement:.1%} but CPC guardrail triggered.{guardrail_note}"
        )
    elif improvement >= threshold:
        winner = "variant"
        reason = f"Winner: {metric} improved {improvement:.1%} (threshold: {threshold:.0%})"
    elif improvement > 0:
        winner = "inconclusive"
        reason = f"{metric} improved {improvement:.1%} but below threshold ({threshold:.0%})"
    elif improvement == 0:
        winner = "inconclusive"
        reason = f"No change in {metric}"
    else:
        winner = "baseline"
        reason = f"Regression: {metric} dropped {improvement:.1%}"

    logger.info(
        "Compare gen %d vs %d on %s: baseline=%.4f variant=%.4f improvement=%.1%% winner=%s",
        baseline_gen_id, variant_gen_id, metric,
        baseline_val, variant_val, improvement * 100, winner,
    )

    return GenerationComparison(
        campaign_id=campaign_id,
        baseline_gen_id=baseline_gen_id,
        variant_gen_id=variant_gen_id,
        metric_used=metric,
        baseline_value=baseline_val,
        variant_value=variant_val,
        improvement_pct=improvement,
        is_winner=is_winner,
        is_valid=True,
        guardrail_passed=guardrail_passed,
        winner=winner,
        reason=reason,
    )
