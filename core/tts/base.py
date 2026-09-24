"""TTS provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel


class VoiceInfo(BaseModel):
    id: str
    name: str
    language: str
    gender: Optional[str] = None


class TTSResult(BaseModel):
    path: str
    duration: float
    provider: str
    voice: str


class BaseTTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,
    ) -> TTSResult:
        ...

    @abstractmethod
    async def list_voices(self, language: Optional[str] = None) -> List[VoiceInfo]:
        ...
