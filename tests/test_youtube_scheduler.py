"""Peak window scheduler tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from core.scheduler import FixedWindowStrategy


def test_next_slot_in_future():
    strat = FixedWindowStrategy("Asia/Kolkata", "18:00", "21:00", min_delay_minutes=0)
    now = datetime(2026, 1, 1, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    slot = strat.next_slot(after=now)
    assert slot > now
    assert slot.hour == 18
