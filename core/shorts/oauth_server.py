"""Minimal FastAPI app for YouTube OAuth callback (Railway-compatible)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from bot.config import Settings
from bot.database.models import User
from bot.database.session import get_session
from core.shorts.oauth import YouTubeOAuth

logger = logging.getLogger(__name__)


def create_oauth_app(settings: Settings, bot) -> FastAPI:
    app = FastAPI(title="YouTube OAuth", docs_url=None, redoc_url=None)
    oauth = YouTubeOAuth(settings)

    @app.get("/")
    async def root():
        return JSONResponse(
            {
                "ok": True,
                "service": "youtube-recap-bot",
                "oauth_callback": "/youtube/oauth/callback",
                "hint": "Use /youtube in Telegram after setting YOUTUBE_* env vars.",
            }
        )

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    async def _handle_callback(request: Request) -> HTMLResponse:
        state = request.query_params.get("state", "")
        code = request.query_params.get("code")
        if request.query_params.get("error"):
            return HTMLResponse(
                "<h2>Authorization cancelled.</h2><p>You can close this window.</p>",
                status_code=400,
            )
        if not state or not code:
            return HTMLResponse("<h2>Missing OAuth response.</h2>", status_code=400)
        try:
            account = await oauth.handle_callback(state, code)
            async with get_session() as s:
                user = await s.get(User, account.user_id)
            if user:
                try:
                    await bot.send_message(
                        user.telegram_id,
                        f"📺 <b>YouTube connected</b>\n\n"
                        f"Channel: <b>{account.channel_title}</b>\n"
                        f"Channel ID: <code>{account.channel_id}</code>\n\n"
                        "OAuth credentials are stored encrypted.",
                    )
                except Exception:
                    logger.exception("Failed to notify user of OAuth success")
            return HTMLResponse(
                "<h2>YouTube connected successfully.</h2>"
                "<p>You can close this window and return to Telegram.</p>"
            )
        except Exception as exc:
            logger.exception("OAuth callback failed")
            return HTMLResponse(
                f"<h2>Connection failed.</h2><p>{str(exc)[:500]}</p>",
                status_code=400,
            )

    @app.get("/youtube/oauth/callback", response_class=HTMLResponse)
    async def callback_primary(request: Request):
        return await _handle_callback(request)

    @app.get("/oauth/youtube/callback", response_class=HTMLResponse)
    async def callback_alias(request: Request):
        return await _handle_callback(request)

    return app
