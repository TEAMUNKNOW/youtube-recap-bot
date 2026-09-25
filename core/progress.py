"""Progress reporting helpers for Telegram status messages."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

STAGE_EMOJI = {
    "DOWNLOADING": "⬇️",
    "PROBING": "🔍",
    "TRANSCRIBING": "🎙",
    "SCRIPTING": "🧠",
    "TTS": "🔊",
    "AUDIO_PROCESSING": "🎧",
    "RENDERING": "🎬",
    "SUBTITLING": "📝",
    "THUMBNAIL": "🖼",
    "SEO": "🏷",
    "VALIDATING": "✅",
    "UPLOADING": "📤",
    "SCHEDULED": "📅",
    "COMPLETED": "🎉",
    "FAILED": "❌",
    "CANCELLED": "🚫",
    "QUEUED": "⏳",
    "RECOVERING": "🔄",
}

STAGE_ORDER = [
    "DOWNLOADING",
    "PROBING",
    "EXTRACTING_AUDIO",
    "TRANSCRIBING",
    "SCRIPTING",
    "TTS",
    "MUTE_VIDEO",
    "MIX_AUDIO",
    "RENDERING",
    "SUBTITLING",
    "THUMBNAIL",
    "VALIDATING_OUTPUT",
    "SEO",
    "UPLOADING",
]


def format_progress(
    stage: str,
    percent: float,
    *,
    extra: Optional[str] = None,
    task_id: Optional[int] = None,
) -> str:
    lines = ["<b>Processing…</b>"]
    if task_id is not None:
        lines.append(f"<code>Task #{task_id}</code>")

    current = stage.upper()
    for s in STAGE_ORDER:
        emoji = STAGE_EMOJI.get(s, "•")
        if s == current:
            bar = _bar(percent)
            lines.append(f"{emoji} <b>{s.title()}</b>  {bar}  {percent:.0f}%")
        elif current in STAGE_ORDER and STAGE_ORDER.index(s) < STAGE_ORDER.index(current):
            lines.append(f"{emoji} {s.title()}  ✓")
        else:
            lines.append(f"{emoji} {s.title()}  —")

    if extra:
        lines.append(f"\n{extra}")
    return "\n".join(lines)


def _bar(percent: float, width: int = 10) -> str:
    filled = int(max(0, min(100, percent)) / 100 * width)
    return "█" * filled + "░" * (width - filled)


def format_completed(task_id: int, youtube_id: Optional[str] = None) -> str:
    msg = f"✅ <b>Task #{task_id} completed</b>"
    if youtube_id:
        msg += f"\n🎬 YouTube: https://youtu.be/{youtube_id}"
    return msg


def format_failed(task_id: int, user_message: str) -> str:
    return f"❌ <b>Task #{task_id} failed</b>\n{user_message}"
