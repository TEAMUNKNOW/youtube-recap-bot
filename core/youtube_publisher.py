"""YouTube Data API v3 OAuth2 publisher with resumable uploads and quota tracking."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bot.config import Settings
from bot.database.models import Quota
from bot.database.session import get_session
from bot.exceptions import QuotaError, YouTubeError
from core.llm_agent import SEOResult

logger = logging.getLogger(__name__)

DEFAULT_COSTS = {"videos.insert": 1600, "thumbnails.set": 50, "channels.list": 1}


class YouTubePublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._youtube = None

    def _get_service(self):
        if self._youtube is not None:
            return self._youtube
        if not self.settings.youtube_client_secrets:
            raise YouTubeError("YOUTUBE_CLIENT_SECRETS not configured", retryable=False)
        secrets = Path(self.settings.youtube_client_secrets)
        if not secrets.exists():
            raise YouTubeError("client_secrets.json not found", retryable=False)
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise YouTubeError("google-api-python-client / google-auth not installed", retryable=False) from exc
        scopes = ["https://www.googleapis.com/auth/youtube.upload"]
        creds = None
        token_file = Path(self.settings.youtube_token_file)
        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes)
                creds = flow.run_local_server(port=0)
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json())
        self._youtube = build("youtube", "v3", credentials=creds)
        return self._youtube

    async def ensure_quota(self, operation: str, units: Optional[int] = None) -> None:
        units = units or DEFAULT_COSTS.get(operation, 100)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with get_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(Quota).where(Quota.date == today))
            row = result.scalar_one_or_none()
            if row is None:
                row = Quota(date=today, youtube_units=0, request_count=0, upload_count=0)
                session.add(row)
                await session.flush()
            budget = self.settings.youtube_quota_daily_budget
            safety = budget * self.settings.youtube_quota_safety_percent / 100
            if row.youtube_units + units > safety:
                raise QuotaError(f"YouTube quota safety threshold reached ({row.youtube_units}/{budget})")

    async def record_quota(self, operation: str, units: Optional[int] = None) -> None:
        units = units or DEFAULT_COSTS.get(operation, 100)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with get_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(Quota).where(Quota.date == today))
            row = result.scalar_one_or_none()
            if row is None:
                row = Quota(date=today, youtube_units=units, request_count=1, upload_count=0)
                session.add(row)
            else:
                row.youtube_units += units
                row.request_count += 1
                if operation == "videos.insert":
                    row.upload_count += 1

    async def upload(
        self, video_path: Path, seo: SEOResult, *,
        thumbnail_path: Optional[Path] = None, privacy: str = "private",
        publish_at: Optional[datetime] = None, category_id: str = "22",
    ) -> str:
        import asyncio
        await self.ensure_quota("videos.insert")

        def _do_upload() -> str:
            youtube = self._get_service()
            from googleapiclient.http import MediaFileUpload
            body: dict[str, Any] = {
                "snippet": {"title": seo.titles[0][:100], "description": seo.description[:5000], "tags": seo.tags[:30], "categoryId": category_id},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            }
            if publish_at is not None:
                body["status"]["privacyStatus"] = "private"
                if publish_at.tzinfo is None:
                    publish_at_utc = publish_at.replace(tzinfo=timezone.utc)
                else:
                    publish_at_utc = publish_at.astimezone(timezone.utc)
                body["status"]["publishAt"] = publish_at_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info("YouTube upload progress: %.1f%%", status.progress() * 100)
            video_id = response["id"]
            logger.info("YouTube upload complete: %s", video_id)
            if thumbnail_path and thumbnail_path.exists():
                try:
                    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
                except Exception as exc:
                    logger.warning("Thumbnail upload failed: %s", exc)
            return video_id

        try:
            video_id = await asyncio.to_thread(_do_upload)
        except QuotaError:
            raise
        except Exception as exc:
            msg = str(exc)
            if "quota" in msg.lower():
                raise QuotaError(msg) from exc
            raise YouTubeError(f"Upload failed: {msg}", retryable=True) from exc
        await self.record_quota("videos.insert")
        if thumbnail_path:
            await self.record_quota("thumbnails.set")
        return video_id
