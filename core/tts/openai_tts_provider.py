"""OpenAI TTS provider."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from bot.config import Settings
from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo
from core.tts.audio_merge import synthesize_chunked

logger = logging.getLogger(__name__)


class OpenAITTSProvider(BaseTTSProvider):
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.openai_api_key:
            raise TTSError("OPENAI_API_KEY not configured", retryable=False)

    async def synthesize(
        self, text: str, output_path: Path, *,
        voice: Optional[str] = None, language: Optional[str] = None,
    ) -> TTSResult:
        import httpx
        if not text.strip():
            raise TTSError("Empty text for TTS", retryable=False)
        voice_id = voice or self.settings.openai_tts_voice
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() not in (".mp3", ".wav"):
            output_path = output_path.with_suffix(".mp3")

        async def synth_one(chunk: str, path: Path) -> None:
            url = "https://api.openai.com/v1/audio/speech"
            headers = {"Authorization": f"Bearer {self.settings.openai_api_key}", "Content-Type": "application/json"}
            body = {"model": self.settings.openai_tts_model, "input": chunk, "voice": voice_id, "response_format": "mp3"}
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, headers=headers, json=body)
            if resp.status_code == 429:
                raise TTSError("OpenAI TTS rate limited", retryable=True)
            if resp.status_code >= 500:
                raise TTSError(f"OpenAI TTS server error {resp.status_code}", retryable=True)
            if resp.status_code != 200:
                raise TTSError(f"OpenAI TTS error {resp.status_code}: {resp.text[:200]}", retryable=False)
            path.write_bytes(resp.content)

        try:
            duration = await synthesize_chunked(
                text, output_path, max_chars=3500, concurrency=2,
                synthesize_one=synth_one,
            )
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"OpenAI TTS failed: {exc}", retryable=True) from exc
        if duration <= 0:
            raise TTSError("OpenAI TTS produced invalid audio", retryable=True)
        return TTSResult(path=str(output_path), duration=duration, provider=self.name, voice=voice_id)

    async def list_voices(self, language: Optional[str] = None) -> List[VoiceInfo]:
        voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        return [VoiceInfo(id=v, name=v.title(), language=language or "en") for v in voices]


class EdgeStyleProbe:
    @staticmethod
    async def probe(path: Path) -> float:
        import asyncio
        cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
