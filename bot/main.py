"""Application entrypoint — Pyrogram dual-client, queue, recovery, graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from pyrogram import Client
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Settings, get_settings
from bot.database.session import close_db, get_session, init_db
from bot.handlers import register_all
from core.audit_log import audit
from core.cleanup import cleanup_orphans
from core.pipeline import Pipeline
from core.progress import format_completed, format_failed, format_progress
from core.queue_manager import QueueManager
from sqlalchemy import select
from bot.database.models import Task
from bot.states import CB_RESULT_DETAILS, CB_RESULT_SYNC, CB_RESULT_RAW

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bot.main")


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bot: Optional[Client] = None
        self.user_client: Optional[Client] = None
        self.queue: Optional[QueueManager] = None
        self.pipeline: Optional[Pipeline] = None
        self._shutdown = asyncio.Event()

    async def start(self) -> None:
        self.settings.ensure_directories()
        await init_db(self.settings)

        self.bot = Client(
            "youtube_recap_bot",
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            bot_token=self.settings.bot_token,
            workdir=str(Path("./data")),
            in_memory=False,
        )

        if self.settings.user_session_string:
            self.user_client = Client(
                "youtube_recap_user",
                api_id=self.settings.api_id,
                api_hash=self.settings.api_hash,
                session_string=self.settings.user_session_string,
                workdir=str(Path("./data")),
            )

        self.pipeline = Pipeline(
            self.settings,
            queue=None,
            notify=self._make_notifier(),
        )
        self.queue = QueueManager(self.settings, worker=self.pipeline.run)
        self.pipeline.queue = self.queue

        self.bot.queue_manager = self.queue  # type: ignore[attr-defined]
        self.bot.app_settings = self.settings  # type: ignore[attr-defined]

        register_all(self.bot)

        await self.bot.start()
        if self.user_client:
            try:
                await self.user_client.start()
                logger.info("User client started")
            except Exception as exc:
                logger.warning("User client failed to start: %s", exc)
                self.user_client = None

        await self.queue.start()
        recovered = await self.queue.recover_stale_tasks()
        logger.info("Startup recovery requeued %s tasks", recovered)

        asyncio.create_task(self._orphan_loop())

        me = await self.bot.get_me()
        logger.info("Bot started as @%s", me.username)
        await audit("bot_started", metadata={"username": me.username})

    def _make_notifier(self):
        async def notify_task(task_id: int, stage: str, percent: float, extra: Optional[str] = None) -> None:
            if not self.bot:
                return
            async with get_session() as session:
                result = await session.execute(select(Task).where(Task.id == task_id))
                task = result.scalar_one_or_none()
            if not task or not task.chat_id or not task.status_message_id:
                return
            try:
                if stage == "COMPLETED":
                    text = format_completed(task_id, extra)
                    await self.bot.edit_message_text(
                        task.chat_id, task.status_message_id, text
                    )
                    if task.output_video_path and Path(task.output_video_path).exists():
                        await self._send_output(task)
                        kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton("📊 Details", callback_data=f"{CB_RESULT_DETAILS}:{task.id}"), InlineKeyboardButton("🔍 Sync", callback_data=f"{CB_RESULT_SYNC}:{task.id}")],
                            [InlineKeyboardButton("🧪 Raw Files", callback_data=f"{CB_RESULT_RAW}:{task.id}")],
                        ])
                        try:
                            await self.bot.edit_message_reply_markup(task.chat_id, task.status_message_id, reply_markup=kb)
                        except Exception:
                            logger.debug("Could not attach result keyboard", exc_info=True)
                elif stage == "FAILED":
                    text = format_failed(task_id, extra or "Failed")
                    await self.bot.edit_message_text(
                        task.chat_id, task.status_message_id, text
                    )
                else:
                    text = format_progress(stage, percent, task_id=task_id, extra=extra)
                    await self.bot.edit_message_text(
                        task.chat_id, task.status_message_id, text
                    )
            except Exception as exc:
                logger.debug("Status edit failed: %s", exc)

        async def pipeline_notify(stage: str, percent: float, extra: Optional[str]) -> None:
            pass

        self._notify_task = notify_task
        return pipeline_notify

    async def _send_output(self, task: Task) -> None:
        path = Path(task.output_video_path) if task.output_video_path else None
        if not path or not path.exists() or not task.chat_id:
            return
        try:
            size = path.stat().st_size
            if size > 50 * 1024 * 1024 and self.user_client:
                await self.user_client.send_video(
                    task.chat_id,
                    str(path),
                    caption=f"✅ Task #{task.id} output",
                    supports_streaming=True,
                )
            else:
                assert self.bot
                await self.bot.send_video(
                    task.chat_id,
                    str(path),
                    caption=f"✅ Task #{task.id} output",
                    supports_streaming=True,
                )
            if task.thumbnail_path and Path(task.thumbnail_path).exists():
                await self.bot.send_photo(task.chat_id, task.thumbnail_path)
        except Exception as exc:
            logger.error("Failed to send output to Telegram: %s", exc)
            try:
                await self.bot.send_message(
                    task.chat_id,
                    "Output ready but Telegram upload failed.",
                )
            except Exception:
                pass

    async def _orphan_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(self.settings.orphan_cleanup_interval_hours * 3600)
                cleanup_orphans(self.settings, max_age_hours=24)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Orphan cleanup error: %s", exc)

    async def stop(self) -> None:
        logger.info("Shutting down…")
        self._shutdown.set()
        if self.queue:
            await self.queue.stop()
        if self.user_client:
            await self.user_client.stop()
        if self.bot:
            await self.bot.stop()
        await close_db()
        logger.info("Shutdown complete")


async def amain() -> None:
    settings = get_settings()
    logging.getLogger().setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    app = Application(settings)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(app.stop()))
        except NotImplementedError:
            pass

    await app.start()

    original_run = app.pipeline.run

    async def run_with_notify(task_id: int) -> None:
        async def notify(stage: str, percent: float, extra: Optional[str] = None) -> None:
            await app._notify_task(task_id, stage, percent, extra)

        app.pipeline.notify = notify
        try:
            await original_run(task_id)
        finally:
            async with get_session() as session:
                result = await session.execute(select(Task).where(Task.id == task_id))
                task = result.scalar_one_or_none()
            if task and task.status.value in ("COMPLETED", "SCHEDULED"):
                await app._notify_task(task_id, "COMPLETED", 100, task.youtube_video_id)
            elif task and task.status.value == "FAILED":
                await app._notify_task(
                    task_id, "FAILED", 0, task.error_message or "Failed"
                )

    app.queue.worker = run_with_notify

    await app._shutdown.wait()
    await app.stop()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
