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


def create_tts_provider(settings: Settings, provider: Optional[str] = None) -> BaseTTSProvider:
    name = (provider or settings.tts_provider or "edge").lower()

    if name in ("edge", "edge_tts"):
        return EdgeTTSProvider(
            default_voices={
                "hi": getattr(settings, "hindi_voice", "hi-IN-MadhurNeural"),
                "en": getattr(settings, "english_voice", "en-US-ChristopherNeural"),
                "bn": getattr(settings, "bengali_voice", "bn-IN-TanishaaNeural"),
                "es": getattr(settings, "spanish_voice", "es-ES-AlvaroNeural"),
            },
            proxy=getattr(settings, "proxy_url", None) or getattr(settings, "http_proxy", None),
        )
    if name in ("elevenlabs", "eleven"):
        return ElevenLabsProvider(settings)
    if name in ("openai", "openai_tts"):
        return OpenAITTSProvider(settings)
    if name in ("local", "offline", "espeak", "espeak-ng"):
        return LocalTTSProvider()
    if name in ("omnivoice", "omni_voice", "omni"):
        try:
            from core.tts.omnivoice_provider import OmniVoiceProvider
        except Exception as exc:
            raise TTSError(
                "OmniVoice not installed. On GPU host run: pip install torch torchaudio omnivoice soundfile",
                retryable=False,
            ) from exp
        instruct = getattr(settings, "omnivoice_instruct", None) or getattr(
            settings, "omnivoice_default_instruct", "male, adult, medium pitch"
        )
        return OmniVoiceProvider(
            model_id=getattr(settings, "omnivoice_model", "facebook/omnilingual-asr-300m"),
            device=getattr(settings, "omnivoice_device", "cpu"),
            dtype=getattr(settings, "omnivoice_dtype", "float32"),
            ref_audio=getattr(settings, "omnivoice_ref_audio", None),
            ref_text=getattr(settings, "omnivoice_ref_text", None),
            instruct=instruct,
            num_steps=getattr(settings, "omnivoice_num_steps", 20),
            allow_cpu=bool(getattr(settings, "omnivoice_allow_cpu", False)),
            default_language="hi",
            default_gender=getattr(settings, "omnivoice_gender", "male") or "male",
        )

    raise TTSError(f"Unknown TTS provider: {name}", retryable=False)


def _canonical_provider(name: str) -> str:
    name = (name or "").strip().lower()
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
    return name or "edge"


class TTSFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._provider = create_tts_provider(settings)

    def _provider_order(self, selected: str) -> list[str]:
        primary = _canonical_provider(selected)
        raw = getattr(self.settings, "tts_fallback_providers", "local,openai,elevenlabs") or ""
        fallbacks = [_canonical_provider(p) for p in raw.split(",") if p.strip()]
        order = [primary]
        for p in fallbacks:
            if p and p not in order:
                order.append(p)
        return order

    def _voice_for(self, provider: str, requested_voice: Optional[str] = None) -> Optional[str]:
        provider = _canonical_provider(provider)
        if provider == "openai":
            return requested_voice or getattr(self.settings, "openai_tts_voice", "alloy")
        if provider == "elevenlabs":
            return requested_voice or getattr(self.settings, "elevenlabs_voice_id", None)
        if provider == "omnivoice":
            return requested_voice or "hi-adult-male"
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
            except TTSError as exp:
                logger.warning("TTS provider=%s unavailable: %s", provider_name, exp)
                last_error = exp
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
            except TTSError as exp:
                last_error = exp
                logger.warning(
                    "TTS provider=%s failed retryable=%s: %s",
                    provider_name,
                    getattr(exp, "retryable", False),
                    exp,
                )
                continue
            except Exception as exp:
                last_error = exp
                logger.exception("TTS provider=%s unexpected failure", provider_name)

        if isinstance(last_error, TTSError):
            raise last_error
        raise TTSError(f"All configured TTS providers failed: {last_error}", retryable=True)
