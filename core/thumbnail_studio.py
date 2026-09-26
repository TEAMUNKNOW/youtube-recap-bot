"""Thumbnail generation with high-quality frame pick + cinematic title card."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, List, Optional

from bot.config import Settings

logger = logging.getLogger(__name__)


class ThumbnailStudio:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(
        self,
        video: Path,
        output: Path,
        *,
        title: str,
        duration: float = 0.0,
        chapters: Optional[List[Any]] = None,
    ) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        frames_dir = output.parent / "thumb_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        timestamps = self._pick_timestamps(duration, chapters)
        extracted: list[Path] = []
        for i, ts in enumerate(timestamps):
            frame = frames_dir / f"frame_{i:02d}.jpg"
            ok = await self._extract_frame(video, frame, max(0.5, ts))
            if ok:
                extracted.append(frame)

        best = self._score_frames(extracted) if extracted else None
        if best is None:
            return self._title_card(output, title or "Recap")

        return self._compose(best, output, title or "Recap")

    def _pick_timestamps(self, duration: float, chapters: Optional[List[Any]]) -> list[float]:
        stamps: list[float] = []
        if chapters:
            for ch in chapters[:8]:
                try:
                    stamps.append(float(getattr(ch, "start_seconds", None) or ch.get("start_seconds", 0)))
                except Exception:
                    continue
        if duration and duration > 10:
            for pct in (0.12, 0.28, 0.45, 0.62, 0.78):
                stamps.append(duration * pct)
        elif not stamps:
            stamps = [1.0, 3.0, 5.0, 8.0]
        out = sorted({round(max(0.3, s), 2) for s in stamps})
        return out[:12]

    async def _extract_frame(self, video: Path, out: Path, ts: float) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{ts:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-q:v", "2",
            str(out),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0 and out.exists() and out.stat().st_size > 2000

    def _score_frames(self, paths: list[Path]) -> Optional[Path]:
        try:
            from PIL import Image, ImageStat
        except ImportError:
            return paths[0] if paths else None
        best_path = None
        best_score = -1.0
        for p in paths:
            try:
                img = Image.open(p).convert("RGB")
                w, h = img.size
                if w < 320 or h < 180:
                    continue
                img_s = img.resize((160, 90))
                stat = ImageStat.Stat(img_s)
                mean = sum(stat.mean) / 3.0
                var = sum(stat.var) / 3.0
                if mean < 25 or mean > 235:
                    score = var * 0.15
                else:
                    score = var * (1.0 - abs(mean - 120) / 140)
                score += 0.01 * paths.index(p)
                if score > best_score:
                    best_score = score
                    best_path = p
            except Exception:
                continue
        return best_path

    def _compose(self, frame: Path, output: Path, title: str) -> Path:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

        img = Image.open(frame).convert("RGB")
        img = img.resize((1280, 720), Image.Resampling.LANCZOS)
        img = ImageEnhance.Contrast(img).enhance(1.25)
        img = ImageEnhance.Color(img).enhance(1.15)
        img = ImageEnhance.Sharpness(img).enhance(1.4)

        base = img.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for y in range(0, 90):
            a = int(90 * (1 - y / 90))
            od.rectangle([(0, y), (1280, y + 1)], fill=(0, 0, 0, a))
        for y in range(400, 720):
            a = int(210 * ((y - 400) / 320) ** 1.1)
            od.rectangle([(0, y), (1280, y + 1)], fill=(0, 0, 0, min(220, a)))
        od.rectangle([(48, 640), (200, 648)], fill=(255, 200, 40, 255))

        composed = Image.alpha_composite(base, overlay).convert("RGB")
        draw = ImageDraw.Draw(composed)

        font_lg = self._load_font(58)
        font_sm = self._load_font(28)
        text = (title or "Recap").strip()
        if len(text) > 70:
            text = text[:67] + "…"
        lines = self._wrap_text(text, font_lg, 1180, draw)

        y = 520 if len(lines) <= 2 else 480
        for line in lines[:3]:
            for dx, dy in [(0, 3), (2, 2), (-1, 2)]:
                draw.text((52 + dx, y + dy), line, font=font_lg, fill=(0, 0, 0))
            draw.text((52, y), line, font=font_lg, fill=(255, 255, 255))
            y += 64

        draw.text((52, 670), "AI RECAP", font=font_sm, fill=(255, 210, 60))
        composed = composed.filter(ImageFilter.UnsharpMask(radius=1.2, percent=80, threshold=2))
        composed.save(output, "JPEG", quality=95, optimize=True)
        logger.info("Thumbnail written %s", output)
        return output

    def _title_card(self, output: Path, title: str) -> Path:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (1280, 720), (18, 18, 24))
        draw = ImageDraw.Draw(img)
        font = self._load_font(56)
        lines = self._wrap_text(title[:80], font, 1100, draw)
        y = 300
        for line in lines[:3]:
            draw.text((60, y), line, font=font, fill=(255, 220, 80))
            y += 70
        img.save(output, "JPEG", quality=95)
        return output

    def _load_font(self, size: int):
        from PIL import ImageFont
        candidates = [
            Path(self.settings.font_directory) / getattr(self.settings, "default_font", "NotoSans-Bold.ttf"),
            Path(self.settings.font_directory) / "Montserrat-Bold.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf"),
        ]
        for c in candidates:
            try:
                if c.exists():
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
