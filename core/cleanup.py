"""Workspace and orphan cleanup."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from bot.config import Settings

logger = logging.getLogger(__name__)


def task_workspace(settings: Settings, task_id: int) -> Path:
    path = settings.workspace_root / str(task_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_rmtree(path: Path, workspace_root: Path) -> None:
    """Delete only paths under workspace_root."""
    try:
        resolved = path.resolve()
        root = workspace_root.resolve()
        if not str(resolved).startswith(str(root)):
            logger.error("Refusing to delete outside workspace: %s", path)
            return
        if resolved.exists():
            shutil.rmtree(resolved, ignore_errors=True)
            logger.info("Cleaned workspace: %s", path)
    except Exception as exc:
        logger.warning("Cleanup failed for %s: %s", path, exc)


def cleanup_task_workspace(
    settings: Settings,
    task_id: int,
    *,
    preserve_debug: bool = False,
    debug_paths: Optional[list[Path]] = None,
) -> None:
    ws = settings.workspace_root / str(task_id)
    if not ws.exists():
        return
    if preserve_debug and debug_paths:
        debug_dir = settings.workspace_root / f"{task_id}_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        for p in debug_paths:
            if p.exists():
                try:
                    shutil.copy2(p, debug_dir / p.name)
                except OSError:
                    pass
    safe_rmtree(ws, settings.workspace_root)


def cleanup_orphans(settings: Settings, max_age_hours: int = 24) -> int:
    """Remove workspace dirs older than max_age_hours."""
    root = settings.workspace_root
    if not root.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
            if mtime < cutoff:
                safe_rmtree(child, root)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Orphan cleanup removed %s directories", removed)
    return removed


def check_disk_space(path: Path, min_gb: float) -> bool:
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024**3)
        return free_gb >= min_gb
    except OSError:
        return False
