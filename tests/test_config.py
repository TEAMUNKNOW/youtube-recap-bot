"""Config tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_settings_parses_ids(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "abc")
    monkeypatch.setenv("OWNER_IDS", "111,222")
    monkeypatch.setenv("ADMIN_IDS", "333")
    from bot.config import Settings

    s = Settings()
    assert s.is_owner(111)
    assert s.is_authorized(333)
    assert not s.is_authorized(999)
    assert s.max_input_bytes() == int(10 * 1024**3)


def test_settings_requires_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x")
    from bot.config import Settings
    from pydantic import ValidationError

    with pytest.raises((ValidationError, ValueError)):
        Settings()
