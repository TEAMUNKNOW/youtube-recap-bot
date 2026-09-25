"""TTS provider factory."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from bot.config import Settings
from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult
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
            },
            proxy=settings.proxy_url,
        )
    if name in ("elevenlabs", "eleven"):
        return ElevenLabsProvider(settings)
    if name in ("openai", "openai_tts"):
        return OpenAITTSProvider(settings)

    raise TTSError(f"Unknown TTS provider: {name}", retryable=False)


class TTSFactory:
    """Pipeline-facing TTS helper."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._provider = create_tts_provider(settings)

    async def synthesize(
        self,
        text: str,
        *,
        language: str = "en",
        out_dir: Optional[Path] = None,
        voice: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> TTSResult:
        out_dir = Path(out_dir) if out_dir else Path(self.settings.workspace_root)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / "narration.mp3"

        # Respect the provider selected for this task instead of always using
        # the provider that was created when the application started.
        selected = (provider or self.settings.tts_provider or "edge").lower()
        active_provider = self._provider
        if selected not in ("edge", "edge_tts") or active_provider.name != "edge":
            active_provider = create_tts_provider(self.settings, selected)

        return await active_provider.synthesize(
            text,
            output_path,
            voice=voice,
            language=language,
        )
