"""ASS subtitle generation with word-level timing and styling."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

from core.transcriber import Transcript, Word

logger = logging.getLogger(__name__)


class SubtitleEngine:
    def __init__(
        self, *,
        font_name: str = "DejaVu Sans",
        font_size: int = 48,
        primary_colour: str = "&H00FFFFFF",
        secondary_colour: str = "&H0000FFFF",
        outline_colour: str = "&H00000000",
        back_colour: str = "&H80000000",
        outline: float = 2.5,
        shadow: float = 1.5,
        margin_v: int = 40,
        margin_l: int = 40,
        margin_r: int = 40,
        max_chars_per_line: int = 42,
    ) -> None:
        self.font_name = font_name
        self.font_size = font_size
        self.primary_colour = primary_colour
        self.secondary_colour = secondary_colour
        self.outline_colour = outline_colour
        self.back_colour = back_colour
        self.outline = outline
        self.shadow = shadow
        self.margin_v = margin_v
        self.margin_l = margin_l
        self.margin_r = margin_r
        self.max_chars_per_line = max_chars_per_line

    @staticmethod
    def font_for_language(language: str | None) -> str:
        """Pick a system font that covers the script."""
        lang = (language or "en")[:2].lower()
        mapping = {
            "hi": "Noto Sans Devanagari",
            "bn": "Noto Sans Bengali",
            "ar": "Noto Sans Arabic",
            "zh": "Noto Sans CJK SC",
            "ja": "Noto Sans CJK JP",
            "ko": "Noto Sans CJK KR",
            "es": "DejaVu Sans",
            "en": "DejaVu Sans",
        }
        return mapping.get(lang, "DejaVu Sans")

    def generate_ass(
        self, transcript: Transcript, output_path: Path, *,
        script_text: Optional[str] = None, use_words: bool = True,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        header = self._header()
        events: list[str] = []
        if script_text and not use_words:
            events = self._from_plain_text(script_text, transcript.duration or 60.0)
        elif use_words and transcript.words:
            events = self._from_words(transcript.words)
        elif transcript.segments:
            events = self._from_segments(transcript.segments)
        elif script_text:
            events = self._from_plain_text(script_text, transcript.duration or 60.0)
        body = "\n".join(events)
        content = f"{header}\n{body}\n"
        output_path.write_text(content, encoding="utf-8")
        logger.info("Wrote ASS subtitles: %s (%s events)", output_path, len(events))
        return output_path

    def _header(self) -> str:
        return f"""[Script Info]
Title: YouTube Recap Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{self.font_name},{self.font_size},{self.primary_colour},{self.secondary_colour},{self.outline_colour},{self.back_colour},-1,0,0,0,100,100,0,0,1,{self.outline},{self.shadow},2,{self.margin_l},{self.margin_r},{self.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""

    def _from_words(self, words: List[Word]) -> List[str]:
        events: list[str] = []
        group: list[Word] = []
        char_count = 0
        for w in words:
            t = (w.word or "").strip()
            if not t:
                continue
            if group and (char_count + len(t) + 1 > self.max_chars_per_line):
                events.append(self._event(group))
                group, char_count = [], 0
            group.append(w)
            char_count += len(t) + 1
        if group:
            events.append(self._event(group))
        return events

    def _event(self, group: List[Word]) -> str:
        start = self._ts(group[0].start)
        end = self._ts(group[-1].end)
        text = " ".join((w.word or "").strip() for w in group)
        text = text.replace("\n", " ").replace("{", "\\{").replace("}", "\\}")
        return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"

    def _from_segments(self, segments) -> List[str]:
        events = []
        for seg in segments:
            text = (getattr(seg, "text", None) or "").strip()
            if not text:
                continue
            start = self._ts(getattr(seg, "start", 0.0))
            end = self._ts(getattr(seg, "end", getattr(seg, "start", 0.0) + 2.0))
            text = text.replace("\n", " ").replace("{", "\\{").replace("}", "\\}")
            events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
        return events

    def _from_plain_text(self, text: str, duration: float) -> List[str]:
        words = text.split()
        if not words:
            return []
        n = max(1, len(words) // 8)
        chunk_size = max(1, len(words) // n)
        events = []
        t = 0.0
        step = duration / max(1, (len(words) + chunk_size - 1) // chunk_size)
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i : i + chunk_size])
            start, end = self._ts(t), self._ts(min(duration, t + step))
            chunk = chunk.replace("{", "\\{").replace("}", "\\}")
            events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{chunk}")
            t += step
        return events

    @staticmethod
    def _ts(seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int((seconds - int(seconds)) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
