"""Modular FFmpeg video/audio engine with HW accel detection and AV sync."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Sequence

from bot.config import Settings
from bot.exceptions import FFmpegError, TimeoutError_, ValidationError
from core.media_probe import MediaInfo, probe

logger = logging.getLogger(__name__)


class FFmpegRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._hw_encoder: Optional[str] = None
        self._detected = False

    async def detect_hw(self) -> Optional[str]:
        if self._detected:
            return self._hw_encoder
        self._detected = True
        for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
            if await self._encoder_available(enc):
                self._hw_encoder = enc
                logger.info("Hardware encoder available: %s", enc)
                return enc
        logger.info("No hardware encoder; using libx264")
        self._hw_encoder = None
        return None

    async def _encoder_available(self, name: str) -> bool:
        cmd = ["ffmpeg", "-hide_banner", "-encoders"]
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return name.encode() in stdout
        except Exception:
            return False

    async def run(self, args: Sequence[str], *, timeout: Optional[int] = None, label: str = "ffmpeg") -> None:
        timeout = timeout or self.settings.ffmpeg_timeout_seconds
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
        logger.debug("FFmpeg %s: %s", label, " ".join(cmd[:20]))
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError as exc:
            raise FFmpegError("ffmpeg not found in PATH", retryable=False) from exc
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise TimeoutError_(f"FFmpeg {label} timed out") from exc
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[-1500:]
            logger.error("FFmpeg %s failed: %s", label, err)
            raise FFmpegError(f"FFmpeg {label} failed: {err[:400]}", retryable=False)


class VideoEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runner = FFmpegRunner(settings)

    async def extract_audio(self, video: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        await self.runner.run(["-i", str(video), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(output)], label="extract_audio")
        return output

    async def mute_video(self, video: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        await self.runner.run(["-i", str(video), "-c:v", "copy", "-an", str(output)], label="mute_video")
        return output

    async def mix_audio(self, narration: Path, output: Path, *, bgm: Optional[Path] = None, video_duration: float, narration_duration: float) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        nv = self.settings.narration_volume
        bv = self.settings.bgm_volume
        if bgm and bgm.exists():
            filter_complex = (
                f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,volume={nv}[narr];"
                f"[1:a]volume={bv},aloop=loop=-1:size=2e+09,atrim=0:{video_duration}[bg];"
                f"[bg][narr]sidechaincompress=threshold=0.02:ratio=8:attack=50:release=300[ducked];"
                f"[ducked][narr]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-14:TP=-1.0:LRA=11[out]"
            )
            args = ["-i", str(narration), "-i", str(bgm), "-filter_complex", filter_complex, "-map", "[out]", "-c:a", "aac", "-b:a", self.settings.export_audio_bitrate, str(output)]
        else:
            args = ["-i", str(narration), "-af", f"volume={nv},loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", self.settings.export_audio_bitrate, str(output)]
        await self.runner.run(args, label="mix_audio")
        return output

    async def align_and_render(self, video: Path, mixed_audio: Path, output: Path, *, video_duration: float, narration_duration: float, subtitles: Optional[Path] = None, mode: str = "AI_RECAP") -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        await self.runner.detect_hw()
        encoder = self._video_encoder_args()
        tolerance = self.settings.av_sync_tolerance_seconds
        diff = narration_duration - video_duration
        vf_parts: list[str] = []
        w, h = self.settings.export_resolution.split("x")
        vf_parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={self.settings.export_fps}")
        if abs(diff) > tolerance:
            logger.warning("AV sync diff %.1fs exceeds tolerance %.1fs", diff, tolerance)
        if diff > 0.5:
            vf_parts.append(f"tpad=stop_mode=clone:stop_duration={diff:.3f}")
        elif diff < -0.5:
            vf_parts.append(f"trim=duration={narration_duration:.3f},setpts=PTS-STARTPTS")
        if mode == "TRANSFORMATIVE":
            vf_parts.append("eq=contrast=1.05:saturation=1.1,unsharp=5:5:0.5")
        if subtitles and subtitles.exists():
            sub_path = str(subtitles).replace("\\", "/").replace(":", "\\:")
            vf_parts.append(f"ass='{sub_path}'")
        vf = ",".join(vf_parts)
        args: list[str] = ["-i", str(video), "-i", str(mixed_audio), "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, *encoder, "-c:a", "aac", "-b:a", self.settings.export_audio_bitrate, "-movflags", "+faststart", "-shortest", str(output)]
        await self.runner.run(args, label="render")
        return output

    def _video_encoder_args(self) -> List[str]:
        hw = self.runner._hw_encoder
        if hw == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(self.settings.export_crf), "-b:v", "0"]
        if hw == "h264_qsv":
            return ["-c:v", "h264_qsv", "-global_quality", str(self.settings.export_crf)]
        return ["-c:v", "libx264", "-preset", self.settings.export_preset, "-crf", str(self.settings.export_crf), "-pix_fmt", "yuv420p"]

    async def validate_output(self, path: Path) -> MediaInfo:
        if not path.exists() or path.stat().st_size < 1000:
            raise ValidationError("Output file missing or too small")
        info = await probe(path, self.settings)
        if info.duration <= 0:
            raise ValidationError("Output has zero duration")
        if not info.has_video:
            raise ValidationError("Output missing video stream")
        return info

    def pick_bgm(self) -> Optional[Path]:
        bgm_dir = self.settings.bgm_directory
        if not bgm_dir.exists():
            return None
        candidates = list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav")) + list(bgm_dir.glob("*.m4a"))
        return candidates[0] if candidates else None
