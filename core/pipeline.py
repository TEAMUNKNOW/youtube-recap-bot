"""Multi-stage async processing pipeline — worker entry: run(task_id)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, List, Optional

from bot.config import Settings
from bot.database.models import SourceType, Task, TaskStatus
from bot.database.session import get_session
from bot.exceptions import BotError, InputError
from core.cleanup import CleanupManager, task_workspace
from core.llm_agent import LLMAgent
from core.media_probe import MediaInfo, probe
from core.subtitle_engine import SubtitleEngine
from core.thumbnail_studio import ThumbnailStudio
from core.transcriber import Transcriber
from core.tts.factory import TTSFactory
from core.video_engine import VideoEngine
from core.youtube_publisher import YouTubePublisher
from sqlalchemy import update

logger = logging.getLogger(__name__)

NotifyFn = Callable[[int, str, float, Optional[str]], Coroutine[Any, Any, None]]


@dataclass
class TaskContext:
    """In-memory processing context for a single task."""

    task_id: int
    workspace: Path
    source_path: Optional[Path] = None
    media_info: Optional[MediaInfo] = None
    audio_path: Optional[Path] = None
    transcript: Any = None
    script: Any = None
    tts_result: Any = None
    muted_video_path: Optional[Path] = None
    mixed_audio_path: Optional[Path] = None
    subtitles_path: Optional[Path] = None
    output_video_path: Optional[Path] = None
    thumbnail_path: Optional[Path] = None
    seo: Any = None
    debug_paths: List[Path] = field(default_factory=list)
    youtube_video_id: Optional[str] = None


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        queue: Any = None,
        notify: Optional[NotifyFn] = None,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.notify = notify
        self.video = VideoEngine(settings)
        self.subtitles = SubtitleEngine()
        self.llm = LLMAgent(settings)
        self.transcriber = Transcriber(settings)
        self.tts = TTSFactory(settings)
        self.thumbs = ThumbnailStudio(settings)
        self.publisher = YouTubePublisher(settings)
        self.cleanup = CleanupManager(settings)

    async def _stage(self, task_id: int, name: str, pct: float) -> None:
        status = TaskStatus[name] if name in TaskStatus.__members__ else TaskStatus.DOWNLOADING
        if self.queue is not None:
            try:
                await self.queue.set_status(task_id, status, progress=pct)
                await self.queue.heartbeat(task_id, name, pct)
            except Exception:
                logger.debug("queue status update failed", exc_info=True)
        if self.notify:
            try:
                await self.notify(task_id, name, pct, None)
            except Exception:
                logger.debug("notify failed", exc_info=True)

    async def run(self, task_id: int) -> None:
        """QueueManager worker entrypoint."""
        ws = task_workspace(self.settings, task_id)
        ctx = TaskContext(task_id=task_id, workspace=ws)

        async with get_session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise BotError(f"Task {task_id} not found", retryable=False)
            language = task.language or "en"
            mode = task.mode.value if task.mode else "AI_RECAP"
            tts_provider = task.tts_provider
            tts_voice = task.tts_voice
            source_type = task.source_type
            source_url = task.source_url
            source_file = task.source_file

        try:
            await self._stage(task_id, "VALIDATING", 5)

            if source_file and Path(source_file).exists():
                ctx.source_path = Path(source_file)
            elif source_url:
                await self._stage(task_id, "DOWNLOADING", 10)
                from core.downloader import Downloader
                ctx.source_path = await Downloader(self.settings).download(source_url, ws)
            else:
                raise InputError("No source media available for task")

            if not ctx.source_path or not ctx.source_path.exists():
                raise InputError("Source media missing after download")

            ctx.media_info = await probe(ctx.source_path)
            if ctx.media_info.duration > self.settings.max_video_duration_minutes * 60:
                raise InputError(
                    f"Video exceeds max duration ({self.settings.max_video_duration_minutes} min)"
                )

            async with get_session() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        duration=ctx.media_info.duration,
                        input_size=ctx.media_info.size_bytes,
                    )
                )
                await session.commit()

            await self._stage(task_id, "EXTRACTING_AUDIO", 15)
            ctx.audio_path = ws / "audio.mp3"
            await self.video.extract_audio(ctx.source_path, ctx.audio_path)

            await self._stage(task_id, "TRANSCRIBING", 30)
            ctx.transcript = await self.transcriber.transcribe(ctx.audio_path)

            await self._stage(task_id, "SCRIPTING", 45)
            if ctx.media_info.duration >= 15 * 60:
                ctx.script = await self.llm.generate_long_form_recap(
                    ctx.transcript,
                    duration_seconds=ctx.media_info.duration,
                    language=language,
                    mode=mode,
                )
            else:
                ctx.script = await self.llm.generate_recap_script(
                    ctx.transcript.text,
                    duration_seconds=ctx.media_info.duration,
                    language=language,
                    mode=mode,
                )

            (ws / "script.txt").write_text(ctx.script.script, encoding="utf-8")
            (ws / "transcript.txt").write_text(ctx.transcript.text, encoding="utf-8")
            await self._stage(task_id, "TTS", 55)
            ctx.tts_result = await self.tts.synthesize(
                ctx.script.script,
                language=language,
                out_dir=ws,
                voice=tts_voice,
                provider=tts_provider,
            )

            narr_dur = float(ctx.tts_result.duration or 0)
            vid_dur = float(ctx.media_info.duration or 0)

            # AI_RECAP is intentionally shorter than the source. Only transformative
            # mode requires full-duration alignment; do not blindly re-script based
            # on a recap-length narration.
            if mode == "TRANSFORMATIVE" and abs(narr_dur - vid_dur) > self.settings.av_sync_tolerance_seconds:
                logger.warning("Re-scripting due to AV mismatch %.1fs", narr_dur - vid_dur)
                ctx.script = await self.llm.generate_long_form_recap(
                    ctx.transcript,
                    duration_seconds=vid_dur,
                    language=language,
                    mode=mode,
                ) if vid_dur >= 15 * 60 else await self.llm.generate_recap_script(
                    ctx.transcript.text,
                    duration_seconds=vid_dur,
                    language=language,
                    mode=mode,
                )
                ctx.tts_result = await self.tts.synthesize(
                    ctx.script.script,
                    language=language,
                    out_dir=ws,
                    voice=tts_voice,
                    provider=tts_provider,
                )
                narr_dur = float(ctx.tts_result.duration or 0)

            await self._stage(task_id, "MUTE_VIDEO", 62)
            ctx.muted_video_path = ws / "muted.mp4"
            await self.video.mute_video(ctx.source_path, ctx.muted_video_path)

            await self._stage(task_id, "MIX_AUDIO", 68)
            bgm = self.video.pick_bgm()
            ctx.mixed_audio_path = ws / "mixed_audio.m4a"
            await self.video.mix_audio(
                Path(ctx.tts_result.path),
                ctx.mixed_audio_path,
                bgm=bgm,
                video_duration=vid_dur,
                narration_duration=narr_dur,
            )

            await self._stage(task_id, "SUBTITLING", 72)
            ctx.subtitles_path = ws / "subtitles.ass"
            self.subtitles.font_name = SubtitleEngine.font_for_language(language)
            self.subtitles.generate_ass(
                ctx.transcript,
                ctx.subtitles_path,
                script_text=ctx.script.script,
                use_words=False,
            )
            ctx.debug_paths.append(ctx.subtitles_path)

            await self._stage(task_id, "RENDERING", 80)
            ctx.output_video_path = ws / "final_output.mp4"
            await self.video.align_and_render(
                ctx.muted_video_path,
                ctx.mixed_audio_path,
                ctx.output_video_path,
                video_duration=vid_dur,
                narration_duration=narr_dur,
                subtitles=ctx.subtitles_path,
                mode=mode,
            )

            await self._stage(task_id, "THUMBNAIL", 88)
            ctx.thumbnail_path = ws / "thumbnail.jpg"
            await self.thumbs.generate(
                ctx.output_video_path,
                ctx.thumbnail_path,
                title=ctx.script.title,
                duration=narr_dur or vid_dur,
                chapters=getattr(ctx.script, "chapters", None),
            )

            await self._stage(task_id, "VALIDATING_OUTPUT", 92)
            info = await self.video.validate_output(ctx.output_video_path)
            sync_diff = narr_dur - float(info.duration or narr_dur)
            metadata = {
                "source_duration": vid_dur,
                "narration_duration": narr_dur,
                "output_duration": float(info.duration or 0),
                "sync_offset_seconds": sync_diff,
                "sync_within_tolerance": abs(sync_diff) <= self.settings.av_sync_tolerance_seconds,
                "word_count": int(getattr(ctx.script, "word_count", 0) or len(ctx.script.script.split())),
                "chapters": [
                    {"title": ch.title, "start_seconds": ch.start_seconds}
                    for ch in (getattr(ctx.script, "chapters", None) or [])
                ],
                "key_points": list(getattr(ctx.script, "key_points", None) or []),
                "raw_files": sorted(p.name for p in ws.iterdir() if p.is_file()),
            }
            async with get_session() as session:
                await session.execute(
                    update(Task).where(Task.id == task_id).values(metadata_json=metadata)
                )
                await session.commit()

            await self._stage(task_id, "SEO", 95)
            try:
                ctx.seo = await self.llm.generate_seo(
                    ctx.script.script,
                    original_title=ctx.script.title,
                    language=language,
                )
            except Exception:
                logger.warning("SEO generation failed", exc_info=True)

            await self._stage(task_id, "COMPLETED", 100)
            async with get_session() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        status=TaskStatus.COMPLETED,
                        progress=100,
                        output_video_path=str(ctx.output_video_path),
                        thumbnail_path=str(ctx.thumbnail_path) if ctx.thumbnail_path else None,
                        output_size=info.size_bytes,
                    )
                )
                await session.commit()

            if self.notify:
                try:
                    await self.notify(task_id, "COMPLETED", 100, str(ctx.output_video_path))
                except Exception:
                    pass

        except BotError:
            raise
        except Exception as exc:
            logger.exception("Pipeline failed for task %s", task_id)
            raise BotError(str(exc), retryable=False) from exc
