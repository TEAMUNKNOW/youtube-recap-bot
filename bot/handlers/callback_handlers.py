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
    CB_MENU_HELP, CB_MENU_HOW, CB_MENU_VOICE, CB_MENU_SYNC, CB_MENU_THUMB, CB_MENU_OUTPUT, CB_MENU_SETTINGS,
    CB_RESULT_DETAILS, CB_RESULT_SYNC, CB_RESULT_RAW, CB_RESULT_RETRY, CB_RESULT_BACK,
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
        help_pages = {
            CB_MENU_HELP: ("❓ <b>Help Center</b>", "Choose a topic below."),
            CB_MENU_HOW: ("🎬 <b>How It Works</b>", "1. Upload/URL → 2. Transcription → 3. Story segmentation → 4. Long-form narration → 5. TTS → 6. Subtitles → 7. Render → 8. Validation → 9. Story thumbnail."),
            CB_MENU_VOICE: ("🎙️ <b>Voice System</b>", "The pipeline uses neural TTS with chunked synthesis, retries and provider failover. Narration is generated for the video's actual coverage instead of a fixed 4-minute cap."),
            CB_MENU_SYNC: ("🎞️ <b>Scene & Sync</b>", "Long videos are split into timestamped story sections. Narration is generated per section and the final output is checked against the rendered duration."),
            CB_MENU_THUMB: ("🖼️ <b>Thumbnail</b>", "Thumbnail candidates include story chapter moments plus key timeline frames. The clearest/highest-information frame is selected and branded with the generated story title."),
            CB_MENU_OUTPUT: ("📦 <b>Output & Raw Files</b>", "After completion you get the final video and thumbnail. Raw narration, transcript, script, subtitles and other task files remain available for diagnostics."),
            CB_MENU_SETTINGS: ("⚙️ <b>Settings</b>", "Task settings include mode, voice provider, language, partition and export target. Failed provider configuration is handled through fallback where available."),
        }
        if data in help_pages:
            title, body = help_pages[data]
            if data == CB_MENU_HELP:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 How It Works", callback_data=CB_MENU_HOW)],
                    [InlineKeyboardButton("🎙️ Voice System", callback_data=CB_MENU_VOICE)],
                    [InlineKeyboardButton("🎞️ Scene Sync", callback_data=CB_MENU_SYNC)],
                    [InlineKeyboardButton("🖼️ Thumbnail", callback_data=CB_MENU_THUMB)],
                    [InlineKeyboardButton("📦 Output & Raw Files", callback_data=CB_MENU_OUTPUT)],
                    [InlineKeyboardButton("⚙️ Settings", callback_data=CB_MENU_SETTINGS)],
                ])
            else:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Help", callback_data=CB_MENU_HELP)]])
            await query.message.edit_text(f"{title}\n\n{body}", reply_markup=kb)
            await query.answer()
            return
        if data.startswith("menu:"):
            action = data.split(":", 1)[1]
            prompts = {
                "create": "🎬 <b>Create Recap</b>\n\nSend a YouTube URL or upload a video file.",
                "url": "🔗 <b>YouTube URL</b>\n\nSend the YouTube link in your next message.",
                "upload": "📤 <b>Upload Video</b>\n\nSend the video/document in your next message.",
                "voice": "🎙️ <b>Voice</b>\n\nVoice is selected per task after you provide the source.",
                "lang": "🌐 <b>Language</b>\n\nLanguage is selected per task after the source is received.",
                "thumb": "🖼️ <b>Thumbnail</b>\n\nThumbnail is generated automatically from story/chapter moments.",
                "tasks": "📊 <b>My Tasks</b>\n\nUse /tasks for recent tasks and /status for active processing.",
            }
            await query.message.edit_text(prompts.get(action, "Choose an option."))
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

        if action in (CB_RESULT_DETAILS, CB_RESULT_SYNC, CB_RESULT_RAW, CB_RESULT_RETRY, CB_RESULT_BACK):
            if action == CB_RESULT_BACK:
                await query.message.edit_text(f"✅ <b>Task #{task_id} completed</b>", reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Details", callback_data=f"{CB_RESULT_DETAILS}:{task_id}"), InlineKeyboardButton("🔍 Sync", callback_data=f"{CB_RESULT_SYNC}:{task_id}")],
                    [InlineKeyboardButton("🧪 Raw Files", callback_data=f"{CB_RESULT_RAW}:{task_id}")],
                ]))
                await query.answer()
                return
            if action == CB_RESULT_DETAILS:
                meta = task.metadata_json or {}
                text = (
                    f"📊 <b>Task #{task_id} Details</b>\n\n"
                    f"Source: {float(meta.get('source_duration', task.duration or 0))/60:.1f} min\n"
                    f"Narration: {float(meta.get('narration_duration', 0))/60:.1f} min\n"
                    f"Output: {float(meta.get('output_duration', 0))/60:.1f} min\n"
                    f"Words: {meta.get('word_count', 0)}\n"
                    f"Sync offset: {float(meta.get('sync_offset_seconds', 0)):.2f}s\n"
                    f"Max chapter drift: {float(meta.get('max_chapter_drift_seconds', 0)):.2f}s\n"
                    f"Sync status: {'✅' if meta.get('sync_within_tolerance') else '⚠️'}"
                )
                await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"{CB_RESULT_BACK}:{task_id}")]]))
                await query.answer()
                return
            if action == CB_RESULT_SYNC:
                meta = task.metadata_json or {}
                offset = float(meta.get("sync_offset_seconds", 0))
                ok = bool(meta.get("sync_within_tolerance"))
                await query.message.edit_text(
                    f"{'✅' if ok else '⚠️'} <b>Sync Check</b>\n\nNarration/output offset: <code>{offset:.2f}s</code>\nMax chapter drift: <code>{float(meta.get('max_chapter_drift_seconds', 0)):.2f}s</code>\nTolerance: <code>{get_settings().av_sync_tolerance_seconds:.1f}s</code>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧪 Raw Files", callback_data=f"{CB_RESULT_RAW}:{task_id}"), InlineKeyboardButton("🔙 Back", callback_data=f"{CB_RESULT_BACK}:{task_id}")]])
                )
                await query.answer()
                return
            if action == CB_RESULT_RAW:
                import zipfile
                from pathlib import Path
                ws = Path(get_settings().workspace_root) / str(task_id)
                candidates = [
                    Path(task.source_file) if task.source_file else None,
                    ws / "narration.mp3",
                    ws / "narration_fitted.m4a",
                    ws / "script.txt",
                    ws / "transcript.txt",
                    ws / "subtitles.ass",
                    ws / "muted.mp4",
                    ws / "mixed_audio.m4a",
                ]
                sent = 0
                uploader = getattr(client, "user_client", None) or client
                for path in candidates:
                    if path and path.exists():
                        try:
                            await uploader.send_document(query.message.chat.id, str(path), caption=f"🧪 Raw: {path.name}")
                            sent += 1
                        except Exception:
                            logger.exception("Failed sending raw file %s", path)
                await query.answer(f"Sent {sent} raw files")
                return
            if action == CB_RESULT_RETRY:
                await query.answer("Retrying is not wired yet; use /cancel and create a new task.", show_alert=True)
                return

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
