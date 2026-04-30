"""Repository — single access point for all DB operations.

All database access goes through the Repository singleton. Never import
aiosqlite directly outside this module.

Mirrors the ledger.py pattern from auto_research with async-first design
and sync wrappers for use in synchronous runner contexts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from .config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_campaign_id TEXT NOT NULL,
    google_ad_group_id TEXT NOT NULL,
    name TEXT NOT NULL,
    daily_budget_cents INTEGER NOT NULL DEFAULT 500,
    status TEXT NOT NULL DEFAULT 'active',
    kill_switch_threshold_pct REAL NOT NULL DEFAULT 150.0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    generation_number INTEGER NOT NULL,
    headlines TEXT NOT NULL,
    descriptions TEXT NOT NULL,
    keywords TEXT NOT NULL,
    rationale TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    applied_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    generation_id INTEGER REFERENCES generations(id),
    snapshot_date TEXT NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    ctr REAL NOT NULL DEFAULT 0.0,
    avg_cpc_cents INTEGER NOT NULL DEFAULT 0,
    conversions REAL NOT NULL DEFAULT 0.0,
    cost_cents INTEGER NOT NULL DEFAULT 0,
    roas REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    baseline_generation_id INTEGER NOT NULL REFERENCES generations(id),
    variant_generation_id INTEGER NOT NULL REFERENCES generations(id),
    metric_compared TEXT NOT NULL DEFAULT 'ctr',
    baseline_value REAL,
    variant_value REAL,
    improvement_pct REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    min_impressions_reached INTEGER NOT NULL DEFAULT 0,
    winner TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kill_switch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    trigger_reason TEXT NOT NULL,
    metric_value REAL NOT NULL,
    threshold_value REAL NOT NULL,
    action_taken TEXT NOT NULL DEFAULT 'paused',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_generations_campaign ON generations(campaign_id);
CREATE INDEX IF NOT EXISTS idx_metrics_campaign_date ON metrics_snapshots(campaign_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_experiments_campaign ON experiments(campaign_id);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_kill_events_campaign ON kill_switch_events(campaign_id);
"""


class Repository:
    """Singleton repository for all AI Ads Loop database access."""

    _instance: Repository | None = None

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get(cls, db_path: Path | None = None) -> Repository:
        """Return (or create) the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(db_path)
        return cls._instance

    # ------------------------------------------------------------------
    # Async core helpers
    # ------------------------------------------------------------------

    async def _execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        fetch_one: bool = False,
        fetch_all: bool = False,
    ) -> Any:
        """Execute a single SQL statement, optionally returning rows."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            await db.commit()
            if fetch_one:
                return await cursor.fetchone()
            if fetch_all:
                return await cursor.fetchall()
            return cursor.lastrowid

    async def _executescript(self, script: str) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.executescript(script)
            await db.commit()

    # ------------------------------------------------------------------
    # Sync wrappers (for use in synchronous runner contexts)
    # ------------------------------------------------------------------

    def _run(self, coro: Any) -> Any:
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Inside an async context — caller should await directly
                raise RuntimeError(
                    "Cannot use sync wrapper inside a running event loop. "
                    "Use the async methods directly."
                )
            return loop.run_until_complete(coro)
        except RuntimeError as exc:
            if "no current event loop" in str(exc).lower():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            raise

    # ------------------------------------------------------------------
    # Schema init
    # ------------------------------------------------------------------

    async def init_db_async(self) -> None:
        """Create tables and indexes if they don't exist."""
        await self._executescript(SCHEMA)
        logger.info("Database initialized at %s", self._db_path)

    def init_db(self) -> None:
        """Sync wrapper for init_db_async."""
        self._run(self.init_db_async())

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------

    async def get_active_campaigns_async(self) -> list[dict[str, Any]]:
        """Return all campaigns with status='active'."""
        rows = await self._execute(
            "SELECT * FROM campaigns WHERE status = 'active' ORDER BY created_at",
            fetch_all=True,
        )
        return [dict(r) for r in (rows or [])]

    def get_active_campaigns(self) -> list[dict[str, Any]]:
        return self._run(self.get_active_campaigns_async())

    async def create_campaign_async(
        self,
        *,
        google_campaign_id: str,
        google_ad_group_id: str,
        name: str,
        daily_budget_cents: int = 500,
        kill_switch_threshold_pct: float = 150.0,
    ) -> int:
        """Insert a new campaign row. Returns the new campaign id."""
        return await self._execute(
            """INSERT INTO campaigns
               (google_campaign_id, google_ad_group_id, name, daily_budget_cents,
                status, kill_switch_threshold_pct, created_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?)""",
            (
                google_campaign_id,
                google_ad_group_id,
                name,
                daily_budget_cents,
                kill_switch_threshold_pct,
                datetime.utcnow().isoformat(),
            ),
        )

    def create_campaign(self, **kwargs: Any) -> int:
        return self._run(self.create_campaign_async(**kwargs))

    async def update_campaign_status_async(self, campaign_id: int, status: str) -> None:
        await self._execute(
            "UPDATE campaigns SET status = ? WHERE id = ?",
            (status, campaign_id),
        )

    def update_campaign_status(self, campaign_id: int, status: str) -> None:
        self._run(self.update_campaign_status_async(campaign_id, status))

    # ------------------------------------------------------------------
    # Generations
    # ------------------------------------------------------------------

    async def create_generation_async(
        self,
        *,
        campaign_id: int,
        generation_number: int,
        headlines: list[str],
        descriptions: list[str],
        keywords: list[str],
        rationale: str | None = None,
        approved: bool = False,
    ) -> int:
        """Insert a new generation. Returns the new generation id."""
        return await self._execute(
            """INSERT INTO generations
               (campaign_id, generation_number, headlines, descriptions, keywords,
                rationale, approved, applied_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                campaign_id,
                generation_number,
                json.dumps(headlines),
                json.dumps(descriptions),
                json.dumps(keywords),
                rationale,
                int(approved),
                datetime.utcnow().isoformat(),
            ),
        )

    def create_generation(self, **kwargs: Any) -> int:
        return self._run(self.create_generation_async(**kwargs))

    async def approve_generation_async(self, generation_id: int) -> None:
        """Mark a generation as HIL-approved."""
        await self._execute(
            "UPDATE generations SET approved = 1 WHERE id = ?",
            (generation_id,),
        )

    def approve_generation(self, generation_id: int) -> None:
        self._run(self.approve_generation_async(generation_id))

    async def mark_generation_applied_async(self, generation_id: int) -> None:
        """Record that this generation was pushed to Google Ads."""
        await self._execute(
            "UPDATE generations SET applied_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), generation_id),
        )

    def mark_generation_applied(self, generation_id: int) -> None:
        self._run(self.mark_generation_applied_async(generation_id))

    async def get_latest_generation_async(
        self, campaign_id: int
    ) -> dict[str, Any] | None:
        """Return the most recently created generation for a campaign."""
        row = await self._execute(
            """SELECT * FROM generations
               WHERE campaign_id = ?
               ORDER BY generation_number DESC LIMIT 1""",
            (campaign_id,),
            fetch_one=True,
        )
        if row is None:
            return None
        d = dict(row)
        d["headlines"] = json.loads(d["headlines"])
        d["descriptions"] = json.loads(d["descriptions"])
        d["keywords"] = json.loads(d["keywords"])
        return d

    def get_latest_generation(self, campaign_id: int) -> dict[str, Any] | None:
        return self._run(self.get_latest_generation_async(campaign_id))

    async def get_generation_async(self, generation_id: int) -> dict[str, Any] | None:
        row = await self._execute(
            "SELECT * FROM generations WHERE id = ?",
            (generation_id,),
            fetch_one=True,
        )
        if row is None:
            return None
        d = dict(row)
        d["headlines"] = json.loads(d["headlines"])
        d["descriptions"] = json.loads(d["descriptions"])
        d["keywords"] = json.loads(d["keywords"])
        return d

    def get_generation(self, generation_id: int) -> dict[str, Any] | None:
        return self._run(self.get_generation_async(generation_id))

    # ------------------------------------------------------------------
    # Metrics snapshots
    # ------------------------------------------------------------------

    async def record_metrics_snapshot_async(
        self,
        *,
        campaign_id: int,
        generation_id: int | None,
        snapshot_date: date | str,
        impressions: int,
        clicks: int,
        ctr: float,
        avg_cpc_cents: int,
        conversions: float,
        cost_cents: int,
        roas: float,
    ) -> int:
        snap_date = (
            snapshot_date.isoformat()
            if isinstance(snapshot_date, date)
            else snapshot_date
        )
        return await self._execute(
            """INSERT INTO metrics_snapshots
               (campaign_id, generation_id, snapshot_date, impressions, clicks,
                ctr, avg_cpc_cents, conversions, cost_cents, roas, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                campaign_id,
                generation_id,
                snap_date,
                impressions,
                clicks,
                ctr,
                avg_cpc_cents,
                conversions,
                cost_cents,
                roas,
                datetime.utcnow().isoformat(),
            ),
        )

    def record_metrics_snapshot(self, **kwargs: Any) -> int:
        return self._run(self.record_metrics_snapshot_async(**kwargs))

    async def get_generation_metrics_async(
        self, generation_id: int
    ) -> list[dict[str, Any]]:
        """Return all snapshots for a generation, oldest first."""
        rows = await self._execute(
            """SELECT * FROM metrics_snapshots
               WHERE generation_id = ?
               ORDER BY snapshot_date ASC""",
            (generation_id,),
            fetch_all=True,
        )
        return [dict(r) for r in (rows or [])]

    def get_generation_metrics(self, generation_id: int) -> list[dict[str, Any]]:
        return self._run(self.get_generation_metrics_async(generation_id))

    async def get_latest_metrics_async(
        self, campaign_id: int
    ) -> dict[str, Any] | None:
        """Return the most recent snapshot for a campaign."""
        row = await self._execute(
            """SELECT * FROM metrics_snapshots
               WHERE campaign_id = ?
               ORDER BY snapshot_date DESC LIMIT 1""",
            (campaign_id,),
            fetch_one=True,
        )
        return dict(row) if row else None

    def get_latest_metrics(self, campaign_id: int) -> dict[str, Any] | None:
        return self._run(self.get_latest_metrics_async(campaign_id))

    # ------------------------------------------------------------------
    # Experiments
    # ------------------------------------------------------------------

    async def create_experiment_async(
        self,
        *,
        campaign_id: int,
        baseline_generation_id: int,
        variant_generation_id: int,
        metric_compared: str = "ctr",
    ) -> int:
        return await self._execute(
            """INSERT INTO experiments
               (campaign_id, baseline_generation_id, variant_generation_id,
                metric_compared, status, min_impressions_reached, created_at)
               VALUES (?, ?, ?, ?, 'collecting', 0, ?)""",
            (
                campaign_id,
                baseline_generation_id,
                variant_generation_id,
                metric_compared,
                datetime.utcnow().isoformat(),
            ),
        )

    def create_experiment(self, **kwargs: Any) -> int:
        return self._run(self.create_experiment_async(**kwargs))

    async def update_experiment_status_async(
        self,
        experiment_id: int,
        *,
        status: str,
        baseline_value: float | None = None,
        variant_value: float | None = None,
        improvement_pct: float | None = None,
        min_impressions_reached: bool | None = None,
        winner: str | None = None,
        notes: str | None = None,
    ) -> None:
        fields: list[str] = ["status = ?"]
        values: list[Any] = [status]

        if baseline_value is not None:
            fields.append("baseline_value = ?")
            values.append(baseline_value)
        if variant_value is not None:
            fields.append("variant_value = ?")
            values.append(variant_value)
        if improvement_pct is not None:
            fields.append("improvement_pct = ?")
            values.append(improvement_pct)
        if min_impressions_reached is not None:
            fields.append("min_impressions_reached = ?")
            values.append(int(min_impressions_reached))
        if winner is not None:
            fields.append("winner = ?")
            values.append(winner)
        if notes is not None:
            fields.append("notes = ?")
            values.append(notes)

        values.append(experiment_id)
        await self._execute(
            f"UPDATE experiments SET {', '.join(fields)} WHERE id = ?",
            tuple(values),
        )

    def update_experiment_status(self, experiment_id: int, **kwargs: Any) -> None:
        self._run(self.update_experiment_status_async(experiment_id, **kwargs))

    async def get_active_experiment_async(
        self, campaign_id: int
    ) -> dict[str, Any] | None:
        """Return the currently running experiment for a campaign, if any."""
        row = await self._execute(
            """SELECT * FROM experiments
               WHERE campaign_id = ? AND status IN ('pending', 'collecting')
               ORDER BY created_at DESC LIMIT 1""",
            (campaign_id,),
            fetch_one=True,
        )
        return dict(row) if row else None

    def get_active_experiment(self, campaign_id: int) -> dict[str, Any] | None:
        return self._run(self.get_active_experiment_async(campaign_id))

    async def get_last_evaluated_experiment_async(
        self, campaign_id: int
    ) -> dict[str, Any] | None:
        """Return the most recently evaluated/promoted experiment for cooldown tracking."""
        row = await self._execute(
            """SELECT * FROM experiments
               WHERE campaign_id = ? AND status IN ('evaluated', 'promoted', 'rolled_back')
               ORDER BY created_at DESC LIMIT 1""",
            (campaign_id,),
            fetch_one=True,
        )
        return dict(row) if row else None

    def get_last_evaluated_experiment(self, campaign_id: int) -> dict[str, Any] | None:
        return self._run(self.get_last_evaluated_experiment_async(campaign_id))

    # ------------------------------------------------------------------
    # Kill switch events
    # ------------------------------------------------------------------

    async def log_kill_switch_event_async(
        self,
        *,
        campaign_id: int,
        trigger_reason: str,
        metric_value: float,
        threshold_value: float,
        action_taken: str = "paused",
    ) -> int:
        return await self._execute(
            """INSERT INTO kill_switch_events
               (campaign_id, trigger_reason, metric_value, threshold_value,
                action_taken, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                campaign_id,
                trigger_reason,
                metric_value,
                threshold_value,
                action_taken,
                datetime.utcnow().isoformat(),
            ),
        )

    def log_kill_switch_event(self, **kwargs: Any) -> int:
        return self._run(self.log_kill_switch_event_async(**kwargs))
