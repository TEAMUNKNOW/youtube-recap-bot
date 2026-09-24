"""Request middleware helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from bot.config import get_settings
from bot.database.models import User
from bot.database.session import get_session

logger = logging.getLogger(__name__)


async def ensure_user(telegram_id: int) -> User:
    settings = get_settings()
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                is_owner=settings.is_owner(telegram_id),
                is_admin=settings.is_authorized(telegram_id),
                last_seen=datetime.now(timezone.utc),
            )
            session.add(user)
            await session.flush()
        else:
            user.last_seen = datetime.now(timezone.utc)
            user.is_owner = settings.is_owner(telegram_id)
            user.is_admin = settings.is_authorized(telegram_id)
        return user
