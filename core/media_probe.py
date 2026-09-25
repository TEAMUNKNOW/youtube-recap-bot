"""FFprobe-based media inspection returning typed MediaInfo."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from bot.config import Settings
from bot.exceptions import FFmpegError, InputError, ValidationError

logger = logging.getLogger(__name__)


class MediaInfo(BaseModel):
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bitrate: Optional[int] = None
    rotation: int = 0
    num_streams: int = 0
    has_video: bool = False
    has_audio: bool = False
    is_hdr: bool = False
    format_name: Optional[str] = None
    size_bytes: int = 0


async def probe(path: Path, settings: Optional[Settings] = None) -> MediaInfo:
    if not path.exists():
        raise InputError(f"Media file not found: {path.name}")

    timeout = (settings.ffmpeg_timeout_seconds if settings else 120)
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise FFmpegError("ffprobe not found in PATH", retryable=False) from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise FFmpegError("ffprobe timed out", retryable=True) from exc

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[-500:]
        raise ValidationError(f"Corrupt or unsupported media: {err}")

    try:
        data = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError("ffprobe returned invalid JSON") from exc

    return _parse(path, data)


def _parse(path: Path, data: dict[str, Any]) -> MediaInfo:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    duration = float(fmt.get("duration") or 0)
    bitrate = int(fmt.get("bit_rate") or 0) or None
    size_bytes = int(fmt.get("size") or 0) or path.stat().st_size
    format_name = fmt.get("format_name")

    video_codec = audio_codec = None
    width = height = 0
    fps = 0.0
    sample_rate = channels = None
    rotation = 0
    has_video = has_audio = False
    is_hdr = False

    for s in streams:
        codec_type = s.get("codec_type")
        if codec_type == "video" and not has_video:
            has_video = True
            video_codec = s.get("codec_name")
            width = int(s.get("width") or 0)
            height = int(s.get("height") or 0)
            avg = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
            try:
                num, den = avg.split("/")
                fps = float(num) / float(den) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                fps = 0.0
            tags = s.get("tags") or {}
            if "rotate" in tags:
                try:
                    rotation = int(tags["rotate"])
                except ValueError:
                    pass
            color_transfer = (s.get("color_transfer") or "").lower()
            if color_transfer in ("smpte2084", "arib-std-b67"):
                is_hdr = True
            if duration <= 0 and s.get("duration"):
                duration = float(s["duration"])
        elif codec_type == "audio" and not has_audio:
            has_audio = True
            audio_codec = s.get("codec_name")
            sample_rate = int(s.get("sample_rate") or 0) or None
            channels = int(s.get("channels") or 0) or None

    if duration <= 0 and not has_video and not has_audio:
        raise ValidationError("No valid streams found in media")

    return MediaInfo(
        path=str(path), duration=duration, width=width, height=height, fps=fps,
        video_codec=video_codec, audio_codec=audio_codec, sample_rate=sample_rate,
        channels=channels, bitrate=bitrate, rotation=rotation, num_streams=len(streams),
        has_video=has_video, has_audio=has_audio, is_hdr=is_hdr,
        format_name=format_name, size_bytes=size_bytes,
    )
