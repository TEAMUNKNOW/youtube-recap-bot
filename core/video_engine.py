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
        """Detect a *working* hardware encoder. On cloud (no GPU) always libx264."""
        if self._detected:
            return self._hw_encoder
        self._detected = True

        # No CUDA library → skip NVENC (Railway / most free cloud)
        cuda_paths = (
            Path("/usr/lib/x86_64-linux-gnu/libcuda.so.1"),
            Path("/usr/lib/libcuda.so.1"),
            Path("/usr/local/cuda/lib64/libcuda.so.1"),
        )
        has_cuda = any(p.exists() for p in cuda_paths)

        candidates = []
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
        cmd = ["ffmpeg", "-hide_banner", "-encoders"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return name.encode() in out
        except Exception:
            return False

    async def _encoder_works(self, name: str) -> bool:
        """Actually try encoding 1 black frame — listing in -encoders is not enough."""
        if not await self._encoder_listed(name):
            return False
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "probe.mp4"
            cmd = [
                "ffmpeg", "-hide_banner", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                "-frames:v", "1",
                "-c:v", name,
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


class MediaNormalizer:
    def __init__(self, settings: Settings, runner: FFmpegRunner) -> None:
        self.settings = settings
        self.runner = runner

    async def normalize_video(self, src: Path, dst: Path) -> MediaInfo:
        args = [
            "-i", str(src),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            str(dst),
        ]
        await self.runner.run(args, label="normalize")
        return await probe(dst)


class AudioProcessor:
    def __init__(self, settings: Settings, runner: FFmpegRunner) -> None:
        self.settings = settings
        self.runner = runner

    async def extract_audio(self, video: Path, out_wav: Path) -> Path:
        args = [
            "-i", str(video),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(out_wav),
        ]
        await self.runner.run(args, label="extract_audio")
        return out_wav

    async def mix_narration_bgm(
        self,
        narration: Path,
        bgm: Optional[Path],
        output: Path,
        *,
        narration_vol: Optional[float] = None,
        bgm_vol: Optional[float] = None,
    ) -> Path:
        nv = narration_vol if narration_vol is not None else self.settings.narration_volume
        bv = bgm_vol if bgm_vol is not None else self.settings.bgm_volume
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


class VideoProcessor:
    def __init__(self, settings: Settings, runner: FFmpegRunner) -> None:
        self.settings = settings
        self.runner = runner

    async def render_final(
        self,
        video: Path,
        mixed_audio: Path,
        output: Path,
        *,
        ass_path: Optional[Path] = None,
        duration_limit: Optional[float] = None,
    ) -> Path:
        await self.runner.detect_hw()
        encoder = self._video_encoder_args()

        w, h = self.settings.export_resolution.split("x")
        vf_parts = [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease",
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
            f"fps={self.settings.export_fps}",
        ]
        if ass_path and ass_path.exists():
            ass_esc = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            vf_parts.append(f"ass='{ass_esc}'")
        vf = ",".join(vf_parts)

        args: list[str] = [
            "-i", str(video),
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
        if duration_limit and duration_limit > 0:
            args = ["-t", str(duration_limit), *args]

        try:
            vinfo = await probe(video)
            ainfo = await probe(mixed_audio)
            if vinfo.duration and ainfo.duration:
                diff = ainfo.duration - vinfo.duration
                if abs(diff) > self.settings.av_sync_tolerance_seconds:
                    logger.warning(
                        "AV sync diff %.1fs exceeds tolerance %.1fs (continuing with -shortest)",
                        diff,
                        self.settings.av_sync_tolerance_seconds,
                    )
        except Exception as exc:
            logger.debug("AV probe skipped: %s", exc)

        try:
            await self.runner.run(args, label="render_final")
        except FFmpegError as exc:
            if self.runner._hw_encoder and (
                "nvenc" in str(exc).lower()
                or "cuda" in str(exc).lower()
                or "qsv" in str(exc).lower()
            ):
                logger.warning("HW encode failed (%s); retrying with libx264", self.runner._hw_encoder)
                self.runner._hw_encoder = None
                encoder = self._video_encoder_args()
                args = [
                    "-i", str(video),
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
