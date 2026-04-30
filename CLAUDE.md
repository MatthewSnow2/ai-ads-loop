# AI Google Ads Loop

Autonomous ad optimization loop. Generates RSA copy with Claude, serves via Google Ads API, evaluates performance, promotes winners.

## Run

```bash
cd /home/apexaipc/projects/ai-ads-loop
source ~/.env.shared
python -m ai_ads_loop.runner --dry-run     # test without API calls
python -m ai_ads_loop.runner --status      # show campaign status
python -m ai_ads_loop.runner               # live run
```

## Architecture

- `config.py` — all settings, loads credentials from ~/.env.shared
- `repository.py` — all DB access (SQLite), Repository singleton
- `ads_client.py` — Google Ads API wrapper + DryRunAdsClient for testing
- `copy_generator.py` — Claude generates headlines + descriptions
- `evaluator.py` — compares generations on CTR/conversions/ROAS
- `kill_switch.py` — auto-pauses if spend exceeds threshold
- `runner.py` — main orchestrator, run daily via cron

## Rules

- All DB access through `repository.py` — never import sqlite3 directly in other modules
- Never touch campaign budgets in the loop — budgets are set once and never modified
- Kill switch is sacred — if triggered, always pause before anything else
- HIL gate on generation 1 is mandatory — never skip for any reason
- Test on dry-run / test account before any real spend
- `GOOGLE_ADS_TEST_ACCOUNT_ID` in ~/.env.shared is the safe sandbox

## Credentials (all in ~/.env.shared)

```
GOOGLE_ADS_CUSTOMER_ID
GOOGLE_ADS_DEVELOPER_TOKEN
GOOGLE_ADS_CLIENT_ID
GOOGLE_ADS_CLIENT_SECRET
GOOGLE_ADS_REFRESH_TOKEN
GOOGLE_ADS_LOGIN_CUSTOMER_ID
GOOGLE_ADS_TEST_ACCOUNT_ID   # TODO: fill this in before running
ANTHROPIC_API_KEY
```

## Before First Run

1. Fill in `GOOGLE_ADS_TEST_ACCOUNT_ID` in `~/.env.shared`
2. Run `python -m ai_ads_loop.runner --dry-run` to verify config loads
3. Create first campaign via Python (or add manually and set google_campaign_id/google_ad_group_id in DB)
4. HIL gate will prompt for copy approval on first run

## Data locations

- DB: `data/ai_ads_loop.db`
- Plan: `~/vault/projects/ai-google-ads-loop/PLAN.md`
