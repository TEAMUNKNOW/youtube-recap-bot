"""Peak-time scheduling strategies for YouTube publishAt."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from bot.config import Settings

logger = logging.getLogger(__name__)


class ScheduleStrategy(ABC):
    @abstractmethod
    def next_slot(self, after: Optional[datetime] = None) -> datetime:
        ...


class FixedWindowStrategy(ScheduleStrategy):
    """Schedule into a fixed daily peak window in the configured timezone."""

    def __init__(
        self,
        timezone: str,
        peak_start: str,
        peak_end: str,
        min_delay_minutes: int = 30,
    ) -> None:
        self.tz = ZoneInfo(timezone)
        self.peak_start = self._parse_hhmm(peak_start)
        self.peak_end = self._parse_hhmm(peak_end)
        self.min_delay = timedelta(minutes=min_delay_minutes)

    @staticmethod
    def _parse_hhmm(value: str) -> tuple[int, int]:
        parts = value.strip().split(":")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

    def next_slot(self, after: Optional[datetime] = None) -> datetime:
        now = after or datetime.now(tz=self.tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=self.tz)
        else:
            now = now.astimezone(self.tz)

        earliest = now + self.min_delay
        sh, sm = self.peak_start
        eh, em = self.peak_end

        candidate = earliest.replace(hour=sh, minute=sm, second=0, microsecond=0)
        if candidate < earliest:
            candidate += timedelta(days=1)

        end_same_day = candidate.replace(hour=eh, minute=em, second=0, microsecond=0)
        if candidate > end_same_day:
            candidate = candidate + timedelta(days=1)
            candidate = candidate.replace(hour=sh, minute=sm, second=0, microsecond=0)

        return candidate


class ManualStrategy(ScheduleStrategy):
    def __init__(self, when: datetime) -> None:
        self.when = when

    def next_slot(self, after: Optional[datetime] = None) -> datetime:
        return self.when


class ChannelAnalyticsStrategy(FixedWindowStrategy):
    """Placeholder that currently behaves like FixedWindow."""

    pass


def build_scheduler(settings: Settings) -> ScheduleStrategy:
    return FixedWindowStrategy(
        timezone=settings.target_timezone,
        peak_start=settings.peak_start,
        peak_end=settings.peak_end,
    )
