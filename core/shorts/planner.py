from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable
@dataclass(frozen=True)
class ClipPlan:
    part_number:int; source_start:float; source_end:float; target_duration:float; actual_duration:float; selection_reason:str; chapter:str|None=None
def _boundary(segments:list[Any], target:float, lo:float, hi:float)->float:
    candidates=[]
    for s in segments:
        end=float(getattr(s,"end",0)); start=float(getattr(s,"start",0))
        if lo <= end <= hi: candidates.append(end)
        if lo <= start <= hi: candidates.append(start)
    return min(candidates,key=lambda x:abs(x-target)) if candidates else target
def continuous_manifest(duration:float,target:float,segments:Iterable[Any],tolerance:float=5.0,max_parts:int=10000)->list[ClipPlan]:
    seg=list(segments); out=[]; start=0.0; part=1
    while start < duration-0.01 and part<=max_parts:
        nominal=min(duration,start+target); lo=max(start+1.0,nominal-tolerance); hi=min(duration,nominal+tolerance)
        end=_boundary(seg,nominal,lo,hi)
        if end<=start+1: end=nominal
        end=min(duration,end)
        out.append(ClipPlan(part,start,end,target,end-start,"continuous source coverage"))
        start=end; part+=1
    if start < duration-0.01: raise ValueError("Source requires more than configured SHORTS_MAX_PARTS")
    return out
def highlight_manifest(duration:float,target:float,segments:Iterable[Any],scores:list[tuple[float,float,float]],max_parts:int=10000)->list[ClipPlan]:
    seg=list(segments); chosen=[]
    for start,end,score in sorted(scores,key=lambda x:x[2],reverse=True):
        if any(max(start,a)<min(end,b) for a,b,_ in chosen): continue
        lo=max(0,start-target/2); hi=min(duration,end+target/2)
        cs=max(0,_boundary(seg,start,lo,start) if seg else start)
        ce=min(duration,_boundary(seg,end,end,hi) if seg else end)
        if ce-cs<target*0.5: ce=min(duration,cs+target)
        chosen.append((cs,ce,score))
        if len(chosen)>=max_parts: break
    chosen.sort()
    return [ClipPlan(i,a,b,target,b-a,"highlight score %.3f"%score) for i,(a,b,score) in enumerate(chosen,1)]
