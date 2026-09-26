"""Telegram command handlers."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot.database.models import Task, TaskStatus
from bot.database.session import get_session
from bot.filters import authorized_users_filter
from bot.middleware import ensure_user

logger = logging.getLogger(__name__)


def register_command_handlers(app: Client) -> None:
    auth = authorized_users_filter(app)

    @app.on_message(filters.command("start") & auth)
    async def start_cmd(client: Client, message: Message) -> None:
        await ensure_user(message.from_user.id)
        await message.reply_text(
            "👋 <b>YouTube Recap Bot</b>\n\n"
            "Send a YouTube URL or upload a video file to begin.\n"
            "Commands: /status /tasks /logs /cancel /help",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 Create Recap", callback_data="menu:create")],
            ]),
        )

    @app.on_message(filters.command("help") & auth)
    async def help_cmd(client: Client, message: Message) -> None:
        await message.reply_text(
            "<b>Help</b>\n"
            "• Send a YouTube link or upload a video\n"
            "• Choose mode, TTS, language, export\n"
            "• /status — active tasks\n"
            "• /tasks — recent tasks\n"
            "• /logs — recent task errors\n"
            "• /cancel &lt;id&gt; — cancel a task\n"
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

    @app.on_message(filters.command("logs") & auth)
    async def logs_cmd(client: Client, message: Message) -> None:
        """Show recent task errors / status."""
        user = await ensure_user(message.from_user.id)
        settings = None
        try:
            from bot.config import get_settings
            settings = get_settings()
        except Exception:
            pass
        owner_ids = set(getattr(settings, "owner_ids", []) or []) if settings else set()
        is_owner = message.from_user.id in owner_ids

        async with get_session() as session:
            q = select(Task).order_by(Task.id.desc()).limit(15 if is_owner else 10)
            if not is_owner:
                q = q.where(Task.user_id == user.id)
            result = await session.execute(q)
            tasks = result.scalars().all()

        if not tasks:
            await message.reply_text("No task logs yet.")
            return

        lines = ["<b>📋 Recent task logs</b>\n"]
        for t in tasks:
            st = t.status.value if hasattr(t.status, "value") else str(t.status)
            err = (t.error_message or t.error_code or "").strip()
            if len(err) > 120:
                err = err[:117] + "..."
            line = f"#{t.id} · <b>{st}</b> · {t.progress:.0f}%"
            if err:
                line += f"\n   ⚠️ <code>{err}</code>"
            elif t.output_video_path:
                line += " · ✅ output ready"
            lines.append(line)
        lines.append("\nUse /status for active tasks, /tasks for history.")
        text = "\n".join(lines)
        if len(text) > 3500:
            text = text[:3490] + "\n…"
        await message.reply_text(text)
