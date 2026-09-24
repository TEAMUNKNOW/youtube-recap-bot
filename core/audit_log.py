"""Audit logging to database."""

from __future__ import annotations

import logging
from typing import Any, Optional

from bot.database.models import AuditLog
from bot.database.session import get_session

logger = logging.getLogger(__name__)


async def audit(
    action: str,
    *,
    user_id: Optional[int] = None,
    task_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    try:
        async with get_session() as session:
            entry = AuditLog(
                user_id=user_id,
                task_id=task_id,
                action=action,
                metadata_json=metadata,
            )
            session.add(entry)
    except Exception as exc:
        logger.warning("Audit log write failed: %s", exc)
