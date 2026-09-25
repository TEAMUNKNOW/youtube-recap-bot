"""Progress reporting helpers for Telegram status messages."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

STAGE_EMOJI = {
    "EXTRACTING_AUDIO": "🎧",
    "MUTE_VIDEO": "🔇",
    "MIX_AUDIO": "🎚️",
    "VALIDATING_OUTPUT": "🔎",
    "DOWNLOADING": "⬇️",
    "PROBING": "🔍",
    "TRANSCRIBING": "🎙",
    "SCRIPTING": "🧠",
    "TTS": "🔊",
    "AUDIO_PROCESSING": "🎧",
    "SUBTITLING": "📝",
    "RENDERING": "🎬",
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
    "SUBTITLING",
    "RENDERING",
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
    current = stage.upper()
    pct = max(0.0, min(100.0, float(percent)))
    emoji = STAGE_EMOJI.get(current, "⚙️")
    index = STAGE_ORDER.index(current) if current in STAGE_ORDER else -1
    completed = max(0, index)
    total = len(STAGE_ORDER)

    lines = ["<b>🎬 YouTube Recap</b>"]
    if task_id is not None:
        lines.append(f"<code>Task #{task_id}</code>")
    lines.append("")
    lines.append(f"<code>{_bar(pct, 20)}</code>  <b>{pct:.0f}%</b>")
    lines.append(f"{emoji} <b>{current.replace('_', ' ').title()}</b>  •  {completed}/{total} stages")
    lines.append("")

    # Compact pipeline map: completed → active → remaining.
    if index >= 0:
        before = "  ".join(f"✓ {STAGE_ORDER[i].replace('_', ' ').title()}" for i in range(max(0, index - 2), index))
        after = "  ".join(f"○ {STAGE_ORDER[i].replace('_', ' ').title()}" for i in range(index + 1, min(total, index + 3)))
        if before:
            lines.append(f"<blockquote>{before}</blockquote>")
        lines.append(f"<blockquote>▶ <b>{current.replace('_', ' ').title()}</b></blockquote>")
        if after:
            lines.append(f"<blockquote>{after}</blockquote>")
    else:
        lines.append(f"<blockquote>▶ <b>{current.replace('_', ' ').title()}</b></blockquote>")

    if extra:
        lines.append("")
        lines.append(f"💬 {extra}")
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
