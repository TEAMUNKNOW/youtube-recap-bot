"""Multi-stage async processing pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from bot.config import Settings
from bot.database.models import Task, TaskState
from bot.exceptions import BotError, InputError
from core.cleanup import CleanupManager
from core.llm_agent import LLMAgent
from core.media_probe import probe
from core.queue_manager import TaskContext
from core.subtitle_engine import SubtitleEngine
from core.thumbnail_studio import ThumbnailStudio
from core.transcriber import Transcriber
from core.tts.factory import TTSFactory
from core.video_engine import VideoEngine
from core.youtube_publisher import YouTubePublisher
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, int], Coroutine[Any, Any, None]]


class Pipeline:
    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.video = VideoEngine(settings)
        self.subtitles = SubtitleEngine()
        self.llm = LLMAgent(settings)
        self.transcriber = Transcriber(settings)
        self.tts = TTSFactory(settings)
        self.thumbs = ThumbnailStudio(settings)
        self.publisher = YouTubePublisher(settings)
        self.cleanup = CleanupManager(settings)

    async def run(self, ctx: TaskContext, progress: Optional[ProgressCb] = None) -> None:
        tid = ctx.task_id

        async def stage(name: str, pct: int) -> None:
            async with self.session_factory() as session:
                await session.execute(
                    update(Task).where(Task.id == tid).values(state=name, progress=pct)
                )
                await session.commit()
            if progress:
                await progress(name, pct)

        try:
            await stage("VALIDATING", 5)
            if not ctx.source_path or not Path(ctx.source_path).exists():
                raise InputError("Source media missing")
            ctx.media_info = await probe(Path(ctx.source_path))
            if ctx.media_info.duration > self.settings.max_video_duration_minutes * 60:
                raise InputError(
                    f"Video exceeds max duration ({self.settings.max_video_duration_minutes} min)"
                )

            async with self.session_factory() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == tid)
                    .values(
                        duration=ctx.media_info.duration,
                        input_size=ctx.media_info.size_bytes,
                    )
                )
                await session.commit()

            # Load task row for language / mode
            async with self.session_factory() as session:
                task = await session.get(Task, tid)
                if task is None:
                    raise BotError("Task not found")
                mode = task.mode or "AI_RECAP"

            await stage("EXTRACTING_AUDIO", 15)
            ctx.audio_path = ctx.workspace / "audio.wav"
            await self.video.extract_audio(Path(ctx.source_path), ctx.audio_path)

            await stage("TRANSCRIBING", 30)
            ctx.transcript = await self.transcriber.transcribe(ctx.audio_path)

            await stage("SCRIPTING", 45)
            ctx.script = await self.llm.generate_recap_script(
                ctx.transcript.text,
                duration_seconds=ctx.media_info.duration,
                language=task.language or "en",
                mode=mode,
            )

            await stage("TTS", 55)
            ctx.tts_result = await self.tts.synthesize(
                ctx.script.script,
                language=task.language or "en",
                out_dir=ctx.workspace,
            )

            # Re-script once if narration much shorter/longer than video
            narr_dur = ctx.tts_result.duration
            vid_dur = ctx.media_info.duration
            if abs(narr_dur - vid_dur) > self.settings.av_sync_tolerance_seconds:
                logger.warning("Re-scripting due to AV mismatch %.1fs", narr_dur - vid_dur)
                ctx.script = await self.llm.generate_recap_script(
                    ctx.transcript.text,
                    duration_seconds=vid_dur,
                    language=task.language or "en",
                    mode=mode,
                )
                ctx.tts_result = await self.tts.synthesize(
                    ctx.script.script,
                    language=task.language or "en",
                    out_dir=ctx.workspace,
                )
                narr_dur = ctx.tts_result.duration

            await stage("MUTE_VIDEO", 62)
            ctx.muted_video_path = ctx.workspace / "muted.mp4"
            await self.video.mute_video(Path(ctx.source_path), ctx.muted_video_path)

            await stage("MIX_AUDIO", 68)
            bgm = self.video.pick_bgm()
            ctx.mixed_audio_path = ctx.workspace / "mixed_audio.m4a"
            await self.video.mix_audio(
                Path(ctx.tts_result.path),
                ctx.mixed_audio_path,
                bgm=bgm,
                video_duration=vid_dur,
                narration_duration=narr_dur,
            )

            await stage("SUBTITLING", 72)
            ctx.subtitles_path = ctx.workspace / "subtitles.ass"
            lang = task.language or "en"
            self.subtitles.font_name = SubtitleEngine.font_for_language(lang)
            self.subtitles.generate_ass(
                ctx.transcript,
                ctx.subtitles_path,
                script_text=ctx.script.script,
                use_words=bool(ctx.transcript.words),
            )
            ctx.debug_paths.append(ctx.subtitles_path)

            await stage("RENDERING", 80)
            ctx.output_video_path = ctx.workspace / "final_output.mp4"
            await self.video.align_and_render(
                ctx.muted_video_path,
                ctx.mixed_audio_path,
                ctx.output_video_path,
                video_duration=vid_dur,
                narration_duration=narr_dur,
                subtitles=ctx.subtitles_path,
                mode=mode,
            )

            await stage("THUMBNAIL", 88)
            ctx.thumbnail_path = ctx.workspace / "thumbnail.jpg"
            await self.thumbs.generate(
                ctx.output_video_path,
                ctx.thumbnail_path,
                title=ctx.script.title,
                duration=narr_dur,
            )

            await stage("VALIDATING_OUTPUT", 92)
            info = await self.video.validate_output(ctx.output_video_path)

            await stage("SEO", 95)
            ctx.seo = await self.llm.generate_seo(
                ctx.script.script,
                original_title=ctx.script.title,
                language=task.language or "en",
            )

            await stage("COMPLETED", 100)
            async with self.session_factory() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == tid)
                    .values(
                        state=TaskState.COMPLETED.value,
                        progress=100,
                        output_path=str(ctx.output_video_path),
                        output_size=info.size_bytes,
                    )
                )
                await session.commit()

        except BotError:
            raise
        except Exception as exc:
            logger.exception("Pipeline failed for task %s", tid)
            raise BotError(str(exc), retryable=False) from exc
        finally:
            try:
                await self.cleanup.clean_workspace(ctx.workspace)
            except Exception:
                logger.warning("Cleanup failed for %s", ctx.workspace, exc_info=True)
