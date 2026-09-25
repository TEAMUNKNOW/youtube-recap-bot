"""Thumbnail generation from keyframes with visual heuristics and text overlay."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Optional

from bot.config import Settings
from bot.exceptions import FFmpegError, ValidationError

logger = logging.getLogger(__name__)


class ThumbnailStudio:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(
        self, video: Path, output: Path, *, title: str, duration: float, chapters=None, count: int = 8,
    ) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        frames_dir = output.parent / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        start = max(1.0, duration * 0.05)
        end = max(start + 1, duration * 0.95)
        if duration < 5:
            start, end = 0.5, max(1.0, duration - 0.5)
        chapter_times = [
            float(getattr(ch, "start_seconds", 0.0))
            for ch in (chapters or [])
            if 0.0 <= float(getattr(ch, "start_seconds", 0.0)) <= max(duration - 0.5, 0.5)
        ]
        base_times = [start + (end - start) * i / max(1, count - 1) for i in range(count)]
        timestamps = list(dict.fromkeys(chapter_times[:max(2, count // 2)] + base_times))[:count]
        frame_paths: list[Path] = []
        for i, ts in enumerate(timestamps):
            fp = frames_dir / f"frame_{i:02d}.jpg"
            await self._extract_frame(video, fp, ts)
            if fp.exists():
                frame_paths.append(fp)
        if not frame_paths:
            raise FFmpegError("Could not extract any keyframes", retryable=False)
        best = await asyncio.to_thread(self._score_frames, frame_paths)
        final = await asyncio.to_thread(self._compose, best, output, title)
        if not final.exists() or final.stat().st_size < 500:
            raise ValidationError("Thumbnail generation failed validation")
        return final

    async def _extract_frame(self, video: Path, output: Path, ts: float) -> None:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{ts:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()

    def _score_frames(self, paths: List[Path]) -> Path:
        try:
            from PIL import Image, ImageStat
        except ImportError:
            return paths[len(paths) // 2]
        best_path = paths[0]
        best_score = -1.0
        for p in paths:
            try:
                img = Image.open(p).convert("RGB")
                img_s = img.resize((160, 90))
                stat = ImageStat.Stat(img_s)
                mean = sum(stat.mean) / 3
                var = sum(stat.var) / 3
                if mean < 30 or mean > 230:
                    score = var * 0.2
                else:
                    score = var * (1.0 - abs(mean - 128) / 128)
                if score > best_score:
                    best_score = score
                    best_path = p
            except Exception:
                continue
        return best_path

    def _compose(self, frame: Path, output: Path, title: str) -> Path:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFont
        img = Image.open(frame).convert("RGB")
        img = img.resize((1280, 720), Image.Resampling.LANCZOS)
        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Color(img).enhance(1.2)
        img = ImageEnhance.Sharpness(img).enhance(1.3)
        draw = ImageDraw.Draw(img)
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for y in range(520, 720):
            alpha = int(180 * (y - 520) / 200)
            od.rectangle([(0, y), (1280, y + 1)], fill=(0, 0, 0, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        font = self._load_font(52)
        text = title[:80]
        lines = self._wrap_text(text, font, 1100, draw)
        y = 560
        for line in lines[:3]:
            for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2), (0, -2), (0, 2)]:
                draw.text((64 + dx, y + dy), line, font=font, fill=(0, 0, 0))
            draw.text((64, y), line, font=font, fill=(255, 220, 50))
            y += 58
        img.save(output, "JPEG", quality=92)
        return output

    def _load_font(self, size: int):
        from PIL import ImageFont
        candidates = [
            self.settings.font_directory / self.settings.default_font,
            self.settings.font_directory / "Montserrat-Bold.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ]
        for c in candidates:
            if c.exists():
                try:
                    return ImageFont.truetype(str(c), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    @staticmethod
    def _wrap_text(text: str, font, max_width: int, draw) -> List[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for w in words:
            test = f"{current} {w}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = w
        if current:
            lines.append(current)
        return lines
