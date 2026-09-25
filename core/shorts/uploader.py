from __future__ import annotations
import asyncio,logging
from datetime import datetime,timezone
from pathlib import Path
from bot.config import Settings
from bot.database.models import YouTubeAccount,ShortClip,ShortsClipStatus
from core.shorts.oauth import YouTubeOAuth
logger=logging.getLogger(__name__)
class ShortsUploader:
    def __init__(self,settings:Settings): self.settings=settings; self.oauth=YouTubeOAuth(settings)
    async def upload(self,account:YouTubeAccount,clip:ShortClip)->str:
        if clip.youtube_video_id: return clip.youtube_video_id
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        creds=self.oauth.credentials(account)
        if not creds.valid:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        yt=build("youtube","v3",credentials=creds,cache_discovery=False)
        body={"snippet":{"title":clip.title or f"Part {clip.part_number}","description":clip.description or "","tags":clip.tags or [],"categoryId":"22"},"status":{"privacyStatus":"private"}}
        if clip.scheduled_at:
            dt=clip.scheduled_at.astimezone(timezone.utc)
            body["status"]["publishAt"]=dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        media=MediaFileUpload(str(clip.local_path),mimetype="video/mp4",resumable=True,chunksize=8*1024*1024)
        def work():
            req=yt.videos().insert(part="snippet,status",body=body,media_body=media); resp=None
            while resp is None:
                _,resp=req.next_chunk()
            return resp["id"]
        return await asyncio.to_thread(work)
