"""DB-backed task queue with in-memory asyncio priority queue and concurrency control."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

from sqlalchemy import select, update

from bot.config import Settings
from bot.database.models import Task, TaskStatus
from bot.database.session import get_session

logger = logging.getLogger(__name__)


@dataclass(order=True)
class QueueItem:
    priority: int
    task_id: int = field(compare=False)
    enqueued_at: float = field(compare=False, default=0.0)


class QueueManager:
    def __init__(
        self,
        settings: Settings,
        worker: Callable[[int], Coroutine[Any, Any, None]],
    ) -> None:
        self.settings = settings
        self.worker = worker
        self._queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._active: set[int] = set()

    async def start(self, num_workers: Optional[int] = None) -> None:
        if self._running:
            return
        self._running = True
        n = num_workers or self.settings.max_concurrent_tasks
        for i in range(n):
            t = asyncio.create_task(self._worker_loop(i), name=f"queue-worker-{i}")
            self._workers.append(t)
        logger.info("Queue manager started with %s workers", n)

    async def stop(self) -> None:
        self._running = False
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("Queue manager stopped")

    async def enqueue(self, task_id: int, priority: int = 2) -> None:
        item = QueueItem(
            priority=priority,
            task_id=task_id,
            enqueued_at=asyncio.get_event_loop().time(),
        )
        await self._queue.put(item)
        logger.info("Enqueued task %s priority=%s", task_id, priority)

    async def recover_stale_tasks(self) -> int:
        threshold = self.settings.stale_task_threshold_seconds
        now = datetime.now(timezone.utc)
        recovered = 0
        active_states = [
            TaskStatus.DOWNLOADING, TaskStatus.PROBING, TaskStatus.TRANSCRIBING,
            TaskStatus.SCRIPTING, TaskStatus.TTS, TaskStatus.AUDIO_PROCESSING,
            TaskStatus.RENDERING, TaskStatus.SUBTITLING, TaskStatus.THUMBNAIL,
            TaskStatus.SEO, TaskStatus.VALIDATING, TaskStatus.UPLOADING,
            TaskStatus.RECOVERING,
        ]
        async with get_session() as session:
            result = await session.execute(
                select(Task).where(Task.status.in_(active_states + [TaskStatus.QUEUED]))
            )
            tasks = result.scalars().all()
            for task in tasks:
                stale = False
                if task.status == TaskStatus.QUEUED:
                    stale = False
                elif task.heartbeat_at is None:
                    stale = True
                else:
                    age = (now - task.heartbeat_at.replace(tzinfo=timezone.utc)).total_seconds()
                    if age > threshold:
                        stale = True
                if task.status == TaskStatus.QUEUED or stale:
                    if stale:
                        task.status = TaskStatus.RECOVERING
                        task.error_message = "Recovered after process restart"
                        await session.flush()
                        task.status = TaskStatus.QUEUED
                        task.current_stage = "QUEUED"
                    recovered += 1
                    await self.enqueue(task.id, priority=0 if stale else task.priority)
        logger.info("Recovery: requeued %s tasks", recovered)
        return recovered

    async def heartbeat(self, task_id: int, stage: str, progress: float = 0.0) -> None:
        async with get_session() as session:
            await session.execute(
                update(Task).where(Task.id == task_id).values(
                    heartbeat_at=datetime.now(timezone.utc),
                    current_stage=stage,
                    progress=progress,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    async def set_status(
        self, task_id: int, status: TaskStatus, *,
        error_code: Optional[str] = None, error_message: Optional[str] = None,
        progress: Optional[float] = None, **extra: Any,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
            "heartbeat_at": datetime.now(timezone.utc),
        }
        if error_code is not None:
            values["error_code"] = error_code
        if error_message is not None:
            values["error_message"] = error_message
        if progress is not None:
            values["progress"] = progress
        if status == TaskStatus.COMPLETED:
            values["completed_at"] = datetime.now(timezone.utc)
            values["progress"] = 100.0
        if status in (TaskStatus.DOWNLOADING, TaskStatus.RECOVERING) and "started_at" not in extra:
            values["started_at"] = datetime.now(timezone.utc)
        values.update(extra)
        async with get_session() as session:
            await session.execute(update(Task).where(Task.id == task_id).values(**values))

    async def _worker_loop(self, worker_id: int) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            task_id = item.task_id
            if task_id in self._active:
                self._queue.task_done()
                continue
            async with self._semaphore:
                self._active.add(task_id)
                try:
                    logger.info("Worker %s starting task %s", worker_id, task_id)
                    await asyncio.wait_for(
                        self.worker(task_id),
                        timeout=self.settings.task_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    logger.error("Task %s timed out", task_id)
                    await self.set_status(task_id, TaskStatus.FAILED, error_code="TIMEOUT_ERROR", error_message="Task exceeded total timeout")
                except asyncio.CancelledError:
                    await self.set_status(task_id, TaskStatus.CANCELLED, error_code="CANCELLED", error_message="Worker cancelled")
                    raise
                except Exception as exc:
                    logger.exception("Task %s failed: %s", task_id, exc)
                    from bot.exceptions import BotError
                    code = exc.code.value if isinstance(exc, BotError) else "UNKNOWN_ERROR"
                    msg = exc.user_message if isinstance(exc, BotError) else "Internal error"
                    await self.set_status(task_id, TaskStatus.FAILED, error_code=code, error_message=msg)
                finally:
                    self._active.discard(task_id)
                    self._queue.task_done()
