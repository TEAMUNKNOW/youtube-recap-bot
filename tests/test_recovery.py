"""Stale task recovery logic (model-level)."""

from __future__ import annotations

from bot.database.models import TaskStatus


def test_status_enum_complete():
    required = {
        "QUEUED",
        "RECOVERING",
        "DOWNLOADING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
    for name in required:
        assert hasattr(TaskStatus, name)
