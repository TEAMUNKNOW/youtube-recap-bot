from __future__ import annotations

from pathlib import Path

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot.config import get_settings
from bot.database.models import (
    ShortsClipStatus,
    ShortsProject,
    ShortsProjectStatus,
    ShortsSelectionMode,
    ShortsSettings,
    ShortClip,
    YouTubeAccount,
)
from bot.database.session import get_session
from bot.middleware import ensure_user
from core.shorts.oauth import YouTubeOAuth


async def _project(pid: int, uid: int):
    user = await ensure_user(uid)
    async with get_session() as s:
        p = await s.get(ShortsProject, pid)
        return p if p and p.user_id == user.id else None


def _back(callback: str = "sf:menu", label: str = "◀️ Back"):
    return [InlineKeyboardButton(label, callback_data=callback)]


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Create Shorts", callback_data="sf:create"),
            InlineKeyboardButton("📊 Queue", callback_data="sf:queue"),
        ],
        [
            InlineKeyboardButton("📺 YouTube", callback_data="sf:youtube"),
            InlineKeyboardButton("📅 Schedule", callback_data="sf:schedule"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="sf:settings"),
            InlineKeyboardButton("📚 Help", callback_data="sf:help"),
        ],
        _back("menu:home"),
    ])


def _project_home_keyboard(pid: int, status: ShortsProjectStatus):
    rows = []
    if status in (
        ShortsProjectStatus.PAUSED,
        ShortsProjectStatus.MANIFEST_READY,
        ShortsProjectStatus.ANALYZING,
    ):
        rows.append([InlineKeyboardButton("▶️ Start / Resume", callback_data=f"sf:start:{pid}")])
    elif status == ShortsProjectStatus.RUNNING:
        rows.append([
            InlineKeyboardButton("⏸ Pause", callback_data=f"sf:pause:{pid}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"sf:stop:{pid}"),
        ])
    rows.extend([
        [
            InlineKeyboardButton("📊 Progress", callback_data=f"sf:progress:{pid}"),
            InlineKeyboardButton("📋 Parts", callback_data=f"sf:parts:{pid}:0"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data=f"sf:project:{pid}"),
            InlineKeyboardButton("📅 Schedule", callback_data=f"sf:project_schedule:{pid}"),
        ],
        _back("sf:menu"),
    ])
    return InlineKeyboardMarkup(rows)


async def _show_project(client, query, pid: int, uid: int):
    p = await _project(pid, uid)
    if not p:
        await query.answer("Project not found.", show_alert=True)
        return
    text = (
        f"🎬 <b>Shorts Project #{pid}</b>\n\n"
        f"Mode: <b>{'Continuous Series' if p.selection_mode.value == 'CONTINUOUS' else 'Highlight Clips'}</b>\n"
        f"Duration: <b>{p.clip_duration}s</b>\n"
        f"Speed: <b>{p.playback_speed}x</b>\n"
        f"Status: <b>{p.status.value}</b>\n"
        f"Parts: <b>{p.processed_parts}/{p.total_parts}</b>"
    )
    await query.message.edit_text(text, reply_markup=_project_home_keyboard(pid, p.status))
    await query.answer()


async def dispatch(client, query):
    data = query.data or ""
    uid = query.from_user.id
    parts = data.split(":")

    if data == "sf:menu":
        await query.message.edit_text(
            "🎬 <b>Shorts Factory</b>\n\nAutonomous long-video → Shorts processing, rendering and scheduling.",
            reply_markup=menu(),
        )
        await query.answer()
        return

    if data == "sf:create":
        client.shorts_sessions[uid] = {"active": True}
        await query.message.edit_text(
            "➕ <b>Create Shorts</b>\n\n"
            "Send a YouTube URL or upload a video.\n\n"
            "Default: Continuous Series · 60 sec · 1.5x\n\n"
            "You must have the rights/permission to repurpose the content.",
            reply_markup=InlineKeyboardMarkup([_back("sf:cancel_input")]),
        )
        await query.answer()
        return

    if data == "sf:cancel_input":
        client.shorts_sessions.pop(uid, None)
        await query.message.edit_text("🎬 <b>Shorts Factory</b>", reply_markup=menu())
        await query.answer()
        return

    if data == "sf:youtube":
        user = await ensure_user(uid)
        async with get_session() as s:
            q = await s.execute(
                select(YouTubeAccount).where(
                    YouTubeAccount.user_id == user.id,
                    YouTubeAccount.revoked_at.is_(None),
                )
            )
            accounts = q.scalars().all()
        rows = []
        for a in accounts:
            rows.append([InlineKeyboardButton(
                f"❌ Disconnect {a.channel_title}",
                callback_data=f"sf:disconnect:{a.id}",
            )])
        try:
            url = await YouTubeOAuth(get_settings()).authorization_url(user.id)
            rows.insert(0, [InlineKeyboardButton("🔐 Authorize Google / YouTube", url=url)])
            body = "📺 <b>Connect YouTube</b>\n\n" + (
                "\n".join(f"✅ {a.channel_title}" for a in accounts)
                if accounts else "No channel connected."
            )
            rows.append(_back("sf:menu"))
            await query.message.edit_text(body, reply_markup=InlineKeyboardMarkup(rows))
        except Exception as exc:
            await query.message.edit_text(
                f"❌ YouTube OAuth is not configured.\n\n{str(exc)[:500]}",
                reply_markup=InlineKeyboardMarkup([_back("sf:menu")]),
            )
        await query.answer()
        return

    if len(parts) >= 3 and parts[1] == "disconnect":
        aid = int(parts[2])
        user = await ensure_user(uid)
        await YouTubeOAuth(get_settings()).disconnect(aid, user.id)
        await query.message.edit_text(
            "📺 YouTube connection revoked.",
            reply_markup=InlineKeyboardMarkup([_back("sf:youtube")]),
        )
        await query.answer()
        return

    if data == "sf:help":
        await query.message.edit_text(
            "📚 <b>Shorts Factory Help</b>\n\n"
            "Continuous Series covers the full source timeline without intentional gaps. "
            "Highlight Clips selects meaningful moments and may skip sections. "
            "Processing is checkpointed and can resume after restarts.",
            reply_markup=InlineKeyboardMarkup([_back("sf:menu")]),
        )
        await query.answer()
        return

    if data == "sf:settings":
        await query.message.edit_text(
            "⚙️ <b>Shorts Settings</b>\n\n"
            "Default duration: 60s\nPlayback: 1.5x\n"
            "Output: 1080×1920 MP4\nTimezone: Asia/Kolkata\n"
            "Daily limit: 3 (max 5)",
            reply_markup=InlineKeyboardMarkup([_back("sf:menu")]),
        )
        await query.answer()
        return

    if data == "sf:queue":
        user = await ensure_user(uid)
        async with get_session() as s:
            q = await s.execute(
                select(ShortsProject)
                .where(ShortsProject.user_id == user.id)
                .order_by(ShortsProject.id.desc())
                .limit(10)
            )
            projects = q.scalars().all()
        lines = ["📊 <b>Shorts Queue</b>", ""]
        rows = []
        if projects:
            for p in projects:
                lines.append(f"#{p.id} · {p.status.value} · {p.processed_parts}/{p.total_parts}")
                rows.append([InlineKeyboardButton(
                    f"🎬 Project #{p.id}",
                    callback_data=f"sf:project_home:{p.id}",
                )])
        else:
            lines.append("No Shorts projects yet.")
        rows.append(_back("sf:menu"))
        await query.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
        await query.answer()
        return

    if data == "sf:schedule":
        user = await ensure_user(uid)
        async with get_session() as s:
            q = await s.execute(
                select(ShortsProject)
                .where(ShortsProject.user_id == user.id)
                .order_by(ShortsProject.id.desc())
                .limit(1)
            )
            p = q.scalar_one_or_none()
        if not p:
            await query.message.edit_text(
                "📅 No Shorts project yet.",
                reply_markup=InlineKeyboardMarkup([_back("sf:menu")]),
            )
            await query.answer()
            return
        await query.message.edit_text(
            f"📅 <b>Project #{p.id}</b>\n\nChoose Shorts per day:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("2 / day", callback_data=f"sf:limit:{p.id}:2"),
                    InlineKeyboardButton("3 / day", callback_data=f"sf:limit:{p.id}:3"),
                    InlineKeyboardButton("5 / day", callback_data=f"sf:limit:{p.id}:5"),
                ],
                _back(f"sf:project_home:{p.id}"),
            ]),
        )
        await query.answer()
        return

    if len(parts) >= 3 and parts[1] == "project_home":
        await _show_project(client, query, int(parts[2]), uid)
        return

    if len(parts) >= 3 and parts[1] == "project":
        pid = int(parts[2])
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        await query.message.edit_text(
            f"⚙️ <b>Project #{pid} Settings</b>\n\n"
            f"Duration: {p.clip_duration}s\nSpeed: {p.playback_speed}x",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("30s", callback_data=f"sf:duration:{pid}:30"),
                    InlineKeyboardButton("60s", callback_data=f"sf:duration:{pid}:60"),
                    InlineKeyboardButton("90s", callback_data=f"sf:duration:{pid}:90"),
                ],
                [
                    InlineKeyboardButton("120s", callback_data=f"sf:duration:{pid}:120"),
                    InlineKeyboardButton("180s", callback_data=f"sf:duration:{pid}:180"),
                    InlineKeyboardButton("Custom", callback_data=f"sf:custom_duration:{pid}"),
                ],
                [
                    InlineKeyboardButton("0.75x", callback_data=f"sf:speed:{pid}:0.75"),
                    InlineKeyboardButton("1x", callback_data=f"sf:speed:{pid}:1.0"),
                    InlineKeyboardButton("1.25x", callback_data=f"sf:speed:{pid}:1.25"),
                ],
                [
                    InlineKeyboardButton("1.5x", callback_data=f"sf:speed:{pid}:1.5"),
                    InlineKeyboardButton("1.75x", callback_data=f"sf:speed:{pid}:1.75"),
                    InlineKeyboardButton("2x", callback_data=f"sf:speed:{pid}:2.0"),
                ],
                _back(f"sf:project_home:{pid}"),
            ]),
        )
        await query.answer()
        return

    if len(parts) >= 4 and parts[1] in ("duration", "speed"):
        kind, pid, raw = parts[1], int(parts[2]), parts[3]
        value = float(raw)
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        async with get_session() as s:
            p = await s.get(ShortsProject, pid)
            if kind == "duration":
                p.clip_duration = int(value)
            else:
                p.playback_speed = value
        await query.message.edit_text(
            f"⚙️ <b>Project #{pid} Settings</b>\n\n"
            f"Duration: {int(p.clip_duration)}s\nSpeed: {p.playback_speed}x\n\n"
            f"✅ {'Duration' if kind == 'duration' else 'Speed'} updated.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Settings", callback_data=f"sf:project:{pid}")],
                _back(f"sf:project_home:{pid}"),
            ]),
        )
        await query.answer()
        return

    if len(parts) >= 3 and parts[1] == "custom_duration":
        pid = int(parts[2])
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        client.shorts_sessions[uid] = {"custom_duration_pid": pid}
        await query.message.edit_text(
            "⏱️ <b>Custom Duration</b>\n\nSend target duration in seconds (30–1800).",
            reply_markup=InlineKeyboardMarkup([_back(f"sf:project:{pid}", "❌ Cancel")]),
        )
        await query.answer()
        return

    if len(parts) >= 4 and parts[1] == "limit":
        pid, limit = int(parts[2]), int(parts[3])
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        async with get_session() as s:
            p = await s.get(ShortsProject, pid)
            p.daily_limit = max(1, min(5, limit))
        await query.message.edit_text(
            f"📅 <b>Project #{pid}</b>\n\n"
            f"Shorts per day: <b>{p.daily_limit}</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Start / Resume", callback_data=f"sf:start:{pid}")],
                _back(f"sf:project_home:{pid}"),
            ]),
        )
        await query.answer()
        return

    if len(parts) >= 3 and parts[1] == "ack":
        pid = int(parts[2])
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        async with get_session() as s:
            p = await s.get(ShortsProject, pid)
            p.rights_acknowledged = True
        await query.message.edit_text(
            f"🎬 <b>Project #{pid}</b>\n\n"
            "Rights confirmation recorded. Choose how clips should be selected.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎞️ Continuous Series", callback_data=f"sf:mode:{pid}:CONTINUOUS")],
                [InlineKeyboardButton("🔥 Highlight Clips", callback_data=f"sf:mode:{pid}:HIGHLIGHT")],
                _back("sf:menu"),
            ]),
        )
        await query.answer()
        return

    if len(parts) >= 4 and parts[1] == "mode":
        pid, mode = int(parts[2]), parts[3]
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        async with get_session() as s:
            p = await s.get(ShortsProject, pid)
            p.selection_mode = ShortsSelectionMode(mode)
        await _show_project(client, query, pid, uid)
        return

    if len(parts) >= 3 and parts[1] in ("start", "pause", "stop"):
        pid = int(parts[2])
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        if parts[1] == "start":
            if not p.rights_acknowledged:
                await query.answer("Confirm rights/permission first.", show_alert=True)
                return
            await client.shorts_manager.start(pid)
            msg = "▶️ Started / resumed"
        elif parts[1] == "pause":
            await client.shorts_manager.pause(pid)
            msg = "⏸ Paused"
        else:
            await client.shorts_manager.stop(pid)
            msg = "⏹ Stopped"
        await query.answer(msg)
        await _show_project(client, query, pid, uid)
        return

    if len(parts) >= 4 and parts[1] == "parts":
        pid, page = int(parts[2]), int(parts[3])
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        async with get_session() as s:
            q = await s.execute(
                select(ShortClip)
                .where(ShortClip.project_id == pid)
                .order_by(ShortClip.part_number)
                .offset(page * 5)
                .limit(5)
            )
            clips = q.scalars().all()
        lines = [f"📋 <b>Project #{pid} Parts</b>", ""]
        rows = []
        for c in clips:
            lines.append(
                f"Part {c.part_number} · {c.source_start:.1f}s→{c.source_end:.1f}s · {c.status.value}"
            )
            if c.local_path and Path(c.local_path).exists():
                rows.append([InlineKeyboardButton(
                    f"▶️ Preview Part {c.part_number}",
                    callback_data=f"sf:preview:{c.id}:{pid}:{page}",
                )])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"sf:parts:{pid}:{page - 1}"))
        if len(clips) == 5:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"sf:parts:{pid}:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append(_back(f"sf:project_home:{pid}"))
        await query.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
        await query.answer()
        return

    if len(parts) >= 3 and parts[1] == "preview":
        cid = int(parts[2])
        pid = int(parts[3]) if len(parts) > 3 else None
        page = int(parts[4]) if len(parts) > 4 else 0
        async with get_session() as s:
            c = await s.get(ShortClip, cid)
            p = await s.get(ShortsProject, c.project_id) if c else None
        if (
            not c
            or not p
            or p.user_id != (await ensure_user(uid)).id
            or not c.local_path
            or not Path(c.local_path).exists()
        ):
            await query.answer("Preview not available.", show_alert=True)
            return
        uploader = getattr(client, "user_client", None) or client
        await uploader.send_video(
            query.message.chat.id,
            c.local_path,
            caption=f"🎬 Part {c.part_number}\n{c.title or ''}",
            supports_streaming=True,
        )
        await query.answer("Preview sent.")
        if pid is None:
            pid = p.id
        await query.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup([
                _back(f"sf:parts:{pid}:{page}", "◀️ Back to Parts"),
            ])
        )
        return

    if len(parts) >= 3 and parts[1] == "progress":
        pid = int(parts[2])
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        async with get_session() as s:
            q = await s.execute(select(ShortClip).where(ShortClip.project_id == pid))
            clips = q.scalars().all()
        rendered = sum(
            c.status in (
                ShortsClipStatus.RENDERED,
                ShortsClipStatus.UPLOADING,
                ShortsClipStatus.UPLOADED,
                ShortsClipStatus.SCHEDULED,
                ShortsClipStatus.PUBLISHED,
            )
            for c in clips
        )
        uploaded = sum(c.youtube_video_id is not None for c in clips)
        scheduled = sum(
            c.status in (ShortsClipStatus.SCHEDULED, ShortsClipStatus.PUBLISHED)
            for c in clips
        )
        await query.message.edit_text(
            f"🎬 <b>Project #{pid} Progress</b>\n\n"
            f"Source: {p.source_duration / 3600:.2f}h\n"
            f"Mode: {p.selection_mode.value}\n"
            f"Speed: {p.playback_speed}x\n"
            f"Rendered: {rendered}/{len(clips)}\n"
            f"Uploaded: {uploaded}\n"
            f"Scheduled: {scheduled}\n"
            f"Status: {p.status.value}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Parts", callback_data=f"sf:parts:{pid}:0")],
                _back(f"sf:project_home:{pid}"),
            ]),
        )
        await query.answer()
        return

    if len(parts) >= 3 and parts[1] == "project_schedule":
        pid = int(parts[2])
        p = await _project(pid, uid)
        if not p:
            await query.answer("Project not found.", show_alert=True)
            return
        await query.message.edit_text(
            f"📅 <b>Project #{pid} Schedule</b>\n\n"
            f"Current daily limit: <b>{p.daily_limit}</b>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("2 / day", callback_data=f"sf:limit:{pid}:2"),
                    InlineKeyboardButton("3 / day", callback_data=f"sf:limit:{pid}:3"),
                    InlineKeyboardButton("5 / day", callback_data=f"sf:limit:{pid}:5"),
                ],
                _back(f"sf:project_home:{pid}"),
            ]),
        )
        await query.answer()
        return

    if data == "sf:back":
        await query.message.edit_text("🎬 <b>Shorts Factory</b>", reply_markup=menu())
        await query.answer()
        return
