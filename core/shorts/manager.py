from __future__ import annotations
import asyncio,logging,json
from pathlib import Path
from datetime import datetime,timezone
from sqlalchemy import select,update
from bot.config import Settings
from bot.database.models import ShortsProject,ShortClip,ShortsProjectStatus,ShortsClipStatus,ShortsSelectionMode,YouTubeAccount,ShortsSettings
from bot.database.session import get_session
from core.cleanup import task_workspace
from core.downloader import Downloader
from core.media_probe import probe
from core.transcriber import Transcriber
from core.llm_agent import LLMAgent
from core.shorts.planner import continuous_manifest,highlight_manifest
from core.shorts.renderer import ShortsRenderer
from core.shorts.metadata import ShortsMetadata
from core.shorts.scheduler import ShortsScheduler
from core.shorts.uploader import ShortsUploader
logger=logging.getLogger(__name__)
class ShortsManager:
    def __init__(self,settings:Settings): self.settings=settings; self.render=ShortsRenderer(settings); self.transcriber=Transcriber(settings); self.llm=LLMAgent(settings); self.uploader=ShortsUploader(settings); self.tasks:set[asyncio.Task]=set(); self.sem=asyncio.Semaphore(settings.shorts_render_concurrency)
    async def create_project(self,user_id:int,source_file:str|None,source_url:str|None,chat_id:int,selection_mode:str="CONTINUOUS",duration:int|None=None,speed:float|None=None)->int:
        async with get_session() as s:
            p=ShortsProject(user_id=user_id,source_file=source_file,source_url=source_url,chat_id=chat_id,selection_mode=ShortsSelectionMode(selection_mode),clip_duration=duration or self.settings.shorts_default_duration,playback_speed=speed or self.settings.shorts_default_speed,workspace_path=str(self.settings.workspace_root/"shorts"),daily_limit=self.settings.shorts_default_daily_limit,timezone=self.settings.shorts_default_timezone,retention_days=self.settings.shorts_retention_days)
            s.add(p); await s.flush(); pid=p.id; p.workspace_path=str(self.settings.workspace_root/"shorts"/str(pid))
        return pid
    async def start(self,pid:int)->None:
        t=asyncio.create_task(self.run(pid),name=f"shorts-project-{pid}")
        self.tasks.add(t); t.add_done_callback(self.tasks.discard)
    async def pause(self,pid:int)->None:
        async with get_session() as s:
            p=await s.get(ShortsProject,pid)
            if p and p.status not in (ShortsProjectStatus.COMPLETED,ShortsProjectStatus.STOPPED): p.status=ShortsProjectStatus.PAUSED
    async def stop(self,pid:int)->None:
        async with get_session() as s:
            p=await s.get(ShortsProject,pid)
            if p: p.status=ShortsProjectStatus.STOPPED
    async def recover(self)->int:
        recovered=0
        async with get_session() as s:
            q=await s.execute(select(ShortsProject).where(ShortsProject.status.in_([ShortsProjectStatus.ANALYZING,ShortsProjectStatus.MANIFEST_READY,ShortsProjectStatus.RUNNING])))
            ids=[p.id for p in q.scalars().all()]
        for pid in ids:
            await self.start(pid); recovered+=1
        return recovered
    async def run(self,pid:int)->None:
        async with get_session() as s:
            p=await s.get(ShortsProject,pid)
            if not p:return
            existing=(await s.execute(select(ShortClip.id).where(ShortClip.project_id==pid).limit(1))).scalar_one_or_none()
            if existing is not None:
                p.status=ShortsProjectStatus.MANIFEST_READY
                source=Path(p.source_file) if p.source_file else None
                ws=Path(p.workspace_path)
                resume=True
            else:
                resume=False
                p.status=ShortsProjectStatus.ANALYZING; ws=Path(p.workspace_path); ws.mkdir(parents=True,exist_ok=True); source=Path(p.source_file) if p.source_file else None; url=p.source_url
        if resume:
            if source is None or not source.exists():
                raise RuntimeError("Shorts manifest exists but source media is missing")
            await self.process(pid)
            return
        if source is None:
            source=await Downloader(self.settings).download(url,ws,max_filesize_bytes=int(self.settings.shorts_max_input_size_gb*1024**3))
        info=await probe(source)
        async with get_session() as s:
            p=await s.get(ShortsProject,pid); p.source_file=str(source); p.source_duration=info.duration; p.status=ShortsProjectStatus.ANALYZING
        # Transcribe in 10-minute file chunks so LLM/API calls remain bounded.
        audio=Path(str(ws/"audio.mp3"))
        from core.video_engine import VideoEngine
        await VideoEngine(self.settings).extract_audio(source,audio)
        segments=[]; chunk=600.0; start=0.0; chunk_no=0
        while start<info.duration-0.1:
            length=min(chunk,info.duration-start); cp=ws/f"audio_{chunk_no:05d}.mp3"
            await VideoEngine(self.settings).runner.run(["-ss",f"{start:.3f}","-i",str(audio),"-t",f"{length:.3f}","-c:a","copy",str(cp)],label="shorts_audio_chunk")
            tr=await self.transcriber.transcribe(cp)
            with (ws/"transcript.jsonl").open("a",encoding="utf-8") as tf:
                for seg in tr.segments:
                    seg.start+=start; seg.end+=start; segments.append(seg)
                    tf.write(json.dumps({"start":seg.start,"end":seg.end,"text":seg.text},ensure_ascii=False)+"\\n")
            cp.unlink(missing_ok=True); start+=length; chunk_no+=1
        mode=p.selection_mode.value; plans=continuous_manifest(info.duration,p.clip_duration,segments,self.settings.shorts_boundary_tolerance_seconds,self.settings.shorts_max_parts) if mode=="CONTINUOUS" else await self._highlight(info.duration,p.clip_duration,segments)
        async with get_session() as s:
            s.add_all([ShortClip(project_id=pid,part_number=x.part_number,source_start=x.source_start,source_end=x.source_end,target_duration=x.target_duration,actual_duration=x.actual_duration,selection_mode=ShortsSelectionMode(mode),selection_reason=x.selection_reason,overlap_allowed=False,chapter=x.chapter) for x in plans]); p=await s.get(ShortsProject,pid); p.total_parts=len(plans); p.status=ShortsProjectStatus.MANIFEST_READY
        await self.process(pid)
    async def _highlight(self,duration,target,segments):
        scores=[]; window=[]
        for seg in segments:
            text=seg.text.lower(); score=sum(k in text for k in ("important","secret","why","how","because","surprising","finally","never","discovered","revealed"))
            if score: scores.append((max(0,seg.start-target/2),min(duration,seg.end+target/2),float(score)))
        return highlight_manifest(duration,target,segments,scores,self.settings.shorts_max_parts)
    async def process(self,pid:int)->None:
        while True:
            async with get_session() as s:
                p=await s.get(ShortsProject,pid)
                if not p or p.status in (ShortsProjectStatus.PAUSED,ShortsProjectStatus.STOPPED,ShortsProjectStatus.COMPLETED): return
                p.status=ShortsProjectStatus.RUNNING
                q=await s.execute(select(ShortClip).where(ShortClip.project_id==pid,ShortClip.status.in_([ShortsClipStatus.PLANNED,ShortsClipStatus.FAILED,ShortsClipStatus.RENDERED])).order_by(ShortClip.part_number).limit(1))
                clip=q.scalar_one_or_none()
            if not clip:
                async with get_session() as s: p=await s.get(ShortsProject,pid); p.status=ShortsProjectStatus.COMPLETED
                return
            try:
                await self._process_clip(pid,clip.id)
            except Exception as exc:
                logger.exception("[SHORTS] project=%s part=%s failed",pid,clip.part_number)
                async with get_session() as s:
                    c=await s.get(ShortClip,clip.id); c.status=ShortsClipStatus.FAILED; c.error=str(exc)[:2000]; c.attempt+=1
                    if c.attempt>=self.settings.max_retries: c.status=ShortsClipStatus.FAILED
    async def _process_clip(self,pid:int,cid:int)->None:
        async with get_session() as s:
            p=await s.get(ShortsProject,pid); c=await s.get(ShortClip,cid)
            if not p or not c:return
            source=Path(p.source_file); ws=Path(p.workspace_path); out=ws/f"part_{c.part_number:05d}.mp4"
            already=bool(c.status==ShortsClipStatus.RENDERED and c.local_path and Path(c.local_path).exists())
            if not already: c.status=ShortsClipStatus.RENDERING
        if already:
            dur=float(c.actual_duration or 0)
        else:
            async with self.sem:
                dur=await self.render.render(source,c.source_start,c.source_end,out,p.playback_speed,"smart")
            async with get_session() as s:
                c=await s.get(ShortClip,cid); c.local_path=str(out); c.actual_duration=dur; c.status=ShortsClipStatus.RENDERED
                p=await s.get(ShortsProject,pid)
                if p: p.processed_parts+=1
        async with get_session() as s:
            c=await s.get(ShortClip,cid); p=await s.get(ShortsProject,pid)
            if c.title is None:
                lines=[]
                tp=ws/"transcript.jsonl"
                if tp.exists():
                    with tp.open(encoding="utf-8") as tf:
                        for line in tf:
                            row=json.loads(line)
                            if float(row["end"])>=c.source_start and float(row["start"])<=c.source_end:
                                lines.append(row["text"])
                try:
                    meta=await ShortsMetadata(self.llm).generate(" ".join(lines),"Source video",c.part_number,c.chapter)
                except Exception as exc:
                    logger.warning("[SHORTS] metadata fallback project=%s part=%s: %s",pid,c.part_number,exc)
                    meta={"title":f"Source Video | Part {c.part_number}","description":f"Part {c.part_number} of the source video.","tags":[],"hashtags":["#Shorts"]}
                c.title=meta["title"]; c.description=meta["description"]; c.tags=meta["tags"]; c.hashtags=meta["hashtags"]
        async with get_session() as s:
            c=await s.get(ShortClip,cid); q=await s.execute(select(YouTubeAccount).where(YouTubeAccount.user_id==(await s.get(ShortsProject,pid)).user_id,YouTubeAccount.revoked_at.is_(None))); account=q.scalars().first()
            if account and c.local_path:
                q2=await s.execute(select(ShortClip.scheduled_at).where(ShortClip.project_id==pid,ShortClip.scheduled_at.is_not(None)).order_by(ShortClip.scheduled_at.desc()).limit(1))
                last=q2.scalar_one_or_none()
                sched=ShortsScheduler(p.timezone).slots(last,1,p.daily_limit)[0]
                c.scheduled_at=sched; c.status=ShortsClipStatus.UPLOADING
            else:
                p.status=ShortsProjectStatus.PAUSED
                p.error="Connect YouTube to continue upload/scheduling"
                return
        try:
            async with get_session() as s: c=await s.get(ShortClip,cid); p=await s.get(ShortsProject,pid); q=await s.execute(select(YouTubeAccount).where(YouTubeAccount.user_id==p.user_id,YouTubeAccount.revoked_at.is_(None))); account=q.scalars().first()
            vid=await self.uploader.upload(account,c)
            async with get_session() as s: c=await s.get(ShortClip,cid); c.youtube_video_id=vid; c.status=ShortsClipStatus.SCHEDULED
        except Exception:
            async with get_session() as s: c=await s.get(ShortClip,cid); c.status=ShortsClipStatus.RENDERED; raise
