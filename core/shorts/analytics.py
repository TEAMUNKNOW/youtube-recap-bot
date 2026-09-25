from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from bot.database.models import YouTubeAccount
from core.shorts.oauth import YouTubeOAuth
class AnalyticsAdvisor:
    def __init__(self,settings): self.settings=settings; self.oauth=YouTubeOAuth(settings)
    async def preferred_weekdays(self,account:YouTubeAccount)->tuple[list[int],str]:
        try:
            creds=self.oauth.credentials(account)
            if not creds.valid:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
            def query():
                from googleapiclient.discovery import build
                yt=build("youtubeAnalytics","v2",credentials=creds,cache_discovery=False)
                end=datetime.now(timezone.utc).date()-timedelta(days=2)
                start=end-timedelta(days=28)
                return yt.reports().query(
                    ids="channel==MINE",startDate=start.isoformat(),endDate=end.isoformat(),
                    metrics="views,engagedViews",dimensions="day",sort="day",maxResults=100
                ).execute()
            data=await asyncio.to_thread(query)
            rows=data.get("rows",[])
            if not rows:return list(range(7)),"No historical analytics rows; using fallback schedule."
            totals=defaultdict(float)
            counts=defaultdict(int)
            for row in rows:
                day=datetime.fromisoformat(row[0]).date()
                value=float(row[1] or 0)
                totals[day.weekday()]+=value; counts[day.weekday()]+=1
            ranked=sorted(range(7),key=lambda d:(totals[d]/counts[d] if counts[d] else 0),reverse=True)
            return ranked,"Channel historical daily views over the latest available 28-day Analytics window."
        except Exception as exc:
            return list(range(7)),f"Analytics unavailable ({type(exc).__name__}); using configured fallback schedule."
