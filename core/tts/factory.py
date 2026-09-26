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
from core.tts.omnivoice_provider import OmniVoiceProvider

logger = logging.getLogger(__name__)


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
    if name in ("local", "offline", "espeak", "espeak-ng"):
        return LocalTTSProvider()
    if name in ("omnivoice", "omni_voice", "omni"):
        instruct = settings.omnivoice_instruct or getattr(settings, "omnivoice_default_instruct", None)
        return OmniVoiceProvider(
            model_id=settings.omnivoice_model,
            device=settings.omnivoice_device,
            dtype=settings.omnivoice_dtype,
            ref_audio=settings.omnivoice_ref_audio,
            ref_text=settings.omnivoice_ref_text,
            instruct=instruct,
            num_steps=settings.omnivoice_num_steps,
            allow_cpu=bool(getattr(settings, "omnivoice_allow_cpu", False)),
            default_language="hi",
            default_gender=getattr(settings, "omnivoice_gender", "male") or "male",
        )

    raise TTSError(f"Unknown TTS provider: {name}", retryable=False)


def _canonical_provider(name: str) -> str:
    name = name.strip().lower()
    if name in ("edge", "edge_tts"):
        return "edge"
    if name in ("openai", "openai_tts"):
        return "openai"
    if name in ("elevenlabs", "eleven"):
        return "elevenlabs"
    if name in ("local", "offline", "espeak", "espeak-ng"):
        return "local"
    if name in ("omnivoice", "omni_voice", "omni"):
        return "omnivoice"
    return name


class TTSFactory:
    """Pipeline-facing TTS helper with automatic provider failover."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._provider = create_tts_provider(settings)

    def _provider_order(self, selected: str) -> list[str]:
        order = [_canonical_provider(selected)]
        for name in self.settings.tts_fallback_providers.split(","):
            canonical = _canonical_provider(name)
            if canonical and canonical not in order:
                order.append(canonical)
        return order

    def _voice_for(self, provider: str, requested_voice: Optional[str]) -> Optional[str]:
        if requested_voice and provider == _canonical_provider(self.settings.tts_provider):
            return requested_voice
        if provider == "edge":
            return requested_voice
        if provider == "openai":
            return self.settings.openai_tts_voice
        if provider == "local":
            return None
        if provider == "elevenlabs":
            return self.settings.elevenlabs_voice_id
        if provider == "omnivoice":
            if requested_voice:
                return requested_voice
            return "hi-adult-male"
        return requested_voice

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

        selected = (provider or self.settings.tts_provider or "edge").lower()
        last_error: Optional[Exception] = None

        for provider_name in self._provider_order(selected):
            try:
                active_provider = create_tts_provider(self.settings, provider_name)
            except TTSError as exc:
                logger.warning("TTS provider=%s unavailable: %s", provider_name, exc)
                last_error = exc
                continue

            selected_voice = self._voice_for(provider_name, voice)
            try:
                logger.info("TTS attempt provider=%s", provider_name)
                result = await active_provider.synthesize(
                    text,
                    output_path,
                    voice=selected_voice,
                    language=language,
                )
                if provider_name != _canonical_provider(selected):
                    logger.warning(
                        "TTS failover succeeded: primary=%s fallback=%s",
                        _canonical_provider(selected),
                        provider_name,
                    )
                return result
            except TTSError as exc:
                last_error = exc
                logger.warning(
                    "TTS provider=%s failed retryable=%s: %s",
                    provider_name,
                    exc.retryable,
                    exc,
                )
                continue
            except Exception as exc:
                last_error = exp if False else exc
                last_error = exc
                logger.exception("TTS provider=%s unexpected failure", provider_name)

        if isinstance(last_error, TTSError):
            raise last_error
        raise TTSError(f"All configured TTS providers failed: {last_error}", retryable=True)
