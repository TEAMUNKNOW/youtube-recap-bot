"""ElevenLabs TTS provider."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Optional

from bot.config import Settings
from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo

logger = logging.getLogger(__name__)


class ElevenLabsProvider(BaseTTSProvider):
    name = "elevenlabs"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.elevenlabs_api_key:
            raise TTSError("ELEVENLABS_API_KEY not configured", retryable=False)

    async def synthesize(
        self, text: str, output_path: Path, *,
        voice: Optional[str] = None, language: Optional[str] = None,
    ) -> TTSResult:
        import httpx
        if not text.strip():
            raise TTSError("Empty text for TTS", retryable=False)
        voice_id = voice or self.settings.elevenlabs_voice_id
        if not voice_id:
            raise TTSError("ElevenLabs voice ID not configured", retryable=False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() not in (".mp3", ".wav"):
            output_path = output_path.with_suffix(".mp3")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": self.settings.elevenlabs_api_key or "",
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        body = {
            "text": text,
            "model_id": self.settings.elevenlabs_model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code == 429:
            raise TTSError("ElevenLabs rate limited", retryable=True)
        if resp.status_code >= 500:
            raise TTSError(f"ElevenLabs server error {resp.status_code}", retryable=True)
        if resp.status_code != 200:
            raise TTSError(f"ElevenLabs error {resp.status_code}: {resp.text[:200]}", retryable=False)
        output_path.write_bytes(resp.content)
        duration = await self._probe_duration(output_path)
        return TTSResult(path=str(output_path), duration=duration, provider=self.name, voice=voice_id)

    async def list_voices(self, language: Optional[str] = None) -> List[VoiceInfo]:
        import httpx
        url = "https://api.elevenlabs.io/v1/voices"
        headers = {"xi-api-key": self.settings.elevenlabs_api_key or ""}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return []
        data = resp.json()
        result: list[VoiceInfo] = []
        for v in data.get("voices") or []:
            result.append(VoiceInfo(id=v.get("voice_id", ""), name=v.get("name", ""), language=language or "multi", gender=None))
        return result

    @staticmethod
    async def _probe_duration(path: Path) -> float:
        cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
