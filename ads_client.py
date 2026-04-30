"""Google Ads API wrapper.

Wraps the google-ads Python client. All campaign/ad/metrics operations go here.
Use DryRunAdsClient for testing without making real API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from . import config

logger = logging.getLogger(__name__)


@dataclass
class CampaignMetrics:
    campaign_id: str
    date_range_start: str
    date_range_end: str
    impressions: int = 0
    clicks: int = 0
    ctr: float = 0.0
    avg_cpc_cents: int = 0
    conversions: float = 0.0
    cost_cents: int = 0
    roas: float = 0.0


@dataclass
class AdGroupMetrics:
    ad_group_id: str
    ad_id: str
    impressions: int = 0
    clicks: int = 0
    ctr: float = 0.0
    conversions: float = 0.0
    cost_cents: int = 0


@dataclass
class CreatedCampaign:
    campaign_resource: str
    campaign_id: str
    ad_group_resource: str
    ad_group_id: str


@dataclass
class CreatedAd:
    ad_resource: str
    ad_id: str
    ad_group_id: str


class AdsClient:
    """Live Google Ads API client."""

    def __init__(self) -> None:
        self._client = self._build_client()

    def _build_client(self) -> Any:
        """Build authenticated Google Ads client from ~/.env.shared credentials."""
        try:
            from google.ads.googleads.client import GoogleAdsClient as _GoogleAdsClient
        except ImportError:
            raise RuntimeError(
                "google-ads package not installed. Run: pip install google-ads"
            )

        credentials = {
            "developer_token": config.GOOGLE_ADS_DEVELOPER_TOKEN,
            "client_id": config.GOOGLE_ADS_CLIENT_ID,
            "client_secret": config.GOOGLE_ADS_CLIENT_SECRET,
            "refresh_token": config.GOOGLE_ADS_REFRESH_TOKEN,
            "login_customer_id": config.GOOGLE_ADS_LOGIN_CUSTOMER_ID,
            "use_proto_plus": True,
        }
        return _GoogleAdsClient.load_from_dict(credentials)

    def create_campaign(
        self,
        name: str,
        daily_budget_cents: int,
        keywords: list[str],
        customer_id: str | None = None,
    ) -> CreatedCampaign:
        """Create a search campaign with one ad group and keyword list."""
        cid = customer_id or config.GOOGLE_ADS_CUSTOMER_ID

        budget_service = self._client.get_service("CampaignBudgetService")
        campaign_service = self._client.get_service("CampaignService")
        ad_group_service = self._client.get_service("AdGroupService")
        ad_group_criterion_service = self._client.get_service("AdGroupCriterionService")

        # 1. Create budget
        budget_op = self._client.get_type("CampaignBudgetOperation")
        budget = budget_op.create
        budget.name = f"{name}_budget"
        budget.delivery_method = self._client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget.amount_micros = daily_budget_cents * 10_000  # cents -> micros
        budget_response = budget_service.mutate_campaign_budgets(
            customer_id=cid, operations=[budget_op]
        )
        budget_resource = budget_response.results[0].resource_name

        # 2. Create campaign
        campaign_op = self._client.get_type("CampaignOperation")
        campaign = campaign_op.create
        campaign.name = name
        campaign.advertising_channel_type = (
            self._client.enums.AdvertisingChannelTypeEnum.SEARCH
        )
        campaign.status = self._client.enums.CampaignStatusEnum.PAUSED
        campaign.manual_cpc.enhanced_cpc_enabled = False
        campaign.campaign_budget = budget_resource
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = True
        campaign_response = campaign_service.mutate_campaigns(
            customer_id=cid, operations=[campaign_op]
        )
        campaign_resource = campaign_response.results[0].resource_name
        campaign_id = campaign_resource.split("/")[-1]

        # 3. Create ad group
        ag_op = self._client.get_type("AdGroupOperation")
        ag = ag_op.create
        ag.name = f"{name}_adgroup"
        ag.campaign = campaign_resource
        ag.status = self._client.enums.AdGroupStatusEnum.ENABLED
        ag.type_ = self._client.enums.AdGroupTypeEnum.SEARCH_STANDARD
        ag_response = ad_group_service.mutate_ad_groups(
            customer_id=cid, operations=[ag_op]
        )
        ad_group_resource = ag_response.results[0].resource_name
        ad_group_id = ad_group_resource.split("/")[-1]

        # 4. Add keywords
        keyword_ops = []
        for kw in keywords:
            kw_op = self._client.get_type("AdGroupCriterionOperation")
            kw_criterion = kw_op.create
            kw_criterion.ad_group = ad_group_resource
            kw_criterion.status = self._client.enums.AdGroupCriterionStatusEnum.ENABLED
            kw_criterion.keyword.text = kw
            kw_criterion.keyword.match_type = (
                self._client.enums.KeywordMatchTypeEnum.PHRASE
            )
            keyword_ops.append(kw_op)

        if keyword_ops:
            ad_group_criterion_service.mutate_ad_group_criteria(
                customer_id=cid, operations=keyword_ops
            )

        logger.info(
            "Created campaign '%s' (id=%s, ad_group=%s)", name, campaign_id, ad_group_id
        )
        return CreatedCampaign(
            campaign_resource=campaign_resource,
            campaign_id=campaign_id,
            ad_group_resource=ad_group_resource,
            ad_group_id=ad_group_id,
        )

    def create_rsa(
        self,
        ad_group_id: str,
        headlines: list[str],
        descriptions: list[str],
        customer_id: str | None = None,
    ) -> CreatedAd:
        """Create a Responsive Search Ad in the given ad group."""
        cid = customer_id or config.GOOGLE_ADS_CUSTOMER_ID
        ad_group_service = self._client.get_service("AdGroupService")
        ad_group_ad_service = self._client.get_service("AdGroupAdService")

        ad_group_resource = ad_group_service.ad_group_path(cid, ad_group_id)

        ad_op = self._client.get_type("AdGroupAdOperation")
        ad_group_ad = ad_op.create
        ad_group_ad.ad_group = ad_group_resource
        ad_group_ad.status = self._client.enums.AdGroupAdStatusEnum.ENABLED

        rsa = ad_group_ad.ad.responsive_search_ad
        for h in headlines[: config.MAX_HEADLINES]:
            asset = self._client.get_type("AdTextAsset")
            asset.text = h[: config.MAX_HEADLINE_CHARS]
            rsa.headlines.append(asset)
        for d in descriptions[: config.MAX_DESCRIPTIONS]:
            asset = self._client.get_type("AdTextAsset")
            asset.text = d[: config.MAX_DESCRIPTION_CHARS]
            rsa.descriptions.append(asset)

        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=cid, operations=[ad_op]
        )
        ad_resource = response.results[0].resource_name
        ad_id = ad_resource.split("/")[-1]
        logger.info("Created RSA ad_id=%s in ad_group=%s", ad_id, ad_group_id)
        return CreatedAd(
            ad_resource=ad_resource, ad_id=ad_id, ad_group_id=ad_group_id
        )

    def pause_ad(self, ad_group_id: str, ad_id: str, customer_id: str | None = None) -> None:
        """Pause a specific ad."""
        cid = customer_id or config.GOOGLE_ADS_CUSTOMER_ID
        ad_group_ad_service = self._client.get_service("AdGroupAdService")
        ad_group_service = self._client.get_service("AdGroupService")

        ad_group_resource = ad_group_service.ad_group_path(cid, ad_group_id)
        ad_op = self._client.get_type("AdGroupAdOperation")
        ad_group_ad = ad_op.update
        ad_group_ad.resource_name = (
            f"customers/{cid}/adGroupAds/{ad_group_id}~{ad_id}"
        )
        ad_group_ad.status = self._client.enums.AdGroupAdStatusEnum.PAUSED
        ad_op.update_mask.paths.append("status")
        ad_group_ad_service.mutate_ad_group_ads(customer_id=cid, operations=[ad_op])
        logger.info("Paused ad_id=%s in ad_group=%s", ad_id, ad_group_id)

    def enable_ad(self, ad_group_id: str, ad_id: str, customer_id: str | None = None) -> None:
        """Enable a paused ad."""
        cid = customer_id or config.GOOGLE_ADS_CUSTOMER_ID
        ad_group_ad_service = self._client.get_service("AdGroupAdService")
        ad_op = self._client.get_type("AdGroupAdOperation")
        ad_group_ad = ad_op.update
        ad_group_ad.resource_name = (
            f"customers/{cid}/adGroupAds/{ad_group_id}~{ad_id}"
        )
        ad_group_ad.status = self._client.enums.AdGroupAdStatusEnum.ENABLED
        ad_op.update_mask.paths.append("status")
        ad_group_ad_service.mutate_ad_group_ads(customer_id=cid, operations=[ad_op])
        logger.info("Enabled ad_id=%s in ad_group=%s", ad_id, ad_group_id)

    def pause_campaign(self, campaign_id: str, customer_id: str | None = None) -> None:
        """Pause an entire campaign (kill switch action)."""
        cid = customer_id or config.GOOGLE_ADS_CUSTOMER_ID
        campaign_service = self._client.get_service("CampaignService")
        campaign_op = self._client.get_type("CampaignOperation")
        campaign = campaign_op.update
        campaign.resource_name = campaign_service.campaign_path(cid, campaign_id)
        campaign.status = self._client.enums.CampaignStatusEnum.PAUSED
        campaign_op.update_mask.paths.append("status")
        campaign_service.mutate_campaigns(customer_id=cid, operations=[campaign_op])
        logger.warning("KILL SWITCH: Paused campaign_id=%s", campaign_id)

    def get_campaign_metrics(
        self,
        campaign_id: str,
        days_back: int = 7,
        customer_id: str | None = None,
    ) -> CampaignMetrics:
        """Pull aggregate metrics for a campaign over the last N days."""
        cid = customer_id or config.GOOGLE_ADS_CUSTOMER_ID
        ga_service = self._client.get_service("GoogleAdsService")

        end = date.today()
        start = end - timedelta(days=days_back)
        query = f"""
            SELECT
                campaign.id,
                metrics.impressions,
                metrics.clicks,
                metrics.ctr,
                metrics.average_cpc,
                metrics.conversions,
                metrics.cost_micros,
                metrics.all_conversions_value
            FROM campaign
            WHERE campaign.id = {campaign_id}
              AND segments.date BETWEEN '{start}' AND '{end}'
        """
        response = ga_service.search(customer_id=cid, query=query)
        impressions = clicks = cost_micros = conversions = roas_value = 0
        avg_cpc_micros = 0
        for row in response:
            m = row.metrics
            impressions += m.impressions
            clicks += m.clicks
            cost_micros += m.cost_micros
            conversions += m.conversions
            roas_value += m.all_conversions_value
            avg_cpc_micros = int(m.average_cpc)

        ctr = (clicks / impressions) if impressions > 0 else 0.0
        roas = (roas_value / (cost_micros / 1_000_000)) if cost_micros > 0 else 0.0

        return CampaignMetrics(
            campaign_id=campaign_id,
            date_range_start=str(start),
            date_range_end=str(end),
            impressions=impressions,
            clicks=clicks,
            ctr=ctr,
            avg_cpc_cents=avg_cpc_micros // 10_000,
            conversions=conversions,
            cost_cents=cost_micros // 10_000,
            roas=roas,
        )

    def get_ad_performance(
        self,
        ad_group_id: str,
        days_back: int = 7,
        customer_id: str | None = None,
    ) -> list[AdGroupMetrics]:
        """Per-ad metrics within an ad group."""
        cid = customer_id or config.GOOGLE_ADS_CUSTOMER_ID
        ga_service = self._client.get_service("GoogleAdsService")

        end = date.today()
        start = end - timedelta(days=days_back)
        query = f"""
            SELECT
                ad_group_ad.ad.id,
                ad_group.id,
                metrics.impressions,
                metrics.clicks,
                metrics.ctr,
                metrics.conversions,
                metrics.cost_micros
            FROM ad_group_ad
            WHERE ad_group.id = {ad_group_id}
              AND segments.date BETWEEN '{start}' AND '{end}'
        """
        response = ga_service.search(customer_id=cid, query=query)
        results: list[AdGroupMetrics] = []
        for row in response:
            m = row.metrics
            results.append(
                AdGroupMetrics(
                    ad_group_id=str(row.ad_group.id),
                    ad_id=str(row.ad_group_ad.ad.id),
                    impressions=m.impressions,
                    clicks=m.clicks,
                    ctr=m.ctr,
                    conversions=m.conversions,
                    cost_cents=m.cost_micros // 10_000,
                )
            )
        return results


class DryRunAdsClient:
    """Fake AdsClient that logs operations without hitting the API. Use for tests."""

    def __init__(self) -> None:
        logger.info("DryRunAdsClient active — no real API calls will be made")

    def create_campaign(
        self,
        name: str,
        daily_budget_cents: int,
        keywords: list[str],
        customer_id: str | None = None,
    ) -> CreatedCampaign:
        logger.info(
            "[DRY RUN] create_campaign name=%s budget=%d keywords=%s",
            name, daily_budget_cents, keywords,
        )
        return CreatedCampaign(
            campaign_resource="customers/0/campaigns/DRY_CAMPAIGN",
            campaign_id="DRY_CAMPAIGN",
            ad_group_resource="customers/0/adGroups/DRY_ADGROUP",
            ad_group_id="DRY_ADGROUP",
        )

    def create_rsa(
        self,
        ad_group_id: str,
        headlines: list[str],
        descriptions: list[str],
        customer_id: str | None = None,
    ) -> CreatedAd:
        logger.info(
            "[DRY RUN] create_rsa ad_group=%s headlines=%d descriptions=%d",
            ad_group_id, len(headlines), len(descriptions),
        )
        return CreatedAd(
            ad_resource="customers/0/adGroupAds/DRY_ADGROUP~DRY_AD",
            ad_id="DRY_AD",
            ad_group_id=ad_group_id,
        )

    def pause_ad(self, ad_group_id: str, ad_id: str, customer_id: str | None = None) -> None:
        logger.info("[DRY RUN] pause_ad ad_id=%s", ad_id)

    def enable_ad(self, ad_group_id: str, ad_id: str, customer_id: str | None = None) -> None:
        logger.info("[DRY RUN] enable_ad ad_id=%s", ad_id)

    def pause_campaign(self, campaign_id: str, customer_id: str | None = None) -> None:
        logger.warning("[DRY RUN] KILL SWITCH: pause_campaign campaign_id=%s", campaign_id)

    def get_campaign_metrics(
        self,
        campaign_id: str,
        days_back: int = 7,
        customer_id: str | None = None,
    ) -> CampaignMetrics:
        logger.info("[DRY RUN] get_campaign_metrics campaign_id=%s", campaign_id)
        return CampaignMetrics(
            campaign_id=campaign_id,
            date_range_start=str(date.today() - timedelta(days=days_back)),
            date_range_end=str(date.today()),
            impressions=500,
            clicks=25,
            ctr=0.05,
            avg_cpc_cents=80,
            conversions=2.0,
            cost_cents=2000,
            roas=3.5,
        )

    def get_ad_performance(
        self,
        ad_group_id: str,
        days_back: int = 7,
        customer_id: str | None = None,
    ) -> list[AdGroupMetrics]:
        logger.info("[DRY RUN] get_ad_performance ad_group=%s", ad_group_id)
        return [
            AdGroupMetrics(
                ad_group_id=ad_group_id,
                ad_id="DRY_AD",
                impressions=500,
                clicks=25,
                ctr=0.05,
                conversions=2.0,
                cost_cents=2000,
            )
        ]
