from __future__ import annotations
from core.llm_agent import LLMAgent
class ShortsMetadata:
    def __init__(self,llm:LLMAgent): self.llm=llm
    async def generate(self,transcript:str,base_title:str,part:int,chapter:str|None=None)->dict:
        prompt=f"""Create YouTube Shorts metadata for PART {part}. Base/source title: {base_title}. Chapter: {chapter or 'N/A'}. Transcript/content: {transcript[:12000]}
Return JSON: title (under 90 chars, accurate, no part number), description (under 5000 chars), tags (max 15), hashtags (max 8). Do not invent facts or use keyword stuffing."""
        raw=await self.llm._complete("You create accurate YouTube metadata from supplied content. Output JSON only.",prompt)
        data=self.llm._extract_json(raw)
        title=str(data.get("title") or base_title).strip()
        return {"title":f"{title[:84].rstrip()} | Part {part}","description":str(data.get("description") or f"{title}. Part {part} of the series."),"tags":[str(x) for x in data.get("tags",[])][:15],"hashtags":[str(x) for x in data.get("hashtags",[])][:8]}
