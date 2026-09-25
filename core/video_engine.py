"""Modular FFmpeg video/audio engine with HW accel detection and AV sync."""

from __future__ import annotations

import asyncio
import logging
import random
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

        cuda_paths = (
            Path("/usr/lib/x86_64-linux-gnu/libcuda.so.1"),
            Path("/usr/lib/libcuda.so.1"),
            Path("/usr/local/cuda/lib64/libcuda.so.1"),
        )
        has_cuda = any(p.exists() for p in cuda_paths)
        candidates: List[str] = []
        if has_cuda:
            candidates.append("h264_nvenc")
        candidates.extend(["h264_qsv", "h264_amf"])

        for enc in candidates:
            if await self._encoder_works(enc):
                self._hw_encoder = enc
                logger.info("Hardware encoder available: %s", enc)
                return enc

        logger.info("No hardware encoder; using libx264")
        self._hw_encoder = None
        return None

    async def _encoder_listed(self, name: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-encoders",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return name.encode() in out
        except Exception:
            return False

    async def _encoder_works(self, name: str) -> bool:
        if not await self._encoder_listed(name):
            return False
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "probe.mp4"
            cmd = [
                "ffmpeg", "-hide_banner", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                "-frames:v", "1", "-c:v", name,
            ]
            if name == "h264_nvenc":
                cmd += ["-preset", "p1"]
            cmd.append(str(out))
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, err = await asyncio.wait_for(proc.communicate(), timeout=20)
                if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                    return True
                logger.debug("Encoder %s probe failed: %s", name, err.decode(errors="replace")[:200])
                return False
            except Exception as exc:
                logger.debug("Encoder %s probe exception: %s", name, exc)
                return False

    async def run(
        self,
        args: Sequence[str],
        *,
        timeout: Optional[float] = None,
        label: str = "ffmpeg",
    ) -> None:
        timeout = timeout or float(self.settings.ffmpeg_timeout_seconds)
        cmd = ["ffmpeg", "-hide_banner", "-y", *args]
        logger.debug("%s cmd: %s", label, " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
            except Exception:
                pass
            raise TimeoutError_(f"{label} timed out after {timeout}s") from exc
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[-2000:]
            raise FFmpegError(f"{label} failed: {err}")


class VideoEngine:
    """Facade used by pipeline: extract / mute / mix / render / validate."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runner = FFmpegRunner(settings)

    async def extract_audio(self, video: Path, out_wav: Path) -> Path:
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-i", str(video),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(out_wav),
        ]
        await self.runner.run(args, label="extract_audio")
        return out_wav

    async def mute_video(self, video: Path, out_video: Path) -> Path:
        out_video.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-i", str(video),
            "-c:v", "copy", "-an",
            str(out_video),
        ]
        await self.runner.run(args, label="mute_video")
        return out_video

    def pick_bgm(self) -> Optional[Path]:
        bgm_dir = Path(self.settings.bgm_directory)
        if not bgm_dir.exists():
            return None
        files = [
            p for p in bgm_dir.iterdir()
            if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
        ]
        if not files:
            return None
        return random.choice(files)

    async def mix_audio(
        self,
        narration: Path,
        output: Path,
        *,
        bgm: Optional[Path] = None,
        video_duration: Optional[float] = None,
        narration_duration: Optional[float] = None,
    ) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        nv = self.settings.narration_volume
        bv = self.settings.bgm_volume
        if bgm is None or not bgm.exists():
            args = [
                "-i", str(narration),
                "-filter:a", f"volume={nv}",
                "-c:a", "aac", "-b:a", self.settings.export_audio_bitrate,
                str(output),
            ]
            await self.runner.run(args, label="narration_only")
            return output

        filter_complex = (
            f"[0:a]volume={nv}[nar];"
            f"[1:a]volume={bv},aloop=loop=-1:size=2e+09[bg];"
            f"[nar][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        args = [
            "-i", str(narration),
            "-i", str(bgm),
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", self.settings.export_audio_bitrate,
            str(output),
        ]
        await self.runner.run(args, label="mix_audio")
        return output

    async def align_and_render(
        self,
        muted_video: Path,
        mixed_audio: Path,
        output: Path,
        *,
        video_duration: Optional[float] = None,
        narration_duration: Optional[float] = None,
        subtitles: Optional[Path] = None,
        mode: str = "AI_RECAP",
    ) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        await self.runner.detect_hw()
        encoder = self._video_encoder_args()

        w, h = self.settings.export_resolution.split("x")
        vf_parts = [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease",
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
            f"fps={self.settings.export_fps}",
        ]
        if subtitles and Path(subtitles).exists():
            ass_esc = (
                str(subtitles)
                .replace("\\", "/")
                .replace(":", "\\:")
                .replace("'", "\\'")
            )
            vf_parts.append(f"ass='{ass_esc}'")
        vf = ",".join(vf_parts)

        try:
            if video_duration is not None and narration_duration is not None:
                diff = narration_duration - video_duration
                if abs(diff) > self.settings.av_sync_tolerance_seconds:
                    logger.warning(
                        "AV sync diff %.1fs exceeds tolerance %.1fs (using -shortest)",
                        diff,
                        self.settings.av_sync_tolerance_seconds,
                    )
        except Exception:
            pass

        args: list[str] = [
            "-i", str(muted_video),
            "-i", str(mixed_audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", vf,
            *encoder,
            "-c:a", "aac",
            "-b:a", self.settings.export_audio_bitrate,
            "-movflags", "+faststart",
            "-shortest",
            str(output),
        ]

        try:
            await self.runner.run(args, label="render_final")
        except FFmpegError as exc:
            msg = str(exc).lower()
            if self.runner._hw_encoder and any(
                x in msg for x in ("nvenc", "cuda", "qsv", "amf")
            ):
                logger.warning(
                    "HW encode failed (%s); retrying with libx264",
                    self.runner._hw_encoder,
                )
                self.runner._hw_encoder = None
                encoder = self._video_encoder_args()
                args = [
                    "-i", str(muted_video),
                    "-i", str(mixed_audio),
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-vf", vf,
                    *encoder,
                    "-c:a", "aac",
                    "-b:a", self.settings.export_audio_bitrate,
                    "-movflags", "+faststart",
                    "-shortest",
                    str(output),
                ]
                await self.runner.run(args, label="render_final_sw")
            else:
                raise
        return output

    async def validate_output(self, path: Path) -> MediaInfo:
        info = await probe(path)
        if not path.exists() or path.stat().st_size < 1000:
            raise ValidationError(f"Output missing or too small: {path}")
        if not info.has_video:
            raise ValidationError("Output has no video stream")
        max_bytes = self.settings.max_output_bytes()
        if info.size_bytes > max_bytes:
            raise ValidationError(
                f"Output {info.size_bytes} exceeds max {max_bytes} bytes"
            )
        return info

    def _video_encoder_args(self) -> List[str]:
        hw = self.runner._hw_encoder
        if hw == "h264_nvenc":
            return [
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-rc", "vbr",
                "-cq", str(self.settings.export_crf),
                "-b:v", "0",
            ]
        if hw == "h264_qsv":
            return ["-c:v", "h264_qsv", "-global_quality", str(self.settings.export_crf)]
        return [
            "-c:v", "libx264",
            "-preset", self.settings.export_preset,
            "-crf", str(self.settings.export_crf),
            "-pix_fmt", "yuv420p",
        ]


VideoProcessor = VideoEngine
AudioProcessor = VideoEngine
