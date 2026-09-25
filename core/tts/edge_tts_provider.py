"""Microsoft Edge neural TTS provider."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Optional

from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo

logger = logging.getLogger(__name__)


class EdgeTTSProvider(BaseTTSProvider):
    name = "edge"

    def __init__(self, default_voices: Optional[dict[str, str]] = None) -> None:
        self.default_voices = default_voices or {
            "hi": "hi-IN-MadhurNeural",
            "en": "en-US-ChristopherNeural",
            "bn": "bn-IN-TanishaaNeural",
            "es": "es-ES-AlvaroNeural",
        }

    async def synthesize(
        self, text: str, output_path: Path, *,
        voice: Optional[str] = None, language: Optional[str] = None,
    ) -> TTSResult:
        if not text.strip():
            raise TTSError("Empty text for TTS", retryable=False)
        voice_id = voice or self._pick_voice(language)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() not in (".mp3", ".wav"):
            output_path = output_path.with_suffix(".mp3")
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSError("edge-tts package not installed", retryable=False) from exc
        try:
            communicate = edge_tts.Communicate(text, voice_id)
            await communicate.save(str(output_path))
        except Exception as exc:
            raise TTSError(f"Edge TTS failed: {exc}", retryable=True) from exc
        if not output_path.exists() or output_path.stat().st_size < 100:
            raise TTSError("Edge TTS produced empty file", retryable=True)
        duration = await self._probe_duration(output_path)
        return TTSResult(path=str(output_path), duration=duration, provider=self.name, voice=voice_id)

    async def list_voices(self, language: Optional[str] = None) -> List[VoiceInfo]:
        try:
            import edge_tts
        except ImportError:
            return []
        voices = await edge_tts.list_voices()
        result: list[VoiceInfo] = []
        for v in voices:
            locale = v.get("Locale", "")
            if language and not locale.lower().startswith(language[:2].lower()):
                continue
            result.append(VoiceInfo(id=v.get("ShortName", ""), name=v.get("FriendlyName", v.get("ShortName", "")), language=locale, gender=v.get("Gender")))
        return result

    def _pick_voice(self, language: Optional[str]) -> str:
        if language:
            key = language[:2].lower()
            if key in self.default_voices:
                return self.default_voices[key]
        return self.default_voices.get("en", "en-US-ChristopherNeural")

    @staticmethod
    async def _probe_duration(path: Path) -> float:
        cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
