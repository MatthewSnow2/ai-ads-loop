# AI Google Ads Loop

Autonomous Google Ads optimization loop. Claude generates Responsive Search Ad
(RSA) copy, serves it via the Google Ads API, evaluates real performance, and
promotes the winners — with a human-in-the-loop approval gate and an automatic
spend kill-switch guarding every run.

## Safety model

This tool spends real ad money, so safety is built into the control flow:

- **HIL gate on generation 1 is mandatory** — copy is never served without
  explicit human approval on the first round.
- **Kill-switch is sacred** — if spend exceeds the configured threshold, the
  loop pauses campaigns before doing anything else.
- **Budgets are never modified** by the loop — they're set once, out of band.
- **Dry-run first** — `--dry-run` exercises the full pipeline with no API calls.

## Install

```bash
pip install -r requirements.txt
```

## Configure

All credentials are read from environment variables (the author loads them from
a shared dotenv; a `.env` in the project root works too). Copy the example and
fill in your values:

```bash
cp .env.example .env   # then edit
```

Required variables:

```
GOOGLE_ADS_CUSTOMER_ID
GOOGLE_ADS_DEVELOPER_TOKEN
GOOGLE_ADS_CLIENT_ID
GOOGLE_ADS_CLIENT_SECRET
GOOGLE_ADS_REFRESH_TOKEN
GOOGLE_ADS_LOGIN_CUSTOMER_ID
GOOGLE_ADS_TEST_ACCOUNT_ID   # safe sandbox account — set before any real run
ANTHROPIC_API_KEY
```

## Run

```bash
python -m ai_ads_loop.runner --dry-run     # test without API calls
python -m ai_ads_loop.runner --status      # show campaign status
python -m ai_ads_loop.runner               # live run
```

## Architecture

| Module | Responsibility |
|--------|----------------|
| `config.py` | All settings; loads credentials from the environment |
| `repository.py` | All DB access (SQLite), `Repository` singleton |
| `ads_client.py` | Google Ads API wrapper + `DryRunAdsClient` for testing |
| `copy_generator.py` | Claude generates headlines + descriptions |
| `evaluator.py` | Compares generations on CTR / conversions / ROAS |
| `kill_switch.py` | Auto-pauses if spend exceeds threshold |
| `runner.py` | Main orchestrator; run daily via cron |

## Conventions

- All DB access goes through `repository.py` — never import `sqlite3` elsewhere.
- RSA limits follow Google's hard caps (15 headlines, 4 descriptions, 30/90 chars).

## License

MIT — see [LICENSE](LICENSE).
