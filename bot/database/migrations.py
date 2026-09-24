"""Lightweight schema helpers (create_all is primary; this supports upgrades)."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def ensure_schema(engine: AsyncEngine) -> None:
    """Apply any incremental schema patches for existing deployments."""
    async with engine.begin() as conn:
        try:
            await conn.execute(
                text(
                    "ALTER TABLE tasks ADD COLUMN heartbeat_at DATETIME"
                )
            )
            logger.info("Added heartbeat_at column to tasks")
        except Exception:
            pass

        try:
            await conn.execute(
                text(
                    "ALTER TABLE tasks ADD COLUMN rights_acknowledged BOOLEAN DEFAULT 0"
                )
            )
        except Exception:
            pass

        try:
            await conn.execute(
                text("ALTER TABLE tasks ADD COLUMN ui_state VARCHAR(64)")
            )
        except Exception:
            pass
