"""Callback query handlers with DB-backed state validation."""

from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy import select, update

from bot.config import get_settings
from bot.database.models import (
    ExportTarget,
    PartitionMode,
    PipelineMode,
    Task,
    TaskStatus,
)
from bot.database.session import get_session
from bot.filters import authorized_users_filter
from bot.middleware import ensure_user
from bot.states import (
    CB_EXPORT_BOTH,
    CB_EXPORT_TG,
    CB_EXPORT_YT,
    CB_LANG_BN,
    CB_LANG_EN,
    CB_LANG_ES,
    CB_LANG_HI,
    CB_LANG_ORIG,
    CB_MODE_RECAP,
    CB_MODE_TRANSFORM,
    CB_PART_FULL,
    CB_PART_SPLIT,
    CB_RIGHTS_ACK,
    CB_TTS_EDGE,
    CB_TTS_ELEVEN,
    CB_TTS_OPENAI,
    UIState,
)

logger = logging.getLogger(__name__)
auth = authorized_users_filter()


async def _load_task_for_user(task_id: int, telegram_id: int) -> Task | None:
    user = await ensure_user(telegram_id)
    async with get_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task is None or task.user_id != user.id:
            return None
        return task


def register_callback_handlers(app: Client) -> None:
    @app.on_callback_query(auth)
    async def on_callback(client: Client, query: CallbackQuery) -> None:
        data = query.data or ""
        if ":" not in data:
            await query.answer("Invalid action", show_alert=False)
            return
        action, tid_s = data.rsplit(":", 1)
        if not tid_s.isdigit():
            await query.answer("Invalid task", show_alert=False)
            return
        task_id = int(tid_s)
        task = await _load_task_for_user(task_id, query.from_user.id)
        if task is None:
            await query.answer("Task not found or not yours", show_alert=True)
            return

        settings = get_settings()

        if action == CB_RIGHTS_ACK:
            async with get_session() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        rights_acknowledged=True,
                        ui_state=UIState.CHOOSE_MODE.value,
                    )
                )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🎬 AI Recap", callback_data=f"{CB_MODE_RECAP}:{task_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🎞 Transformative Creator Edit",
                            callback_data=f"{CB_MODE_TRANSFORM}:{task_id}",
                        )
                    ],
                ]
            )
            await query.message.edit_text(
                f"Task <code>#{task_id}</code>\n\nChoose pipeline:",
                reply_markup=kb,
            )
            await query.answer()
            return

        if action in (CB_MODE_RECAP, CB_MODE_TRANSFORM):
            mode = (
                PipelineMode.AI_RECAP
                if action == CB_MODE_RECAP
                else PipelineMode.TRANSFORMATIVE
            )
            async with get_session() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(mode=mode, ui_state=UIState.CHOOSE_TTS.value)
                )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Edge TTS", callback_data=f"{CB_TTS_EDGE}:{task_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "ElevenLabs", callback_data=f"{CB_TTS_ELEVEN}:{task_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "OpenAI TTS", callback_data=f"{CB_TTS_OPENAI}:{task_id}"
                        )
                    ],
                ]
            )
            await query.message.edit_text(
                f"Task <code>#{task_id}</code>\n\nChoose voice provider:",
                reply_markup=kb,
            )
            await query.answer()
            return

        if action in (CB_TTS_EDGE, CB_TTS_ELEVEN, CB_TTS_OPENAI):
            provider = {
                CB_TTS_EDGE: "edge",
                CB_TTS_ELEVEN: "elevenlabs",
                CB_TTS_OPENAI: "openai",
            }[action]
            async with get_session() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(tts_provider=provider, ui_state=UIState.CHOOSE_LANGUAGE.value)
                )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Hindi", callback_data=f"{CB_LANG_HI}:{task_id}"),
                        InlineKeyboardButton("English", callback_data=f"{CB_LANG_EN}:{task_id}"),
                    ],
                    [
                        InlineKeyboardButton("Bengali", callback_data=f"{CB_LANG_BN}:{task_id}"),
                        InlineKeyboardButton("Spanish", callback_data=f"{CB_LANG_ES}:{task_id}"),
                    ],
                    [
                        InlineKeyboardButton(
                            "Original Audio", callback_data=f"{CB_LANG_ORIG}:{task_id}"
                        )
                    ],
                ]
            )
            await query.message.edit_text(
                f"Task <code>#{task_id}</code>\n\nChoose language:",
                reply_markup=kb,
            )
            await query.answer()
            return

        if action in (CB_LANG_HI, CB_LANG_EN, CB_LANG_BN, CB_LANG_ES, CB_LANG_ORIG):
            lang = {
                CB_LANG_HI: "hi",
                CB_LANG_EN: "en",
                CB_LANG_BN: "bn",
                CB_LANG_ES: "es",
                CB_LANG_ORIG: "original",
            }[action]
            voice_map = {
                "hi": settings.hindi_voice,
                "en": settings.english_voice,
                "bn": settings.bengali_voice,
                "es": settings.spanish_voice,
                "original": settings.english_voice,
            }
            async with get_session() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        language=lang,
                        tts_voice=voice_map.get(lang),
                        ui_state=UIState.CHOOSE_PARTITION.value,
                    )
                )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Full Video", callback_data=f"{CB_PART_FULL}:{task_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Part 1 + Part 2", callback_data=f"{CB_PART_SPLIT}:{task_id}"
                        )
                    ],
                ]
            )
            await query.message.edit_text(
                f"Task <code>#{task_id}</code>\n\nPartition:",
                reply_markup=kb,
            )
            await query.answer()
            return

        if action in (CB_PART_FULL, CB_PART_SPLIT):
            part = (
                PartitionMode.FULL
                if action == CB_PART_FULL
                else PartitionMode.SPLIT
            )
            async with get_session() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(partition_mode=part, ui_state=UIState.CHOOSE_EXPORT.value)
                )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Telegram", callback_data=f"{CB_EXPORT_TG}:{task_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "YouTube", callback_data=f"{CB_EXPORT_YT}:{task_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Both", callback_data=f"{CB_EXPORT_BOTH}:{task_id}"
                        )
                    ],
                ]
            )
            await query.message.edit_text(
                f"Task <code>#{task_id}</code>\n\nExport target:",
                reply_markup=kb,
            )
            await query.answer()
            return

        if action in (CB_EXPORT_TG, CB_EXPORT_YT, CB_EXPORT_BOTH):
            export = {
                CB_EXPORT_TG: ExportTarget.TELEGRAM,
                CB_EXPORT_YT: ExportTarget.YOUTUBE,
                CB_EXPORT_BOTH: ExportTarget.BOTH,
            }[action]
            async with get_session() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        export_target=export,
                        ui_state=UIState.QUEUED.value,
                        status=TaskStatus.QUEUED,
                        status_message_id=query.message.id,
                    )
                )
            await query.message.edit_text(
                f"⏳ Task <code>#{task_id}</code> queued for processing…"
            )
            await query.answer("Queued!")
            queue = client.queue_manager  # type: ignore[attr-defined]
            async with get_session() as session:
                result = await session.execute(select(Task).where(Task.id == task_id))
                t = result.scalar_one()
                priority = t.priority or 2
            await queue.enqueue(task_id, priority=priority)
            return

        await query.answer("Unknown action", show_alert=False)
