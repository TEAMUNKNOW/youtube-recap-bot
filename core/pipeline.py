"""Multi-stage async processing pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from bot.config import Settings
from bot.database.models import ExportTarget, PipelineMode, Task, TaskStatus
from bot.database.session import get_session
from bot.exceptions import BotError, CancelledError, InputError, ValidationError
from core.audit_log import audit
from core.cleanup import check_disk_space, cleanup_task_workspace, task_workspace
from core.downloader import Downloader
from core.llm_agent import LLMAgent
from core.media_probe import probe
from core.pipeline_context import PipelineContext
from core.queue_manager import QueueManager
from core.scheduler import build_scheduler
from core.subtitle_engine import SubtitleEngine
from core.thumbnail_studio import ThumbnailStudio
from core.transcriber import Transcriber
from core.tts.factory import create_tts_provider
from core.video_engine import VideoEngine
from core.youtube_publisher import YouTubePublisher
from sqlalchemy import select

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float, Optional[str]], Coroutine[Any, Any, None]]


class Pipeline:
    def __init__(self, settings: Settings, queue: QueueManager, *, notify: Optional[ProgressCallback] = None) -> None:
        self.settings = settings
        self.queue = queue
        self.notify = notify
        self.downloader = Downloader(settings)
        self.transcriber = Transcriber(settings)
        self.llm = LLMAgent(settings)
        self.video = VideoEngine(settings)
        self.subtitles = SubtitleEngine()
        self.thumbs = ThumbnailStudio(settings)
        self.scheduler = build_scheduler(settings)

    async def run(self, task_id: int) -> None:
        async with get_session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if task is None:
                logger.error("Task %s not found", task_id)
                return
            if task.status == TaskStatus.CANCELLED:
                return

        if not check_disk_space(self.settings.workspace_root, self.settings.disk_space_min_gb):
            await self.queue.set_status(task_id, TaskStatus.FAILED, error_code="STORAGE_ERROR", error_message="Insufficient disk space")
            return

        workspace = task_workspace(self.settings, task_id)
        ctx = PipelineContext(task_id=task_id, workspace=workspace)

        try:
            await self._execute(task, ctx)
        except CancelledError:
            await self.queue.set_status(task_id, TaskStatus.CANCELLED, error_code="CANCELLED")
        except BotError as exc:
            logger.error("Task %s BotError: %s", task_id, exc.message)
            await self.queue.set_status(task_id, TaskStatus.FAILED, error_code=exc.code.value, error_message=exc.user_message)
            if self.notify:
                await self.notify("FAILED", 0, exc.user_message)
        except Exception as exc:
            logger.exception("Task %s unexpected error", task_id)
            await self.queue.set_status(task_id, TaskStatus.FAILED, error_code="UNKNOWN_ERROR", error_message="An unexpected error occurred")
        finally:
            preserve = self.settings.preserve_debug_package and ctx.debug_paths
            cleanup_task_workspace(self.settings, task_id, preserve_debug=bool(preserve), debug_paths=ctx.debug_paths if preserve else None)

    async def _execute(self, task: Task, ctx: PipelineContext) -> None:
        tid = task.id

        async def stage(name: str, pct: float) -> None:
            await self.queue.set_status(tid, TaskStatus[name] if name in TaskStatus.__members__ else TaskStatus.DOWNLOADING)
            await self.queue.heartbeat(tid, name, pct)
            if self.notify:
                await self.notify(name, pct, None)

        await stage("DOWNLOADING", 5)
        if task.source_type.value == "TELEGRAM_UPLOAD" and task.source_file:
            src = Path(task.source_file)
            if not src.exists():
                raise InputError("Uploaded file missing from workspace")
            ctx.source_path = src
        elif task.source_url:
            ctx.source_path = await self.downloader.download(task.source_url, ctx.workspace)
        else:
            raise InputError("No source URL or file")

        await stage("PROBING", 15)
        ctx.media_info = await probe(ctx.source_path, self.settings)
        if ctx.media_info.duration > self.settings.max_video_duration_minutes * 60:
            raise InputError(f"Video exceeds max duration ({self.settings.max_video_duration_minutes} min)")
        async with get_session() as session:
            from sqlalchemy import update
            await session.execute(update(Task).where(Task.id == tid).values(duration=ctx.media_info.duration, input_size=ctx.media_info.size_bytes))

        ctx.audio_path = ctx.workspace / "audio_16k.wav"
        await self.video.extract_audio(ctx.source_path, ctx.audio_path)

        await stage("TRANSCRIBING", 30)
        lang = (task.language or "en")[:2]
        if lang == "or":
            lang = None
        ctx.transcript = await self.transcriber.transcribe(ctx.audio_path, language=lang)

        await stage("SCRIPTING", 45)
        mode = (task.mode or PipelineMode.AI_RECAP).value
        ctx.script = await self.llm.generate_recap_script(ctx.transcript.text, duration_seconds=ctx.media_info.duration, language=task.language or "en", mode=mode)
        script_path = ctx.workspace / "transcript_recap.txt"
        script_path.write_text(ctx.script.script, encoding="utf-8")
        ctx.debug_paths.append(script_path)

        await stage("TTS", 55)
        tts = create_tts_provider(self.settings, task.tts_provider)
        voice = task.tts_voice
        tts_out = ctx.workspace / "voiceover_master.mp3"
        ctx.tts_result = await tts.synthesize(ctx.script.script, tts_out, voice=voice, language=task.language)
        ctx.debug_paths.append(Path(ctx.tts_result.path))

        narr_dur = ctx.tts_result.duration
        vid_dur = ctx.media_info.duration
        if abs(narr_dur - vid_dur) > self.settings.av_sync_tolerance_seconds * 1.5:
            logger.warning("Re-scripting due to AV mismatch %.1fs", narr_dur - vid_dur)
            ctx.script = await self.llm.generate_recap_script(ctx.transcript.text, duration_seconds=vid_dur, language=task.language or "en", mode=mode)
            ctx.tts_result = await tts.synthesize(ctx.script.script, tts_out, voice=voice, language=task.language)
            narr_dur = ctx.tts_result.duration

        await stage("AUDIO_PROCESSING", 65)
        ctx.muted_video_path = ctx.workspace / "clean_video_muted.mp4"
        await self.video.mute_video(ctx.source_path, ctx.muted_video_path)
        ctx.debug_paths.append(ctx.muted_video_path)

        bgm = self.video.pick_bgm()
        ctx.mixed_audio_path = ctx.workspace / "mixed_audio.m4a"
        await self.video.mix_audio(Path(ctx.tts_result.path), ctx.mixed_audio_path, bgm=bgm, video_duration=vid_dur, narration_duration=narr_dur)

        await stage("SUBTITLING", 72)
        ctx.subtitles_path = ctx.workspace / "subtitles.ass"
        self.subtitles.generate_ass(ctx.transcript, ctx.subtitles_path, script_text=ctx.script.script, use_words=bool(ctx.transcript.words))
        ctx.debug_paths.append(ctx.subtitles_path)

        await stage("RENDERING", 80)
        ctx.output_video_path = ctx.workspace / "final_output.mp4"
        await self.video.align_and_render(ctx.muted_video_path, ctx.mixed_audio_path, ctx.output_video_path, video_duration=vid_dur, narration_duration=narr_dur, subtitles=ctx.subtitles_path, mode=mode)

        await stage("THUMBNAIL", 88)
        ctx.thumbnail_path = ctx.workspace / "thumbnail.jpg"
        await self.thumbs.generate(ctx.output_video_path, ctx.thumbnail_path, title=ctx.script.title, duration=narr_dur)
        ctx.debug_paths.append(ctx.thumbnail_path)

        await stage("SEO", 92)
        ctx.seo = await self.llm.generate_seo(ctx.script.script, original_title=ctx.script.title, language=task.language or "en")
        meta_path = ctx.workspace / "metadata.json"
        meta_path.write_text(ctx.seo.model_dump_json(indent=2), encoding="utf-8")
        ctx.debug_paths.append(meta_path)

        await stage("VALIDATING", 95)
        info = await self.video.validate_output(ctx.output_video_path)
        async with get_session() as session:
            from sqlalchemy import update
            await session.execute(update(Task).where(Task.id == tid).values(output_size=info.size_bytes, output_video_path=str(ctx.output_video_path), thumbnail_path=str(ctx.thumbnail_path), metadata_json=ctx.seo.model_dump()))

        export = task.export_target or ExportTarget.TELEGRAM
        if export in (ExportTarget.YOUTUBE, ExportTarget.BOTH):
            await stage("UPLOADING", 97)
            try:
                publisher = YouTubePublisher(self.settings)
                publish_at = self.scheduler.next_slot()
                video_id = await publisher.upload(ctx.output_video_path, ctx.seo, thumbnail_path=ctx.thumbnail_path, privacy="private", publish_at=publish_at)
                ctx.youtube_video_id = video_id
                async with get_session() as session:
                    from sqlalchemy import update
                    await session.execute(update(Task).where(Task.id == tid).values(youtube_video_id=video_id, scheduled_at=publish_at, status=TaskStatus.SCHEDULED if publish_at else TaskStatus.COMPLETED))
            except BotError as exc:
                logger.error("YouTube export failed: %s", exc)
                if export == ExportTarget.YOUTUBE:
                    raise

        await self.queue.set_status(tid, TaskStatus.COMPLETED if not ctx.youtube_video_id else TaskStatus.SCHEDULED, progress=100.0, youtube_video_id=ctx.youtube_video_id)
        await audit("task_completed", task_id=tid, metadata={"youtube_id": ctx.youtube_video_id})
        if self.notify:
            await self.notify("COMPLETED", 100, ctx.youtube_video_id)
