"""Global error handlers."""

from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.types import Update

from bot.exceptions import AuthError, BotError

logger = logging.getLogger(__name__)


def register_error_handlers(app: Client) -> None:
    @app.on_message()
    async def unauthorized_catch(client: Client, message) -> None:
        pass

    async def global_error_handler(client: Client, update: Update, exception: Exception) -> None:
        if isinstance(exception, AuthError):
            return
        if isinstance(exception, BotError):
            logger.warning("BotError: %s", exception.message)
            return
        logger.exception("Unhandled error on update: %s", exception)

    app.global_error_handler = global_error_handler  # type: ignore[attr-defined]
