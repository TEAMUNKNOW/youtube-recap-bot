"""Security / URL validation tests."""

from __future__ import annotations

import pytest

from bot.exceptions import InputError
from core.downloader import validate_url


def test_youtube_url_ok():
    assert "youtube.com" in validate_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_rejects_non_http():
    with pytest.raises(InputError):
        validate_url("ftp://example.com/a.mp4")


def test_rejects_empty():
    with pytest.raises(InputError):
        validate_url("")


def test_direct_media_allowed():
    url = validate_url("https://cdn.example.com/video.mp4")
    assert url.endswith(".mp4")
