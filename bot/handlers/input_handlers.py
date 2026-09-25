"""URL and media upload handlers."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import get_settings
from bot.database.models import SourceType, Task, TaskStatus
from bot.database.session import get_session
from bot.filters import authorized_users_filter
from bot.middleware import ensure_user
from bot.states import CB_RIGHTS_ACK, UIState
from core.cleanup import task_workspace
from core.downloader import validate_url
from bot.exceptions import InputError

logger = logging.getLogger(__name__)
auth = authorized_users_filter()

URL_RE = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)


def register_input_handlers(app: Client) -> None:
    @app.on_message(filters.text & auth & ~filters.command([]))
    async def text_input(client: Client, message: Message) -> None:
        text = (message.text or "").strip()
        if text.startswith("/"):
            return
        m = URL_RE.search(text)
        if not m:
            return
        url = m.group(0)
        try:
            validate_url(url)
        except InputError as exc:
            await message.reply_text(exc.user_message)
            return

        user = await ensure_user(message.from_user.id)
        settings = get_settings()

        async with get_session() as session:
            task = Task(
                user_id=user.id,
                source_type=SourceType.YOUTUBE
                if "youtu" in url.lower()
                else SourceType.DIRECT_URL,
                source_url=url,
                status=TaskStatus.QUEUED,
                ui_state=UIState.AWAITING_RIGHTS.value,
                chat_id=message.chat.id,
                priority=1 if settings.is_owner(message.from_user.id) else 2,
            )
            session.add(task)
            await session.flush()
            task_id = task.id

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ I have rights to process this content",
                        callback_data=f"{CB_RIGHTS_ACK}:{task_id}",
                    )
                ],
                [InlineKeyboardButton("🏠 Back to Home", callback_data="menu:home")],
            ]
        )
        await message.reply_text(
            f"📋 Task <code>#{task_id}</code> created.\n\n"
            "Before processing, confirm you own the rights or have authorization "
            "to create a transformative recap/edit of this media.\n\n"
            "This system does <b>not</b> bypass copyright enforcement.",
            reply_markup=kb,
        )

    @app.on_message((filters.video | filters.document) & auth)
    async def media_upload(client: Client, message: Message) -> None:
        settings = get_settings()
        user = await ensure_user(message.from_user.id)

        media = message.video or message.document
        if media is None:
            return
        size = getattr(media, "file_size", 0) or 0
        if size > settings.max_input_bytes():
            await message.reply_text("File exceeds maximum allowed size.")
            return

        async with get_session() as session:
            task = Task(
                user_id=user.id,
                source_type=SourceType.TELEGRAM_UPLOAD,
                status=TaskStatus.QUEUED,
                ui_state=UIState.AWAITING_RIGHTS.value,
                chat_id=message.chat.id,
                input_size=size,
                priority=1 if settings.is_owner(message.from_user.id) else 2,
            )
            session.add(task)
            await session.flush()
            task_id = task.id

        ws = task_workspace(settings, task_id)
        status = await message.reply_text("⬇️ Downloading upload…")
        try:
            path = await message.download(file_name=str(ws / "upload_input"))
            path_obj = Path(path)
            async with get_session() as session:
                from sqlalchemy import update

                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(source_file=str(path_obj), status_message_id=status.id)
                )
        except Exception as exc:
            logger.error("Upload download failed: %s", exc)
            await status.edit_text("Failed to download the uploaded file.")
            return

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ I have rights to process this content",
                        callback_data=f"{CB_RIGHTS_ACK}:{task_id}",
                    )
                ],
                [InlineKeyboardButton("🏠 Back to Home", callback_data="menu:home")],
            ]
        )
        await status.edit_text(
            f"📋 Task <code>#{task_id}</code> — upload received.\n\n"
            "Confirm you have rights to process this media.",
            reply_markup=kb,
        )
