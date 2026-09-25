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
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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
            cmd = ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                   "-frames:v", "1", "-c:v", name]
            if name == "h264_nvenc":
                cmd += ["-preset", "p1"]
            cmd.append(str(out))
            try:
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(proc.communicate(), timeout=20)
                return proc.returncode == 0 and out.exists() and out.stat().st_size > 0
            except Exception:
                return False

    async def run(self, args: Sequence[str], *, timeout: Optional[float] = None, label: str = "ffmpeg") -> None:
        timeout = timeout or float(self.settings.ffmpeg_timeout_seconds)
        cmd = ["ffmpeg", "-hide_banner", "-y", *args]
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
            except Exception:
                pass
            raise TimeoutError_(f"{label} timed out after {timeout}s") from exc
        if proc.returncode != 0:
            raw_err = stderr.decode(errors="replace")
            err = raw_err[-5000:]
            raise FFmpegError(f"{label} failed (exit={proc.returncode}): {err}")


class VideoEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runner = FFmpegRunner(settings)

    async def extract_audio(self, video: Path, out_wav: Path) -> Path:
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        await self.runner.run(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k", str(out_wav)], label="extract_audio")
        return out_wav

    async def mute_video(self, video: Path, out_video: Path) -> Path:
        out_video.parent.mkdir(parents=True, exist_ok=True)
        await self.runner.run([
            "-fflags", "+genpts",
            "-i", str(video),
            "-map", "0:v:0",
            "-c:v", "copy",
            "-an",
            "-avoid_negative_ts", "make_zero",
            str(out_video),
        ], label="mute_video")
        return out_video

    def pick_bgm(self) -> Optional[Path]:
        bgm_dir = Path(self.settings.bgm_directory)
        if not bgm_dir.exists():
            return None
        files = [p for p in bgm_dir.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}]
        return random.choice(files) if files else None

    async def mix_audio(self, narration: Path, output: Path, *, bgm: Optional[Path] = None, video_duration: Optional[float] = None, narration_duration: Optional[float] = None) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        nv, bv = self.settings.narration_volume, self.settings.bgm_volume
        if bgm is None or not bgm.exists():
            await self.runner.run(["-i", str(narration), "-filter:a", f"volume={nv}", "-c:a", "aac", "-b:a", self.settings.export_audio_bitrate, str(output)], label="narration_only")
            return output
        fc = f"[0:a]volume={nv}[nar];[1:a]volume={bv},aloop=loop=-1:size=2e+09[bg];[nar][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        await self.runner.run(["-i", str(narration), "-i", str(bgm), "-filter_complex", fc, "-map", "[aout]", "-c:a", "aac", "-b:a", self.settings.export_audio_bitrate, str(output)], label="mix_audio")
        return output

    async def align_and_render(self, muted_video: Path, mixed_audio: Path, output: Path, *, video_duration: Optional[float] = None, narration_duration: Optional[float] = None, subtitles: Optional[Path] = None, mode: str = "AI_RECAP") -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        await self.runner.detect_hw()
        encoder = self._video_encoder_args()
        # CPU-only Railway containers can be OOM-killed when a 720p source is
        # accidentally rendered into a 1080p canvas. Bound the render size to
        # 1280x720 regardless of a stale EXPORT_RESOLUTION environment variable.
        try:
            requested_w, requested_h = (int(x) for x in self.settings.export_resolution.split("x", 1))
        except (ValueError, AttributeError):
            requested_w, requested_h = 1280, 720
        max_w = min(max(320, requested_w), 1280)
        max_h = min(max(240, requested_h), 720)
        vf_parts = [
            f"scale=w='min(iw,{max_w})':h='min(ih,{max_h})':force_original_aspect_ratio=decrease",
            f"fps=min({self.settings.export_fps},30)",
        ]
        if subtitles and Path(subtitles).exists():
            ass_esc = str(subtitles).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            vf_parts.append(f"ass='{ass_esc}'")
        vf = ",".join(vf_parts)
        if video_duration is not None and narration_duration is not None:
            diff = narration_duration - video_duration
            if abs(diff) > self.settings.av_sync_tolerance_seconds:
                logger.info("Source/narration duration differ by %.1fs; output will follow narration duration", diff)

        def build_args(vf_str: str, enc: list) -> list:
            args = [
                "-fflags", "+genpts",
                "-i", str(muted_video),
                "-i", str(mixed_audio),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-vf", vf_str,
                *enc,
                "-c:a", "aac",
                "-b:a", self.settings.export_audio_bitrate,
                "-map_metadata", "-1",
                "-avoid_negative_ts", "make_zero",
            ]
            # Recaps intentionally use a short narration over the source video.
            # Explicitly limit output duration so broken/large source timestamps
            # cannot make -shortest wait indefinitely.
            if narration_duration and narration_duration > 0:
                args += ["-t", f"{narration_duration:.3f}"]
            else:
                args += ["-shortest"]
            args += ["-movflags", "+faststart", str(output)]
            return args

        try:
            await self.runner.run(build_args(vf, encoder), label="render_final")
        except FFmpegError as exc:
            msg = str(exc).lower()
            if self.runner._hw_encoder and any(x in msg for x in ("nvenc", "cuda", "qsv", "amf")):
                logger.warning("HW encode failed; retrying libx264")
                self.runner._hw_encoder = None
                encoder = self._video_encoder_args()
                try:
                    await self.runner.run(build_args(vf, encoder), label="render_final_sw")
                    return output
                except FFmpegError as exc2:
                    exc, msg = exc2, str(exc2).lower()
            # A subtitle filter can fail for many reasons (fontconfig, libass,
            # malformed glyphs, or a bad ASS event). The video itself should still
            # be recoverable, so retry once without burn-in before failing the task.
            if subtitles and Path(subtitles).exists() and "ass=" in vf:
                logger.warning("Final render failed; retrying once without subtitle burn-in")
                # Do not split the serialized filter graph on commas: FFmpeg
                # expressions such as min(iw,1280) and fps=min(30,30) contain
                # commas themselves. Removing the subtitle stage from the
                # original list keeps the filter graph syntactically intact.
                vf_nosub = ",".join(p for p in vf_parts if not p.startswith("ass="))
                try:
                    await self.runner.run(build_args(vf_nosub, self._video_encoder_args()), label="render_final_nosub")
                    return output
                except FFmpegError as exc2:
                    raise FFmpegError(
                        f"render_final failed; subtitle-free retry also failed: {exc2}"
                    ) from exc2
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
            raise ValidationError(f"Output {info.size_bytes} exceeds max {max_bytes} bytes")
        return info

    def _video_encoder_args(self) -> List[str]:
        hw = self.runner._hw_encoder
        if hw == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(self.settings.export_crf), "-b:v", "0"]
        if hw == "h264_qsv":
            return ["-c:v", "h264_qsv", "-global_quality", str(self.settings.export_crf)]
        return [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(self.settings.export_crf),
            "-pix_fmt", "yuv420p",
            "-threads", "2",
            "-x264-params", "threads=2:lookahead_threads=1",
        ]


VideoProcessor = VideoEngine
AudioProcessor = VideoEngine
