"""Bot entrypoint."""

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
from bot.states import CB_RESULT_DETAILS, CB_RESULT_SYNC, CB_RESULT_RAW, CB_RESULT_RETRY
from core.shorts.manager import ShortsManager
from core.shorts.oauth_server import create_oauth_app

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bot: Optional[Client] = None
        self.user_client: Optional[Client] = None
        self.queue: Optional[QueueManager] = None
        self.pipeline: Optional[Pipeline] = None
        self.shorts: Optional[ShortsManager] = None
        self._oauth_server = None
        self._oauth_task = None
        self._orphan_task = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await init_db()
        self.bot = Client(
            "bot",
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            bot_token=self.settings.bot_token,
            workdir=str(Path(self.settings.workspace_root) / "sessions"),
            in_memory=True,
        )
        await self.bot.start()
        me = await self.bot.get_me()
        logger.info("Bot started as @%s", me.username)

        if self.settings.shorts_enabled:
            self.shorts = ShortsManager(self.settings)
            self.bot.shorts_manager = self.shorts  # type: ignore[attr-defined]

        register_all(self.bot)

        self.pipeline = Pipeline(self.settings)
        self.queue = QueueManager(self.settings, self.pipeline.run)
        self.bot.queue_manager = self.queue  # type: ignore[attr-defined]
        await self.queue.start()

        # Railway public URL hits $PORT. Prefer that so OAuth is reachable.
        if self.settings.youtube_oauth_redirect_uri or self.settings.shorts_enabled:
            import os
            import uvicorn

            oauth_app = create_oauth_app(self.settings, self.bot)
            port = int(os.environ.get("PORT") or self.settings.shorts_oauth_bind_port or 8080)
            host = self.settings.shorts_oauth_bind_host or "0.0.0.0"
            config = uvicorn.Config(
                oauth_app, host=host, port=port, log_level="warning"
            )
            self._oauth_server = uvicorn.Server(config)
            self._oauth_task = asyncio.create_task(self._oauth_server.serve())
            logger.info("OAuth HTTP server listening on %s:%s", host, port)

        if self.shorts:
            try:
                logger.info("Shorts recovery requeued %s projects", await self.shorts.recover())
            except Exception:
                logger.exception("Shorts recovery failed")

        try:
            n = await self.queue.recover()
            logger.info("Startup recovery requeued %s tasks", n)
        except Exception:
            logger.exception("Task recovery failed")

        self._orphan_task = asyncio.create_task(self._orphan_loop())

    async def _orphan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                cleanup_orphans(self.settings, max_age_hours=24)
            except Exception:
                logger.debug("orphan cleanup failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=3600)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._oauth_server:
            self._oauth_server.should_exit = True
        if self._oauth_task:
            await asyncio.gather(self._oauth_task, return_exceptions=True)
        if self.queue:
            await self.queue.stop()
        if self.bot:
            await self.bot.stop()
        await close_db()


async def amain() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app = Application(settings)

    async def run_with_notify(task_id: int) -> None:
        async def notify(stage: str, percent: float, extra: Optional[str] = None) -> None:
            pass

        original_run = app.pipeline.run  # type: ignore

        async def wrapped(tid: int) -> None:
            await original_run(tid)

        await wrapped(task_id)

    # Use pipeline directly; QueueManager already calls pipeline.run
    await app.start()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _sig(*_args):
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sig)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await app.stop()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
