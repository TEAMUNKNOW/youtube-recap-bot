"""Cleanup path safety tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.cleanup import safe_rmtree


def test_refuses_outside_workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Should not delete outside
    safe_rmtree(outside, root)
    assert outside.exists()
