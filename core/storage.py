"""Storage abstraction — LocalStorage now, S3/R2/MinIO later without pipeline changes."""

from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from bot.exceptions import StorageError

logger = logging.getLogger(__name__)


class BaseStorage(ABC):
    @abstractmethod
    async def put(self, local_path: Path, key: str) -> str:
        """Upload/store a file; return the storage key or path."""

    @abstractmethod
    async def get(self, key: str, dest: Path) -> Path:
        """Retrieve a file to dest; return dest path."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    async def url(self, key: str) -> Optional[str]:
        """Public or signed URL if applicable."""


class LocalStorage(BaseStorage):
    """Filesystem-backed storage under a root directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        safe = Path(key).name if "/" not in key and "\\" not in key else Path(key)
        target = (self.root / safe).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise StorageError(f"Path traversal blocked: {key}")
        return target

    async def put(self, local_path: Path, key: str) -> str:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(local_path, dest)
        except OSError as exc:
            raise StorageError(f"Failed to store {key}: {exc}") from exc
        return str(dest)

    async def get(self, key: str, dest: Path) -> Path:
        src = self._resolve(key)
        if not src.exists():
            raise StorageError(f"Key not found: {key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            path.unlink()

    async def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    async def url(self, key: str) -> Optional[str]:
        path = self._resolve(key)
        if path.exists():
            return path.as_uri()
        return None


def get_storage(root: Path) -> BaseStorage:
    return LocalStorage(root)
