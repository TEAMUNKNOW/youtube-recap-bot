from __future__ import annotations
from datetime import datetime,timedelta,time,timezone
from zoneinfo import ZoneInfo
from typing import Optional
class ShortsScheduler:
    def __init__(self,tz:str="Asia/Kolkata"): self.tz=ZoneInfo(tz)
    def slots(self,start:Optional[datetime],count:int,limit:int,times:list[str]|None=None)->list[datetime]:
        if count<=0:return []
        now=(start or datetime.now(timezone.utc)).astimezone(self.tz); raw=times or ["09:00","12:00","15:00","18:00","21:00"]
        parsed=[time.fromisoformat(x) for x in raw[:limit]]
        out=[]; day=now.date(); cursor=now
        while len(out)<count:
            for t in parsed:
                candidate=datetime.combine(day,t,tzinfo=self.tz)
                if candidate>cursor+timedelta(minutes=5): out.append(candidate)
                if len(out)>=count: break
            day+=timedelta(days=1); cursor=datetime.combine(day,time(0),tzinfo=self.tz)
        return out
