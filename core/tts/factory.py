"""TTS provider factory with resilient automatic failover."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from bot.config import Settings
from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult
from core.tts.edge_tts_provider import EdgeTTSProvider
from core.tts.elevenlabs_provider import ElevenLabsProvider
from core.tts.openai_tts_provider import OpenAITTSProvider
from core.tts.local_tts_provider import LocalTTSProvider

logger = logging.getLogger(__name__)


def _canonical_provider(name: str) -> str:
    return (name or "edge").strip().lower()


def create_tts_provider(settings: Settings, provider_name: Optional[str] = None) -> BaseTTSProvider:
    name = _canonical_provider(provider_name or settings.tts_provider)
    default_voices = {
        "hi": getattr(settings, "hindi_voice", "hi-IN-MadhurNeural"),
        "en": getattr(settings, "english_voice", "en-US-ChristopherNeural"),
        "bn": getattr(settings, "bengali_voice", "bn-IN-TanishaaNeural"),
        "es": getattr(settings, "spanish_voice", "es-ES-AlvaroNeural"),
    }
    proxy = getattr(settings, "proxy_url", None) or getattr(settings, "http_proxy", None)

    if name in ("edge", "edge-tts", "edge_tts"):
        return EdgeTTSProvider(default_voices=default_voices, proxy=proxy)
    if name in ("local", "espeak", "espeak-ng"):
        return LocalTTSProvider()
    if name in ("openai", "openai-tts"):
        if not settings.openai_api_key:
            raise TTSError("OPENAI_API_KEY required for OpenAI TTS", retryable=False)
        return OpenAITTSProvider(
            api_key=settings.openai_api_key,
            voice=getattr(settings, "openai_tts_voice", "alloy"),
        )
    if name in ("elevenlabs", "11labs"):
        if not settings.elevenlabs_api_key:
            raise TTSError("ELEVENLABS_API_KEY required", retryable=False)
        return ElevenLabsProvider(
            api_key=settings.elevenlabs_api_key,
            voice_id=getattr(settings, "elevenlabs_voice_id", None),
        )
    if name in ("omnivoice", "omni"):
        try:
            from core.tts.omnivoice_provider import OmniVoiceProvider
        except Exception as exc:
            raise TTSError(
                "OmniVoice not installed. On GPU host run: pip install torch torchaudio omnivoice soundfile",
                retryable=False,
            ) from exc
        return OmniVoiceProvider(
            instruct=getattr(settings, "omnivoice_instruct", None)
            or getattr(settings, "omnivoice_default_instruct", "male, adult, medium pitch"),
            model_id=getattr(settings, "omnivoice_model", "facebook/omnilingual-asr-300m"),
            device=getattr(settings, "omnivoice_device", "cpu"),
            dtype=getattr(settings, "omnivoice_dtype", "float32"),
            ref_audio=getattr(settings, "omnivoice_ref_audio", None),
            ref_text=getattr(settings, "omnivoice_ref_text", None),
            num_steps=getattr(settings, "omnivoice_num_steps", 20),
        )
    raise TTSError(f"Unknown TTS provider: {name}", retryable=False)


class TTSFactory:
    """Primary provider + ordered failover chain."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._provider = create_tts_provider(settings)
        raw = getattr(settings, "tts_fallback_providers", "local,openai,elevenlabs") or ""
        self._fallbacks = [p.strip() for p in raw.split(",") if p.strip()]

    @property
    def provider_name(self) -> str:
        return _canonical_provider(self.settings.tts_provider)

    def _voice_for(self, language: str, requested_voice: Optional[str] = None) -> Optional[str]:
        provider = self.provider_name
        if requested_voice and provider == _canonical_provider(self.settings.tts_provider):
            return requested_voice
        if provider in ("openai", "openai-tts"):
            return getattr(self.settings, "openai_tts_voice", "alloy")
        if provider in ("elevenlabs", "11labs"):
            return getattr(self.settings, "elevenlabs_voice_id", None)
        return None

    async def synthesize(
        self,
        text: str,
        *,
        language: str = "en",
        voice: Optional[str] = None,
        output_path: Optional[Path] = None,
    ) -> TTSResult:
        order = [self.provider_name] + [
            _canonical_provider(p)
            for p in self._fallbacks
            if _canonical_provider(p) != self.provider_name
        ]
        # Never try omnivoice on CPU-only hosts first if primary failed hard
        last_err: Optional[Exception] = None
        for name in order:
            try:
                logger.info("TTS attempt provider=%s", name)
                provider = create_tts_provider(self.settings, name)
                result = await provider.synthesize(
                    text,
                    language=language,
                    voice=voice or self._voice_for(language, voice),
                    output_path=output_path,
                )
                if name != self.provider_name:
                    logger.warning(
                        "TTS failover succeeded: primary=%s fallback=%s",
                        self.provider_name,
                        name,
                    )
                return result
            except TTSError as exc:
                last_err = exc
                logger.warning(
                    "TTS provider=%s failed retryable=%s: %s",
                    name,
                    getattr(exc, "retryable", False),
                    exc,
                )
                if not getattr(exc, "retryable", True) and name == "omnivoice":
                    continue
                continue
            except Exception as exc:
                last_err = TTSError(str(exc), retryable=True)
                logger.warning("TTS provider=%s unexpected error: %s", name, exp)
                continue
        raise TTSError(f"All TTS providers failed: {last_err}", retryable=False) from last_err
