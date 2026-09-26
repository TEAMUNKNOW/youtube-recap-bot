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
    CB_TTS_OMNI,
    CB_TTS_LOCAL,
    UIState,
    CB_MENU_HELP, CB_MENU_HOW, CB_MENU_VOICE, CB_MENU_SYNC, CB_MENU_THUMB, CB_MENU_OUTPUT, CB_MENU_SETTINGS,
    CB_RESULT_DETAILS, CB_RESULT_SYNC, CB_RESULT_RAW, CB_RESULT_RETRY, CB_RESULT_BACK,
)

logger = logging.getLogger(__name__)
auth = authorized_users_filter()


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Create Recap", callback_data="menu:create")],
        [InlineKeyboardButton("🎬 Shorts Factory", callback_data="sf:menu")],
        [
            InlineKeyboardButton("🔗 YouTube URL", callback_data="menu:url"),
            InlineKeyboardButton("📤 Upload Video", callback_data="menu:upload"),
        ],
        [
            InlineKeyboardButton("📊 My Tasks", callback_data="menu:tasks"),
            InlineKeyboardButton("❓ Help", callback_data=CB_MENU_HELP),
        ],
    ])


async def _load_task_for_user(task_id: int, telegram_id: int) -> Task | None:
    user = await ensure_user(telegram_id)
    async with get_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task is None or task.user_id != user.id:
            return None
        return task


def _tts_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎙️ OmniVoice (Hindi Adult)", callback_data=f"{CB_TTS_OMNI}:{task_id}")],
        [InlineKeyboardButton("Edge TTS", callback_data=f"{CB_TTS_EDGE}:{task_id}")],
        [InlineKeyboardButton("Local espeak", callback_data=f"{CB_TTS_LOCAL}:{task_id}")],
        [InlineKeyboardButton("ElevenLabs", callback_data=f"{CB_TTS_ELEVEN}:{task_id}")],
        [InlineKeyboardButton("OpenAI TTS", callback_data=f"{CB_TTS_OPENAI}:{task_id}")],
        [InlineKeyboardButton("◀️ Back", callback_data=f"nav:mode:{task_id}")],
    ])


def register_callback_handlers(app: Client) -> None:
    @app.on_callback_query(auth)
    async def on_callback(client: Client, query: CallbackQuery) -> None:
        data = query.data or ""
        help_pages = {
            CB_MENU_HELP: ("❓ <b>Help Center</b>", "Choose a topic below."),
            CB_MENU_HOW: ("🎬 <b>How It Works</b>", "1. Upload/URL → 2. Transcription → 3. Story → 4. TTS → 5. Render."),
            CB_MENU_VOICE: ("🎙️ <b>Voice System</b>", "OmniVoice (Hindi Adult), Edge, Local espeak, ElevenLabs, OpenAI. OmniVoice needs GPU."),
            CB_MENU_SYNC: ("🎞️ <b>Scene & Sync</b>", "AV sync checked against rendered duration."),
            CB_MENU_THUMB: ("🖼️ <b>Thumbnail</b>", "Best frame selected and branded."),
            CB_MENU_OUTPUT: ("📦 <b>Output</b>", "Final video + thumbnail delivered on completion."),
            CB_MENU_SETTINGS: ("⚙️ <b>Settings</b>", "Mode, voice provider, language, partition, export."),
        }
        if data in help_pages:
            title, body = help_pages[data]
            if data == CB_MENU_HELP:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 How It Works", callback_data=CB_MENU_HOW)],
                    [InlineKeyboardButton("🎙️ Voice System", callback_data=CB_MENU_VOICE)],
                    [InlineKeyboardButton("🎞️ Scene Sync", callback_data=CB_MENU_SYNC)],
                    [InlineKeyboardButton("🖼️ Thumbnail", callback_data=CB_MENU_THUMB)],
                    [InlineKeyboardButton("📦 Output", callback_data=CB_MENU_OUTPUT)],
                    [InlineKeyboardButton("⚙️ Settings", callback_data=CB_MENU_SETTINGS)],
                ])
            else:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Help", callback_data=CB_MENU_HELP)],
                    [InlineKeyboardButton("🏠 Home", callback_data="menu:home")],
                ])
            await query.message.edit_text(f"{title}\n\n{body}", reply_markup=kb)
            await query.answer()
            return
        if data == "menu:home":
            await query.message.edit_text(
                "👋 <b>YouTube Recap & Video Automation</b>\n\nChoose an option below.",
                reply_markup=main_menu_keyboard(),
            )
            await query.answer()
            return

        if data.startswith("menu:"):
            action = data.split(":", 1)[1]
            if action == "tasks":
                user = await ensure_user(query.from_user.id)
                async with get_session() as session:
                    result = await session.execute(
                        select(Task).where(Task.user_id == user.id).order_by(Task.id.desc()).limit(10)
                    )
                    tasks = result.scalars().all()
                rows = []
                if tasks:
                    for t in tasks:
                        rows.append([InlineKeyboardButton(
                            f"#{t.id} · {t.status.value}", callback_data=f"{CB_RESULT_DETAILS}:{t.id}"
                        )])
                    body = "📊 <b>My Tasks</b>\n\nSelect a task:"
                else:
                    body = "📊 <b>My Tasks</b>\n\nNo tasks yet."
                rows.append([InlineKeyboardButton("◀️ Back", callback_data="menu:home")])
                await query.message.edit_text(body, reply_markup=InlineKeyboardMarkup(rows))
                await query.answer()
                return
            prompts = {
                "create": "🎬 <b>Create Recap</b>\n\nSend a YouTube URL or upload a video file.",
                "url": "🔗 <b>YouTube URL</b>\n\nSend the YouTube link in your next message.",
                "upload": "📤 <b>Upload Video</b>\n\nSend the video/document in your next message.",
            }
            await query.message.edit_text(
                prompts.get(action, "Choose an option."),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu:home")]]),
            )
            await query.answer()
            return

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

        if action in ("nav:mode", "nav:tts", "nav:lang", "nav:partition"):
            if action == "nav:mode":
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 AI Recap", callback_data=f"{CB_MODE_RECAP}:{task_id}")],
                    [InlineKeyboardButton("🎞 Transformative Creator Edit", callback_data=f"{CB_MODE_TRANSFORM}:{task_id}")],
                ])
                text = f"Task <code>#{task_id}</code>\n\nChoose pipeline:"
            elif action == "nav:tts":
                kb = _tts_keyboard(task_id)
                text = f"Task <code>#{task_id}</code>\n\nChoose voice provider:"
            elif action == "nav:lang":
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Hindi", callback_data=f"{CB_LANG_HI}:{task_id}"), InlineKeyboardButton("English", callback_data=f"{CB_LANG_EN}:{task_id}")],
                    [InlineKeyboardButton("Bengali", callback_data=f"{CB_LANG_BN}:{task_id}"), InlineKeyboardButton("Spanish", callback_data=f"{CB_LANG_ES}:{task_id}")],
                    [InlineKeyboardButton("Original Audio", callback_data=f"{CB_LANG_ORIG}:{task_id}")],
                    [InlineKeyboardButton("◀️ Back", callback_data=f"nav:tts:{task_id}")],
                ])
                text = f"Task <code>#{task_id}</code>\n\nChoose language:"
            else:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Full Video", callback_data=f"{CB_PART_FULL}:{task_id}")],
                    [InlineKeyboardButton("Part 1 + Part 2", callback_data=f"{CB_PART_SPLIT}:{task_id}")],
                    [InlineKeyboardButton("◀️ Back", callback_data=f"nav:lang:{task_id}")],
                ])
                text = f"Task <code>#{task_id}</code>\n\nPartition:"
            await query.message.edit_text(text, reply_markup=kb)
            await query.answer()
            return

        if action == "nav:home":
            await query.message.edit_text(
                "👋 <b>YouTube Recap & Video Automation</b>\n\nChoose an option below.",
                reply_markup=main_menu_keyboard(),
            )
            await query.answer()
            return

        if action == CB_RIGHTS_ACK:
            async with get_session() as session:
                await session.execute(
                    update(Task).where(Task.id == task_id).values(
                        rights_acknowledged=True, ui_state=UIState.CHOOSE_MODE.value,
                    )
                )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 AI Recap", callback_data=f"{CB_MODE_RECAP}:{task_id}")],
                [InlineKeyboardButton("🎞 Transformative Creator Edit", callback_data=f"{CB_MODE_TRANSFORM}:{task_id}")],
                [InlineKeyboardButton("◀️ Back", callback_data=f"nav:home:{task_id}")],
            ])
            await query.message.edit_text(f"Task <code>#{task_id}</code>\n\nChoose pipeline:", reply_markup=kb)
            await query.answer()
            return

        if action in (CB_MODE_RECAP, CB_MODE_TRANSFORM):
            mode = PipelineMode.AI_RECAP if action == CB_MODE_RECAP else PipelineMode.TRANSFORMATIVE
            async with get_session() as session:
                await session.execute(
                    update(Task).where(Task.id == task_id).values(mode=mode, ui_state=UIState.CHOOSE_TTS.value)
                )
            await query.message.edit_text(
                f"Task <code>#{task_id}</code>\n\nChoose voice provider:",
                reply_markup=_tts_keyboard(task_id),
            )
            await query.answer()
            return

        if action in (CB_TTS_EDGE, CB_TTS_ELEVEN, CB_TTS_OPENAI, CB_TTS_OMNI, CB_TTS_LOCAL):
            provider = {
                CB_TTS_EDGE: "edge",
                CB_TTS_ELEVEN: "elevenlabs",
                CB_TTS_OPENAI: "openai",
                CB_TTS_OMNI: "omnivoice",
                CB_TTS_LOCAL: "local",
            }[action]
            async with get_session() as session:
                await session.execute(
                    update(Task).where(Task.id == task_id).values(
                        tts_provider=provider, ui_state=UIState.CHOOSE_LANGUAGE.value,
                    )
                )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Hindi", callback_data=f"{CB_LANG_HI}:{task_id}"), InlineKeyboardButton("English", callback_data=f"{CB_LANG_EN}:{task_id}")],
                [InlineKeyboardButton("Bengali", callback_data=f"{CB_LANG_BN}:{task_id}"), InlineKeyboardButton("Spanish", callback_data=f"{CB_LANG_ES}:{task_id}")],
                [InlineKeyboardButton("Original Audio", callback_data=f"{CB_LANG_ORIG}:{task_id}")],
                [InlineKeyboardButton("◀️ Back", callback_data=f"nav:tts:{task_id}")],
            ])
            await query.message.edit_text(f"Task <code>#{task_id}</code>\n\nChoose language:", reply_markup=kb)
            await query.answer()
            return

        if action in (CB_LANG_HI, CB_LANG_EN, CB_LANG_BN, CB_LANG_ES, CB_LANG_ORIG):
            lang = {
                CB_LANG_HI: "hi", CB_LANG_EN: "en", CB_LANG_BN: "bn",
                CB_LANG_ES: "es", CB_LANG_ORIG: "original",
            }[action]
            voice_map = {
                "hi": settings.hindi_voice, "en": settings.english_voice,
                "bn": settings.bengali_voice, "es": settings.spanish_voice,
                "original": settings.english_voice,
            }
            omni_voice_map = {
                "hi": "hi-adult-male", "en": "en-male",
                "bn": "hi-adult-male", "es": "en-male", "original": "en-male",
            }
            async with get_session() as session:
                task_row = await session.get(Task, task_id)
                use_omni = bool(task_row and (task_row.tts_provider or "") == "omnivoice")
                chosen_voice = omni_voice_map.get(lang) if use_omni else voice_map.get(lang)
                await session.execute(
                    update(Task).where(Task.id == task_id).values(
                        language=lang,
                        tts_voice=chosen_voice,
                        ui_state=UIState.CHOOSE_PARTITION.value,
                    )
                )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Full Video", callback_data=f"{CB_PART_FULL}:{task_id}")],
                [InlineKeyboardButton("Part 1 + Part 2", callback_data=f"{CB_PART_SPLIT}:{task_id}")],
                [InlineKeyboardButton("◀️ Back", callback_data=f"nav:lang:{task_id}")],
            ])
            await query.message.edit_text(f"Task <code>#{task_id}</code>\n\nPartition:", reply_markup=kb)
            await query.answer()
            return

        if action in (CB_PART_FULL, CB_PART_SPLIT):
            part = PartitionMode.FULL if action == CB_PART_FULL else PartitionMode.SPLIT
            async with get_session() as session:
                await session.execute(
                    update(Task).where(Task.id == task_id).values(
                        partition_mode=part, ui_state=UIState.CHOOSE_EXPORT.value,
                    )
                )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Telegram only", callback_data=f"{CB_EXPORT_TG}:{task_id}")],
                [InlineKeyboardButton("YouTube only", callback_data=f"{CB_EXPORT_YT}:{task_id}")],
                [InlineKeyboardButton("Both", callback_data=f"{CB_EXPORT_BOTH}:{task_id}")],
                [InlineKeyboardButton("◀️ Back", callback_data=f"nav:partition:{task_id}")],
            ])
            await query.message.edit_text(f"Task <code>#{task_id}</code>\n\nExport target:", reply_markup=kb)
            await query.answer()
            return

        if action in (CB_EXPORT_TG, CB_EXPORT_YT, CB_EXPORT_BOTH):
            target = {
                CB_EXPORT_TG: ExportTarget.TELEGRAM,
                CB_EXPORT_YT: ExportTarget.YOUTUBE,
                CB_EXPORT_BOTH: ExportTarget.BOTH,
            }[action]
            async with get_session() as session:
                await session.execute(
                    update(Task).where(Task.id == task_id).values(
                        export_target=target,
                        status=TaskStatus.QUEUED,
                        ui_state=UIState.QUEUED.value,
                    )
                )
            queue = client.queue_manager  # type: ignore[attr-defined]
            await queue.enqueue(task_id, priority=task.priority or 1)
            await query.message.edit_text(
                f"⏳ Task <code>#{task_id}</code> queued for processing…"
            )
            await query.answer()
            return

        if action in (CB_RESULT_DETAILS, CB_RESULT_SYNC, CB_RESULT_RAW, CB_RESULT_RETRY, CB_RESULT_BACK):
            if action == CB_RESULT_BACK:
                await query.message.edit_text(
                    f"✅ <b>Task #{task_id} completed</b>",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📊 Details", callback_data=f"{CB_RESULT_DETAILS}:{task_id}")],
                    ]),
                )
                await query.answer()
                return
            if action == CB_RESULT_DETAILS:
                await query.message.edit_text(
                    f"📊 <b>Task #{task_id}</b>\nStatus: {task.status.value}\nProgress: {task.progress:.0f}%",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data=f"{CB_RESULT_BACK}:{task_id}")],
                    ]),
                )
                await query.answer()
                return
            if action == CB_RESULT_RETRY:
                if task.status != TaskStatus.FAILED:
                    await query.answer("Retry only for failed tasks.", show_alert=True)
                    return
                async with get_session() as session:
                    await session.execute(
                        update(Task).where(Task.id == task_id).values(
                            status=TaskStatus.QUEUED, progress=0.0,
                            error_code=None, error_message=None,
                        )
                    )
                queue = client.queue_manager  # type: ignore[attr-defined]
                await queue.enqueue(task_id, priority=task.priority or 2)
                await query.answer("Retry queued")
                return
            await query.answer()
            return

        await query.answer("Unknown action", show_alert=False)
