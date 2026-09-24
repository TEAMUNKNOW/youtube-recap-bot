"""Shared context object passed through pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.llm_agent import RecapScript, SEOResult
from core.media_probe import MediaInfo
from core.transcriber import Transcript
from core.tts.base import TTSResult


@dataclass
class PipelineContext:
    task_id: int
    workspace: Path
    source_path: Optional[Path] = None
    media_info: Optional[MediaInfo] = None
    audio_path: Optional[Path] = None
    transcript: Optional[Transcript] = None
    script: Optional[RecapScript] = None
    tts_result: Optional[TTSResult] = None
    mixed_audio_path: Optional[Path] = None
    muted_video_path: Optional[Path] = None
    subtitles_path: Optional[Path] = None
    output_video_path: Optional[Path] = None
    thumbnail_path: Optional[Path] = None
    seo: Optional[SEOResult] = None
    youtube_video_id: Optional[str] = None
    debug_paths: list[Path] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)
