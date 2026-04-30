"""Configuration for the AI Google Ads optimization loop.

All credentials loaded from ~/.env.shared. Never create separate .env files.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load shared credentials
load_dotenv(os.path.expanduser("~/.env.shared"))

# ---------------------------------------------------------------------------
# Google Ads credentials
# ---------------------------------------------------------------------------
GOOGLE_ADS_CUSTOMER_ID: str = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "")
GOOGLE_ADS_DEVELOPER_TOKEN: str = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
GOOGLE_ADS_CLIENT_ID: str = os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
GOOGLE_ADS_CLIENT_SECRET: str = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")
GOOGLE_ADS_REFRESH_TOKEN: str = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "")
GOOGLE_ADS_LOGIN_CUSTOMER_ID: str = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")

# TODO: Matthew — fill in your test account ID before running
TEST_ACCOUNT_ID: str = os.environ.get("GOOGLE_ADS_TEST_ACCOUNT_ID", "TODO_FILL_IN")

# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# ---------------------------------------------------------------------------
# Campaign behaviour
# ---------------------------------------------------------------------------
DAILY_BUDGET_CENTS: int = 500  # $5.00/day default
KILL_SWITCH_THRESHOLD_PCT: float = 150.0  # Pause if spend hits 150% of expected
MIN_IMPRESSIONS_FOR_EVAL: int = 1000
MIN_CLICKS_FOR_EVAL: int = 100
MUTATION_COOLDOWN_HOURS: int = 48
IMPROVEMENT_THRESHOLD: float = 0.15  # 15% CTR or ROAS improvement required to promote

# ---------------------------------------------------------------------------
# RSA limits (Google's hard caps)
# ---------------------------------------------------------------------------
MAX_HEADLINES: int = 15
MAX_DESCRIPTIONS: int = 4
MAX_HEADLINE_CHARS: int = 30
MAX_DESCRIPTION_CHARS: int = 90

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent
DB_PATH: Path = PROJECT_ROOT / "data" / "ai_ads_loop.db"
