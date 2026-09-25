from __future__ import annotations
import logging
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse
from core.shorts.oauth import YouTubeOAuth
from bot.config import Settings
from bot.database.models import User
from bot.database.session import get_session
logger=logging.getLogger(__name__)
def create_oauth_app(settings:Settings,bot):
    app=FastAPI(title="Shorts Factory OAuth",docs_url=None,redoc_url=None)
    oauth=YouTubeOAuth(settings)
    @app.get("/youtube/oauth/callback",response_class=HTMLResponse)
    async def callback(request:Request):
        state=request.query_params.get("state",""); code=request.query_params.get("code")
        if request.query_params.get("error"): return HTMLResponse("<h2>Authorization cancelled.</h2><p>You can close this window.</p>",status_code=400)
        if not state or not code:return HTMLResponse("<h2>Missing OAuth response.</h2>",status_code=400)
        try:
            account=await oauth.handle_callback(state,code)
            async with get_session() as s:
                user=await s.get(User,account.user_id)
            if user: await bot.send_message(user.telegram_id,f"📺 <b>YouTube connected</b>\n\nChannel: <b>{account.channel_title}</b>\nChannel ID: <code>{account.channel_id}</code>\n\nOAuth credentials are stored encrypted.")
            return HTMLResponse("<h2>YouTube connected successfully.</h2><p>You can close this window and return to Telegram.</p>")
        except Exception as exc:
            logger.exception("OAuth callback failed")
            return HTMLResponse(f"<h2>Connection failed.</h2><p>{str(exc)[:500]}</p>",status_code=400)
    return app
