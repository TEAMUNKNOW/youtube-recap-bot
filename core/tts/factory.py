"""TTS provider factory."""

from __future__ import annotations

from typing import Optional

from bot.config import Settings
from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider
from core.tts.edge_tts_provider import EdgeTTSProvider
from core.tts.elevenlabs_provider import ElevenLabsProvider
from core.tts.openai_tts_provider import OpenAITTSProvider


def create_tts_provider(settings: Settings, provider: Optional[str] = None) -> BaseTTSProvider:
    name = (provider or settings.tts_provider or "edge").lower()

    if name in ("edge", "edge_tts"):
        return EdgeTTSProvider(
            default_voices={
                "hi": settings.hindi_voice,
                "en": settings.english_voice,
                "bn": settings.bengali_voice,
                "es": settings.spanish_voice,
            }
        )
    if name in ("elevenlabs", "eleven"):
        return ElevenLabsProvider(settings)
    if name in ("openai", "openai_tts"):
        return OpenAITTSProvider(settings)

    raise TTSError(f"Unknown TTS provider: {name}", retryable=False)
