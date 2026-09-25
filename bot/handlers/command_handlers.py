"""Telegram command handlers."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from sqlalchemy import select

from bot.config import get_settings
from bot.database.models import Task, TaskStatus
from bot.database.session import get_session
from bot.filters import authorized_users_filter
from bot.middleware import ensure_user

logger = logging.getLogger(__name__)
auth = authorized_users_filter()


def register_command_handlers(app: Client) -> None:
    @app.on_message(filters.command("start") & auth)
    async def start_cmd(client: Client, message: Message) -> None:
        await ensure_user(message.from_user.id)
        await message.reply_text(
            "🎬 <b>YouTube Recap &amp; Repurpose Bot</b>\n\n"
            "Send a YouTube URL or upload a video to begin.\n\n"
            "Commands:\n"
            "/status — active tasks\n"
            "/tasks — recent tasks\n"
            "/retry &lt;id&gt; — retry failed task\n"
            "/cancel &lt;id&gt; — cancel task\n"
            "/help — help\n\n"
            "⚠️ You must have rights to process any media you submit.",
            quote=True,
        )

    @app.on_message(filters.command("help") & auth)
    async def help_cmd(client: Client, message: Message) -> None:
        await message.reply_text(
            "<b>Help</b>\n\n"
            "1. Send a YouTube link or video file\n"
            "2. Acknowledge content rights\n"
            "3. Choose pipeline, voice, language, export\n"
            "4. Wait for processing — status updates live\n\n"
            "Modes:\n"
            "• <b>AI Recap</b> — narration + subtitles + SEO\n"
            "• <b>Transformative Creator Edit</b> — reframing, color, captions, commentary\n\n"
            "This bot does <b>not</b> bypass copyright systems.",
            quote=True,
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
            lines.append(
                f"#{t.id} {t.status.value} {t.current_stage or ''} {t.progress:.0f}%"
            )
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
            err = f" — {t.error_code}" if t.error_code else ""
            lines.append(f"#{t.id} {t.status.value}{err}")
        await message.reply_text("\n".join(lines))

    @app.on_message(filters.command("retry") & auth)
    async def retry_cmd(client: Client, message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.reply_text("Usage: /retry &lt;task_id&gt;")
            return
        task_id = int(parts[1])
        user = await ensure_user(message.from_user.id)
        async with get_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if task is None or task.user_id != user.id:
                await message.reply_text("Task not found.")
                return
            if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                await message.reply_text("Only failed/cancelled tasks can be retried.")
                return
            task.status = TaskStatus.QUEUED
            task.retry_count = (task.retry_count or 0) + 1
            task.error_code = None
            task.error_message = None
            priority = 0
        queue = client.queue_manager  # type: ignore[attr-defined]
        await queue.enqueue(task_id, priority=priority)
        await message.reply_text(f"Task #{task_id} requeued.")

    @app.on_message(filters.command("cancel") & auth)
    async def cancel_cmd(client: Client, message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.reply_text("Usage: /cancel &lt;task_id&gt;")
            return
        task_id = int(parts[1])
        user = await ensure_user(message.from_user.id)
        async with get_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if task is None or task.user_id != user.id:
                await message.reply_text("Task not found.")
                return
            if task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                await message.reply_text("Task already finished.")
                return
            task.status = TaskStatus.CANCELLED
            task.error_code = "CANCELLED"
        await message.reply_text(f"Task #{task_id} cancelled.")

    @app.on_message(filters.command("settings") & auth)
    async def settings_cmd(client: Client, message: Message) -> None:
        s = get_settings()
        await message.reply_text(
            f"<b>Settings</b>\n"
            f"Max concurrent: {s.max_concurrent_tasks}\n"
            f"Max duration: {s.max_video_duration_minutes} min\n"
            f"TTS default: {s.tts_provider}\n"
            f"Timezone: {s.target_timezone}\n"
            f"Peak: {s.peak_start}–{s.peak_end}",
        )
