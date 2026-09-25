from core.shorts.planner import continuous_manifest,highlight_manifest
from core.shorts.scheduler import ShortsScheduler
from dataclasses import dataclass
from datetime import datetime,timezone
@dataclass
class S:
    start:float
    end:float
    text:str
def test_continuous_covers_source_without_overlap():
    seg=[S(i,i+1,"sentence") for i in range(0,600)]
    clips=continuous_manifest(600,60,seg,5,10000)
    assert clips[0].source_start==0
    assert clips[-1].source_end==600
    assert all(abs(a.source_end-b.source_start)<1e-9 for a,b in zip(clips,clips[1:]))
    assert len(clips)==10
def test_ten_thousand_part_limit():
    seg=[S(i,i+1,"x") for i in range(0,10001)]
    clips=continuous_manifest(10000,1,seg,0,10000)
    assert clips[-1].part_number==10000
def test_highlight_does_not_require_coverage():
    seg=[S(0,10,"boring"),S(100,110,"important discovery"),S(300,310,"surprising answer")]
    clips=highlight_manifest(600,60,seg,[(100,110,1.0),(300,310,0.8)],10000)
    assert len(clips)==2
    assert clips[0].source_start>0
def test_scheduler_is_timezone_aware():
    s=ShortsScheduler("Asia/Kolkata")
    slots=s.slots(datetime(2026,9,26,0,0,tzinfo=timezone.utc),5,5)
    assert len(slots)==5
    assert all(x.tzinfo is not None for x in slots)
