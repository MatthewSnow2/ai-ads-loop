"""Kill switch monitor.

Auto-pauses campaigns when spend or performance exceeds guardrail thresholds.
"""

from __future__ import annotations

import logging
from datetime import date

from . import config

logger = logging.getLogger(__name__)


def check_kill_switch(
    campaign_id: str,
    daily_spend_cents: int,
    daily_budget_cents: int,
    threshold_pct: float | None = None,
) -> bool:
    """Return True if kill switch should be triggered.

    Args:
        campaign_id: Campaign being checked.
        daily_spend_cents: Actual spend so far today.
        daily_budget_cents: Campaign daily budget.
        threshold_pct: % of budget that triggers the switch (default: config value).
    """
    threshold = threshold_pct if threshold_pct is not None else config.KILL_SWITCH_THRESHOLD_PCT
    expected_max = daily_budget_cents * (threshold / 100.0)

    if daily_spend_cents >= expected_max:
        logger.warning(
            "Kill switch check TRIGGERED: campaign=%s spend=%dc budget=%dc threshold=%.0f%%",
            campaign_id, daily_spend_cents, daily_budget_cents, threshold,
        )
        return True

    logger.debug(
        "Kill switch check OK: campaign=%s spend=%dc / %dc (%.1f%% of %d%% threshold)",
        campaign_id, daily_spend_cents, daily_budget_cents,
        (daily_spend_cents / daily_budget_cents * 100) if daily_budget_cents > 0 else 0,
        threshold,
    )
    return False


def trigger_kill_switch(
    campaign_id: str,
    reason: str,
    metric_value: float,
    threshold_value: float,
    ads_client=None,
    repo=None,
) -> None:
    """Execute the kill switch: pause campaign, log event.

    Args:
        campaign_id: Campaign to pause.
        reason: Human-readable trigger reason.
        metric_value: The metric value that triggered it.
        threshold_value: The threshold that was exceeded.
        ads_client: AdsClient instance (or DryRunAdsClient). If None, only logs.
        repo: Repository instance for logging event. If None, only logs.
    """
    logger.warning(
        "KILL SWITCH TRIGGERED: campaign=%s reason=%s value=%.4f threshold=%.4f",
        campaign_id, reason, metric_value, threshold_value,
    )

    if ads_client is not None:
        try:
            ads_client.pause_campaign(campaign_id)
            action_taken = "paused"
        except Exception as e:
            logger.error("Kill switch: failed to pause campaign %s: %s", campaign_id, e)
            action_taken = "pause_failed"
    else:
        logger.warning("Kill switch: no ads_client provided, campaign NOT paused")
        action_taken = "alert_only"

    if repo is not None:
        repo.log_kill_switch_event(
            campaign_id=int(campaign_id),
            trigger_reason=reason,
            metric_value=metric_value,
            threshold_value=threshold_value,
            action_taken=action_taken,
        )

    # Stdout alert (future: Slack/Telegram notification hook here)
    print(
        f"\n{'='*60}\n"
        f"KILL SWITCH ACTIVATED — {date.today()}\n"
        f"Campaign: {campaign_id}\n"
        f"Reason: {reason}\n"
        f"Value: {metric_value:.4f}  Threshold: {threshold_value:.4f}\n"
        f"Action: {action_taken}\n"
        f"{'='*60}\n"
    )
