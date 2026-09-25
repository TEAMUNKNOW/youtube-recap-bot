from __future__ import annotations
from pathlib import Path
from bot.config import Settings
from core.video_engine import FFmpegRunner
class ShortsRenderer:
    def __init__(self,settings:Settings): self.settings=settings; self.ff=FFmpegRunner(settings)
    async def render(self,source:Path,start:float,end:float,out:Path,speed:float,crop_mode:str="smart",fps:str="source")->float:
        out.parent.mkdir(parents=True,exist_ok=True)
        vf="scale=iw*min(1080/iw\\,1920/ih):ih*min(1080/iw\\,1920/ih),crop=1080:1920"
        # The crop expression above can fail for very wide/tall inputs; fallback to fit+blur.
        if crop_mode=="fit": vf="scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
        elif crop_mode=="center": vf="scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        elif crop_mode=="blur": vf="split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20[blur];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[main];[blur][main]overlay=(W-w)/2:(H-h)/2"
        # Smart mode currently uses center crop as a safe deterministic baseline; face tracking can be added without changing the manifest contract.
        else: vf="scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        vf+=f",setpts=PTS/{speed}"
        af=f"atempo={speed}"
        args=["-ss",f"{start:.3f}","-i",str(source),"-t",f"{max(0.1,end-start):.3f}","-vf",vf,"-af",af,"-r",("30" if fps=="30" else "60" if fps=="60" else "30"),"-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)]
        try: await self.ff.run(args,label="short_render")
        except Exception:
            if crop_mode=="smart":
                args[args.index("-vf")+1]="scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setpts=PTS/"+str(speed)
                await self.ff.run(args,label="short_render_fit")
            else: raise
        from core.media_probe import probe
        return float((await probe(out)).duration)
