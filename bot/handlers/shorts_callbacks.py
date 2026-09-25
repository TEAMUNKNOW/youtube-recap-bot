from __future__ import annotations
from pyrogram.types import InlineKeyboardButton,InlineKeyboardMarkup
from sqlalchemy import select,update
from bot.database.models import ShortsProject,ShortsProjectStatus,ShortsSelectionMode,ShortClip,ShortsClipStatus,YouTubeAccount,ShortsSettings
from bot.database.session import get_session
from bot.middleware import ensure_user
from bot.config import get_settings
from core.shorts.oauth import YouTubeOAuth
async def _project(pid,uid):
    user=await ensure_user(uid)
    async with get_session() as s:
        p=await s.get(ShortsProject,pid)
        return p if p and p.user_id==user.id else None
def menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Create Shorts",callback_data="sf:create")],[InlineKeyboardButton("📺 Connect YouTube",callback_data="sf:youtube")],[InlineKeyboardButton("📅 Schedule",callback_data="sf:schedule")],[InlineKeyboardButton("📊 Shorts Queue",callback_data="sf:queue")],[InlineKeyboardButton("⚙️ Shorts Settings",callback_data="sf:settings")],[InlineKeyboardButton("📚 Help",callback_data="sf:help")],[InlineKeyboardButton("◀️ Back",callback_data="sf:back")]])
async def dispatch(client,query):
    data=query.data or ""; uid=query.from_user.id
    if data=="sf:menu":
        await query.message.edit_text("🎬 <b>Shorts Factory</b>\n\nAutonomous long-video → Shorts processing, rendering and scheduling.",reply_markup=menu()); await query.answer(); return
    if data=="sf:create":
        client.shorts_sessions[uid]={"active":True}
        await query.message.edit_text("➕ <b>Create Shorts</b>\n\nSend a YouTube URL or upload a video.\n\nDefault: Continuous Series · 60 sec · 1.5x\n\nYou must have the rights/permission to repurpose the content."); await query.answer(); return
    if data=="sf:youtube":
        user=await ensure_user(uid)
        async with get_session() as s:
            q=await s.execute(select(YouTubeAccount).where(YouTubeAccount.user_id==user.id,YouTubeAccount.revoked_at.is_(None)))
            accounts=q.scalars().all()
        rows=[]
        for a in accounts:
            rows.append([InlineKeyboardButton(f"❌ Disconnect {a.channel_title}",callback_data=f"sf:disconnect:{a.id}")])
        try:
            url=await YouTubeOAuth(get_settings()).authorization_url(user.id)
            rows.insert(0,[InlineKeyboardButton("🔐 Authorize Google / YouTube",url=url)])
            body="📺 <b>Connect YouTube</b>\n\n"+("\n".join(f"✅ {a.channel_title}" for a in accounts) if accounts else "No channel connected.")
            await query.message.edit_text(body,reply_markup=InlineKeyboardMarkup(rows+[[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]]))
        except Exception as exc:
            await query.message.edit_text(f"❌ YouTube OAuth is not configured.\n\n{str(exc)[:500]}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]]))
        await query.answer(); return
    if len(data.split(":"))>=3 and data.split(":")[1]=="disconnect":
        aid=int(data.split(":")[2])
        user=await ensure_user(uid)
        await YouTubeOAuth(get_settings()).disconnect(aid,user.id)
        await query.message.edit_text("📺 YouTube connection revoked.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
    if data=="sf:help":
        await query.message.edit_text("📚 <b>Shorts Factory Help</b>\n\nContinuous Series covers the full source timeline without intentional gaps. Highlight Clips selects meaningful moments and may skip sections. Processing is checkpointed and can resume after restarts.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
    if data=="sf:settings":
        await query.message.edit_text("⚙️ <b>Shorts Settings</b>\n\nDefault duration: 60s\nPlayback: 1.5x\nOutput: 1080×1920 MP4\nTimezone: Asia/Kolkata\nDaily limit: 3 (max 5)",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
    if data=="sf:queue":
        user=await ensure_user(uid)
        async with get_session() as s:
            q=await s.execute(select(ShortsProject).where(ShortsProject.user_id==user.id).order_by(ShortsProject.id.desc()).limit(10)); ps=q.scalars().all()
        text="📊 <b>Shorts Queue</b>\n\n" + ("\n".join(f"#{p.id} · {p.status.value} · {p.processed_parts}/{p.total_parts}" for p in ps) if ps else "No Shorts projects yet.")
        await query.message.edit_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
    if data=="sf:schedule":
        user=await ensure_user(uid)
        async with get_session() as s:
            q=await s.execute(select(ShortsProject).where(ShortsProject.user_id==user.id).order_by(ShortsProject.id.desc()).limit(1)); p=q.scalar_one_or_none()
        if not p:
            await query.message.edit_text("📅 No Shorts project yet.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
        await query.message.edit_text(f"📅 <b>Project #{p.id}</b>\n\nChoose Shorts per day:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("2 / day",callback_data=f"sf:limit:{p.id}:2"),InlineKeyboardButton("3 / day",callback_data=f"sf:limit:{p.id}:3"),InlineKeyboardButton("5 / day",callback_data=f"sf:limit:{p.id}:5")],[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
    if len(data.split(":"))>=4 and data.split(":")[1]=="limit":
        pid=int(data.split(":")[2]); limit=int(data.split(":")[3]); p=await _project(pid,uid)
        if not p: await query.answer("Project not found",show_alert=True); return
        async with get_session() as s:
            p=await s.get(ShortsProject,pid); p.daily_limit=max(1,min(5,limit))
        await query.message.edit_text(f"📅 Project #{pid}: <b>{limit} Shorts/day</b> selected.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Start",callback_data=f"sf:start:{pid}")],[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
    if data=="sf:back":
        await query.message.edit_text("Choose an option from the main menu."); await query.answer(); return
    parts=data.split(":")
    if len(parts)>=3 and parts[1]=="ack":
        pid=int(parts[2]); p=await _project(pid,uid)
        if not p: await query.answer("Project not found",show_alert=True); return
        async with get_session() as s:
            p=await s.get(ShortsProject,pid); p.rights_acknowledged=True
        await query.message.edit_text(f"🎬 <b>Project #{pid}</b>\n\nRights confirmation recorded. Choose how clips should be selected.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎞️ Continuous Series",callback_data=f"sf:mode:{pid}:CONTINUOUS")],[InlineKeyboardButton("🔥 Highlight Clips",callback_data=f"sf:mode:{pid}:HIGHLIGHT")],[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]]))
        await query.answer(); return
    if len(parts)>=4 and parts[1]=="mode":
        pid=int(parts[2]); mode=parts[3]; p=await _project(pid,uid)
        if not p: await query.answer("Project not found",show_alert=True); return
        async with get_session() as s:
            p=await s.get(ShortsProject,pid); p.selection_mode=ShortsSelectionMode(mode)
        await query.message.edit_text(f"🎬 <b>Project #{pid}</b>\n\nMode: {'Continuous Series' if mode=='CONTINUOUS' else 'Highlight Clips'}\nDuration: {p.clip_duration}s\nSpeed: {p.playback_speed}x\n\nReady to start.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Start",callback_data=f"sf:start:{pid}")],[InlineKeyboardButton("⚙️ Settings",callback_data=f"sf:project:{pid}")],[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
    if len(parts)>=3 and parts[1] in ("start","pause","stop"):
        pid=int(parts[2]); p=await _project(pid,uid)
        if not p: await query.answer("Project not found",show_alert=True); return
        if parts[1]=="start" and not p.rights_acknowledged: await query.answer("Confirm rights/permission first.",show_alert=True); return
        if parts[1]=="start":
            await client.shorts_manager.start(pid); msg="▶️ Started"
        elif parts[1]=="pause":
            await client.shorts_manager.pause(pid); msg="⏸ Paused"
        else:
            await client.shorts_manager.stop(pid); msg="⏹ Stopped"
        await query.message.edit_text(f"{msg} — Project #{pid}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 Progress",callback_data=f"sf:progress:{pid}")],[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
    if len(parts)>=3 and parts[1]=="progress":
        pid=int(parts[2]); p=await _project(pid,uid)
        async with get_session() as s:
            q=await s.execute(select(ShortClip).where(ShortClip.project_id==pid)); clips=q.scalars().all()
        rendered=sum(c.status in (ShortsClipStatus.RENDERED,ShortsClipStatus.UPLOADING,ShortsClipStatus.UPLOADED,ShortsClipStatus.SCHEDULED,ShortsClipStatus.PUBLISHED) for c in clips); uploaded=sum(c.youtube_video_id is not None for c in clips); scheduled=sum(c.status in (ShortsClipStatus.SCHEDULED,ShortsClipStatus.PUBLISHED) for c in clips)
        await query.message.edit_text(f"🎬 <b>Project #{pid}</b>\n\nSource: {p.source_duration/3600:.2f}h\nMode: {p.selection_mode.value}\nSpeed: {p.playback_speed}x\nProgress: {rendered}/{len(clips)}\nUploaded: {uploaded}\nScheduled: {scheduled}\nStatus: {p.status.value}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back",callback_data="sf:menu")]])); await query.answer(); return
