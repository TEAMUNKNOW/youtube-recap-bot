from __future__ import annotations
import re
from pathlib import Path
from pyrogram import Client,filters,StopPropagation
from pyrogram.types import Message,InlineKeyboardButton,InlineKeyboardMarkup
from sqlalchemy import select
from bot.database.models import User,ShortsProject,ShortsSettings
from bot.database.session import get_session
from bot.filters import authorized_users_filter
from bot.middleware import ensure_user
from bot.config import get_settings
from core.shorts.oauth import YouTubeOAuth
URL_RE=re.compile(r"https?://[^\s<>\"']+",re.I)
def register_shorts_handlers(app:Client):
    if not hasattr(app,"shorts_sessions"): app.shorts_sessions={}
    auth=authorized_users_filter()
    @app.on_callback_query(filters.regex(r"^sf:"),group=-10)
    async def shorts_callback(client,query):
        from bot.handlers.shorts_callbacks import dispatch
        await dispatch(client,query)
        raise StopPropagation
    @app.on_message((filters.text|filters.video|filters.document)&auth,group=-10)
    async def shorts_input(client,message:Message):
        uid=message.from_user.id
        session=getattr(client,"shorts_sessions",{}).get(uid)
        if not session:return
        if message.text and not (m:=URL_RE.search(message.text.strip())): return
        user=await ensure_user(uid); settings=get_settings()
        if message.text:
            url=m.group(0)
            pid=await client.shorts_manager.create_project((await ensure_user(uid)).id,None,url,message.chat.id)
        else:
            media=message.video or message.document
            if not media:return
            if (getattr(media,"file_size",0) or 0)>settings.max_input_bytes(): await message.reply_text("File exceeds the configured input limit."); raise StopPropagation
            pid=await client.shorts_manager.create_project(user.id,None,None,message.chat.id)
            ws=Path(settings.workspace_root)/"shorts"/str(pid); ws.mkdir(parents=True,exist_ok=True)
            path=await message.download(file_name=str(ws/"source_input"))
            async with get_session() as s:
                p=await s.get(ShortsProject,pid); p.source_file=str(path)
        getattr(client,"shorts_sessions",{}).pop(uid,None)
        await message.reply_text(f"🎬 <b>Shorts Project #{pid}</b> created.\n\nChoose the selection mode and then start processing.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎞️ Continuous Series",callback_data=f"sf:mode:{pid}:CONTINUOUS")],[InlineKeyboardButton("🔥 Highlight Clips",callback_data=f"sf:mode:{pid}:HIGHLIGHT")],[InlineKeyboardButton("◀️ Back",callback_data="sf:back")]]))
        raise StopPropagation
