"""Main orchestrator — daily cron loop for the AI Google Ads optimization system.

Entry point: python -m ai_ads_loop.runner [--dry-run] [--campaign-id ID] [--status]

The loop:
  For each active campaign:
    1. Pull today's metrics snapshot
    2. If experiment collecting and has enough data -> evaluate winner
    3. Elif cooldown elapsed -> generate copy variant and start experiment
    4. Check kill switch conditions
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta

from . import config
from .ads_client import AdsClient, DryRunAdsClient
from .copy_generator import generate_initial_pool, generate_variant
from .evaluator import compare_generations
from .kill_switch import check_kill_switch, trigger_kill_switch
from .repository import Repository

logger = logging.getLogger(__name__)


def _hil_gate(copy_display: str, campaign_name: str) -> bool:
    """Human-in-the-loop gate. Returns True if approved.

    If running interactively (TTY), prompts for approval.
    If not TTY, prints instructions for manual DB approval and returns False.
    """
    print("\n" + "=" * 60)
    print(f"HIL GATE — Generation 1 for campaign: {campaign_name}")
    print("=" * 60)
    print(copy_display)
    print("=" * 60)

    if sys.stdin.isatty():
        answer = input("Approve this ad copy? [yes/no]: ").strip().lower()
        return answer in ("yes", "y")
    else:
        logger.warning(
            "HIL gate: not running interactively. "
            "Set generations.approved=1 in the DB to proceed."
        )
        return False


def _format_copy_for_display(headlines: list[str], descriptions: list[str]) -> str:
    lines = ["HEADLINES:"]
    for i, h in enumerate(headlines, 1):
        lines.append(f"  {i:2}. {h}  ({len(h)} chars)")
    lines.append("\nDESCRIPTIONS:")
    for i, d in enumerate(descriptions, 1):
        lines.append(f"  {i}. {d}  ({len(d)} chars)")
    return "\n".join(lines)


def run_loop(
    dry_run: bool = False,
    campaign_id_filter: int | None = None,
) -> None:
    """Main orchestration loop. Processes all active campaigns once."""
    repo = Repository()
    ads = DryRunAdsClient() if dry_run else AdsClient()

    campaigns = repo.get_active_campaigns()
    if campaign_id_filter:
        campaigns = [c for c in campaigns if c["id"] == campaign_id_filter]

    if not campaigns:
        logger.info("No active campaigns to process.")
        return

    logger.info("Processing %d campaign(s)", len(campaigns))

    for campaign in campaigns:
        cid = campaign["id"]
        name = campaign["name"]
        google_campaign_id = campaign["google_campaign_id"]
        google_ad_group_id = campaign["google_ad_group_id"]
        daily_budget_cents = campaign["daily_budget_cents"]

        logger.info("=" * 50)
        logger.info("Campaign: %s (id=%d)", name, cid)

        # 1. Pull metrics snapshot
        try:
            metrics = ads.get_campaign_metrics(google_campaign_id, days_back=1)
        except Exception as e:
            logger.error("Failed to pull metrics for campaign %d: %s", cid, e)
            continue

        current_gen = repo.get_current_generation(cid)
        if current_gen:
            repo.record_metrics_snapshot(
                campaign_id=cid,
                generation_id=current_gen["id"],
                snapshot_date=str(datetime.now().date()),
                impressions=metrics.impressions,
                clicks=metrics.clicks,
                ctr=metrics.ctr,
                avg_cpc_cents=metrics.avg_cpc_cents,
                conversions=metrics.conversions,
                cost_cents=metrics.cost_cents,
                roas=metrics.roas,
            )
            logger.info(
                "Snapshot: impressions=%d clicks=%d ctr=%.3f cost=%dc",
                metrics.impressions, metrics.clicks, metrics.ctr, metrics.cost_cents,
            )

        # 2. Kill switch check
        if check_kill_switch(
            campaign_id=str(google_campaign_id),
            daily_spend_cents=metrics.cost_cents,
            daily_budget_cents=daily_budget_cents,
            threshold_pct=campaign["kill_switch_threshold_pct"],
        ):
            if not dry_run:
                trigger_kill_switch(
                    campaign_id=str(google_campaign_id),
                    reason="Daily spend exceeded kill switch threshold",
                    metric_value=metrics.cost_cents,
                    threshold_value=daily_budget_cents * (campaign["kill_switch_threshold_pct"] / 100),
                    ads_client=ads,
                    repo=repo,
                )
                repo.update_campaign_status(cid, "killed")
            else:
                logger.warning("[DRY RUN] Would trigger kill switch for campaign %d", cid)
            continue

        # 3. Check active experiment
        active_experiment = repo.get_active_experiment(cid)
        if active_experiment:
            exp_id = active_experiment["id"]
            baseline_gen_id = active_experiment["baseline_generation_id"]
            variant_gen_id = active_experiment["variant_generation_id"]

            # Get aggregate metrics for each generation
            baseline_metrics_agg = repo.get_generation_metrics(baseline_gen_id)
            variant_metrics_agg = repo.get_generation_metrics(variant_gen_id)

            total_impressions = (
                (baseline_metrics_agg["impressions"] or 0)
                + (variant_metrics_agg["impressions"] or 0)
            )

            if total_impressions >= config.MIN_IMPRESSIONS_FOR_EVAL:
                repo.update_experiment_field(exp_id, "min_impressions_reached", 1)

                # Build CampaignMetrics objects from aggregated DB data
                from .ads_client import CampaignMetrics as CM
                bm = CM(
                    campaign_id=str(google_campaign_id),
                    date_range_start="",
                    date_range_end="",
                    impressions=baseline_metrics_agg["impressions"] or 0,
                    clicks=baseline_metrics_agg["clicks"] or 0,
                    ctr=baseline_metrics_agg["ctr"] or 0.0,
                    avg_cpc_cents=baseline_metrics_agg["avg_cpc_cents"] or 0,
                    conversions=baseline_metrics_agg["conversions"] or 0.0,
                    cost_cents=baseline_metrics_agg["cost_cents"] or 0,
                    roas=baseline_metrics_agg["roas"] or 0.0,
                )
                vm = CM(
                    campaign_id=str(google_campaign_id),
                    date_range_start="",
                    date_range_end="",
                    impressions=variant_metrics_agg["impressions"] or 0,
                    clicks=variant_metrics_agg["clicks"] or 0,
                    ctr=variant_metrics_agg["ctr"] or 0.0,
                    avg_cpc_cents=variant_metrics_agg["avg_cpc_cents"] or 0,
                    conversions=variant_metrics_agg["conversions"] or 0.0,
                    cost_cents=variant_metrics_agg["cost_cents"] or 0,
                    roas=variant_metrics_agg["roas"] or 0.0,
                )

                comparison = compare_generations(
                    campaign_id=str(cid),
                    baseline_gen_id=baseline_gen_id,
                    variant_gen_id=variant_gen_id,
                    baseline_metrics=bm,
                    variant_metrics=vm,
                    metric=active_experiment["metric_compared"],
                )

                logger.info("Experiment result: %s", comparison.reason)

                if comparison.winner == "variant":
                    logger.info("Promoting variant gen %d", variant_gen_id)
                    repo.update_experiment_status(exp_id, "promoted", winner="variant")
                    repo.update_campaign_current_generation(cid, variant_gen_id)
                    # Pause baseline ad (if tracked)
                    # Future: track ad_ids per generation for fine-grained pausing
                elif comparison.winner == "baseline":
                    logger.info("Keeping baseline gen %d, rolling back variant", baseline_gen_id)
                    repo.update_experiment_status(exp_id, "rolled_back", winner="baseline")
                else:
                    logger.info("Inconclusive — continuing data collection")
                    repo.update_experiment_status(exp_id, "inconclusive", winner="inconclusive")

            else:
                logger.info(
                    "Experiment collecting: %d/%d impressions so far",
                    total_impressions, config.MIN_IMPRESSIONS_FOR_EVAL,
                )
            continue

        # 4. Check if cooldown has elapsed for next mutation
        last_mutation_at = repo.get_last_mutation_time(cid)
        if last_mutation_at:
            elapsed_hours = (datetime.now() - last_mutation_at).total_seconds() / 3600
            if elapsed_hours < config.MUTATION_COOLDOWN_HOURS:
                logger.info(
                    "Cooldown: %.1fh elapsed / %dh required. Skipping mutation.",
                    elapsed_hours, config.MUTATION_COOLDOWN_HOURS,
                )
                continue

        # 5. Generate new variant
        current_gen = repo.get_current_generation(cid)
        generation_number = (current_gen["generation_number"] + 1) if current_gen else 1
        is_first_generation = generation_number == 1

        if is_first_generation:
            # Get campaign brief from DB
            brief = campaign.get("campaign_brief", name)
            logger.info("Generating initial copy pool (gen 1) for '%s'", name)
            copy = generate_initial_pool(campaign_brief=brief)
        else:
            # Use performance data to inform the variant
            gen_metrics = repo.get_generation_metrics(current_gen["id"])
            performance_data = {
                "impressions": gen_metrics.get("impressions", 0),
                "clicks": gen_metrics.get("clicks", 0),
                "ctr": f"{(gen_metrics.get('ctr', 0) or 0):.3f}",
                "conversions": gen_metrics.get("conversions", 0),
                "roas": f"{(gen_metrics.get('roas', 0) or 0):.2f}",
            }
            import json
            current_headlines = json.loads(current_gen["headlines"])
            current_descriptions = json.loads(current_gen["descriptions"])
            current_keywords = json.loads(current_gen["keywords"])

            logger.info("Generating variant (gen %d) for '%s'", generation_number, name)
            copy = generate_variant(
                current_headlines=current_headlines,
                current_descriptions=current_descriptions,
                current_keywords=current_keywords,
                performance_data=performance_data,
            )

        # 6. HIL gate on first generation
        approved = True
        if is_first_generation:
            display = _format_copy_for_display(copy.headlines, copy.descriptions)
            approved = _hil_gate(display, name)
            if not approved:
                logger.warning("HIL gate: copy not approved. Saving as unapproved for manual review.")

        import json
        new_gen_id = repo.create_generation(
            campaign_id=cid,
            generation_number=generation_number,
            headlines=copy.headlines,
            descriptions=copy.descriptions,
            keywords=copy.keywords,
            rationale=copy.rationale,
            approved=approved,
        )
        logger.info(
            "Created generation %d (id=%d) approved=%s: %s",
            generation_number, new_gen_id, approved, copy.rationale[:100],
        )

        if approved and not dry_run:
            # Apply to Google Ads
            created_ad = ads.create_rsa(
                ad_group_id=google_ad_group_id,
                headlines=copy.headlines,
                descriptions=copy.descriptions,
            )
            repo.mark_generation_applied(new_gen_id)
            logger.info("Applied gen %d to Google Ads (ad_id=%s)", new_gen_id, created_ad.ad_id)

            # Create experiment record (if not first gen)
            if current_gen:
                repo.create_experiment(
                    campaign_id=cid,
                    baseline_generation_id=current_gen["id"],
                    variant_generation_id=new_gen_id,
                    metric_compared="ctr",
                )
                logger.info(
                    "Started experiment: baseline gen %d vs variant gen %d",
                    current_gen["id"], new_gen_id,
                )

        time.sleep(2)  # rate limit between campaigns

    logger.info("Loop complete.")


def show_status() -> None:
    """Print campaign status summary and exit."""
    repo = Repository()
    campaigns = repo.get_all_campaigns()
    if not campaigns:
        print("No campaigns found.")
        return

    print(f"\n{'Campaign':<30} {'Status':<10} {'Gen':<5} {'Experiments':<12} {'Last Run'}")
    print("-" * 75)
    for c in campaigns:
        gen = repo.get_current_generation(c["id"])
        gen_num = gen["generation_number"] if gen else 0
        exp_count = repo.get_experiment_count(c["id"])
        last_snap = repo.get_last_snapshot_date(c["id"]) or "never"
        print(f"{c['name']:<30} {c['status']:<10} {gen_num:<5} {exp_count:<12} {last_snap}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Google Ads Loop — daily optimization runner"
    )
    parser.add_argument("--dry-run", action="store_true", help="No real API calls")
    parser.add_argument("--campaign-id", type=int, default=None, help="Run only this campaign ID")
    parser.add_argument("--status", action="store_true", help="Show campaign status and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.status:
        show_status()
        return

    run_loop(dry_run=args.dry_run, campaign_id_filter=args.campaign_id)


if __name__ == "__main__":
    main()
