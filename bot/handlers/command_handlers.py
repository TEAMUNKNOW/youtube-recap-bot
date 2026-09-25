"""Telegram command handlers."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.database.models import Task, TaskStatus
from bot.database.session import get_session
from bot.filters import authorized_users_filter
from bot.middleware import ensure_user
from bot.states import CB_MENU_HELP

logger = logging.getLogger(__name__)


def register_command_handlers(app: Client) -> None:
    # authorized_users_filter is a factory that takes no arguments.
    auth = authorized_users_filter()

    @app.on_message(filters.command("start") & auth)
    async def start_cmd(client: Client, message: Message) -> None:
        await ensure_user(message.from_user.id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Create Recap", callback_data="menu:create")],
            [InlineKeyboardButton("🎬 Shorts Factory", callback_data="sf:menu")],
            [InlineKeyboardButton("🔗 YouTube URL", callback_data="menu:url"), InlineKeyboardButton("📤 Upload Video", callback_data="menu:upload")],
            [InlineKeyboardButton("📊 My Tasks", callback_data="menu:tasks"), InlineKeyboardButton("❓ Help", callback_data=CB_MENU_HELP)],
        ])
        await message.reply_text(
            "👋 <b>YouTube Recap & Video Automation</b>\n\n"
            "🎬 Upload a video or send a YouTube URL.\n"
            "🧠 AI analyzes the story and important scenes.\n"
            "🎙️ Natural cinematic narration is generated with duration-scaled coverage.\n"
            "🎞️ Video, narration and subtitles are checked before delivery.\n"
            "🖼️ Thumbnail is selected from story/chapter moments.\n\n"
            "Choose an option below to begin.",
            reply_markup=kb,
        )

    @app.on_message(filters.command("help") & auth)
    async def help_cmd(client: Client, message: Message) -> None:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 How It Works", callback_data="h:how")],
            [InlineKeyboardButton("🎙️ Voice System", callback_data="h:voice")],
            [InlineKeyboardButton("🎞️ Scene Sync", callback_data="h:sync")],
            [InlineKeyboardButton("🖼️ Thumbnail", callback_data="h:thumb")],
            [InlineKeyboardButton("📦 Output & Raw Files", callback_data="h:out")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="h:set")],
        ])
        await message.reply_text(
            "<b>❓ Help Center</b>\n\n"
            "The bot converts long-form video into a structured story recap with narration, subtitles, sync checks and a story-based thumbnail.\n\n"
            "Use the buttons below for details.",
            reply_markup=kb,
        )

    @app.on_message(filters.command("status") & auth)
    async def status_cmd(client: Client, message: Message) -> None:
        user = await ensure_user(message.from_user.id)
        async with get_session() as session:
            result = await session.execute(
                select(Task)
                .where(
                    Task.user_id == user.id,
                    Task.status.notin_(
                        [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
                    ),
                )
                .order_by(Task.id.desc())
                .limit(10)
            )
            tasks = result.scalars().all()
        if not tasks:
            await message.reply_text("No active tasks.")
            return
        lines = ["<b>Active tasks</b>"]
        for t in tasks:
            lines.append(f"#{t.id} {t.status.value} {t.progress:.0f}%")
        await message.reply_text("\n".join(lines))

    @app.on_message(filters.command("tasks") & auth)
    async def tasks_cmd(client: Client, message: Message) -> None:
        user = await ensure_user(message.from_user.id)
        async with get_session() as session:
            result = await session.execute(
                select(Task)
                .where(Task.user_id == user.id)
                .order_by(Task.id.desc())
                .limit(15)
            )
            tasks = result.scalars().all()
        if not tasks:
            await message.reply_text("No tasks yet.")
            return
        lines = ["<b>Recent tasks</b>"]
        for t in tasks:
            lines.append(f"#{t.id} {t.status.value} {t.progress:.0f}%")
        await message.reply_text("\n".join(lines))

    @app.on_message(filters.command("cancel") & auth)
    async def cancel_cmd(client: Client, message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.reply_text("Usage: /cancel &lt;task_id&gt;")
            return
        task_id = int(parts[1])
        user = await ensure_user(message.from_user.id)
        async with get_session() as session:
            task = await session.get(Task, task_id)
            if task is None or task.user_id != user.id:
                await message.reply_text("Task not found.")
                return
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                await message.reply_text(f"Task already {task.status.value}.")
                return
            task.status = TaskStatus.CANCELLED
            task.error_code = "CANCELLED"
            task.error_message = "Cancelled by user"
            await session.commit()
        await message.reply_text(f"Task #{task_id} cancelled.")
