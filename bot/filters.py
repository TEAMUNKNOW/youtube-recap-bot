"""Authorization and input filters."""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message, CallbackQuery

from bot.config import get_settings


def authorized_users_filter():
    """Only allow configured owners/admins."""

    async def func(_, __, update: Message | CallbackQuery) -> bool:
        settings = get_settings()
        user = update.from_user
        if user is None:
            return False
        return settings.is_authorized(user.id)

    return filters.create(func)


def owner_filter():
    async def func(_, __, update: Message | CallbackQuery) -> bool:
        settings = get_settings()
        user = update.from_user
        if user is None:
            return False
        return settings.is_owner(user.id)

    return filters.create(func)
