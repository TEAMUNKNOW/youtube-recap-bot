"""Microsoft Edge neural TTS provider."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from pathlib import Path
from typing import List, Optional

from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo
from core.tts.audio_merge import synthesize_chunked

logger = logging.getLogger(__name__)

_FALLBACK_VOICES = {
    "hi": ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural", "en-IN-NeerjaNeural"],
    "en": ["en-US-ChristopherNeural", "en-US-JennyNeural", "en-GB-SoniaNeural"],
    "bn": ["bn-IN-TanishaaNeural", "bn-IN-BashkarNeural", "en-IN-NeerjaNeural"],
    "es": ["es-ES-AlvaroNeural", "es-MX-JorgeNeural", "en-US-JennyNeural"],
}


class EdgeTTSProvider(BaseTTSProvider):
    name = "edge"

    def __init__(self, default_voices: Optional[dict[str, str]] = None, proxy: Optional[str] = None) -> None:
        self.proxy = proxy
        self.default_voices = default_voices or {
            "hi": "hi-IN-MadhurNeural",
            "en": "en-US-ChristopherNeural",
            "bn": "bn-IN-TanishaaNeural",
            "es": "es-ES-AlvaroNeural",
        }

    @staticmethod
    def _clean_text(text: str) -> str:
        t = text.strip()
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t)
        return t

    async def synthesize(
        self, text: str, output_path: Path, *,
        voice: Optional[str] = None, language: Optional[str] = None,
    ) -> TTSResult:
        cleaned = self._clean_text(text)
        if not cleaned:
            raise TTSError("Empty text for TTS", retryable=False)

        lang_key = (language or "en")[:2].lower()
        voices_to_try: list[str] = []
        if voice:
            voices_to_try.append(voice)
        primary = self._pick_voice(language)
        if primary not in voices_to_try:
            voices_to_try.append(primary)
        for v in _FALLBACK_VOICES.get(lang_key, _FALLBACK_VOICES["en"]):
            if v not in voices_to_try:
                voices_to_try.append(v)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() not in (".mp3", ".wav"):
            output_path = output_path.with_suffix(".mp3")

        try:
            import edge_tts
        except ImportError as exc:
            raise TTSError("edge-tts package not installed", retryable=False) from exc

        async def synth_one(chunk: str, path: Path) -> None:
            # Edge's free endpoint can intermittently return NoAudioReceived even
            # for valid voices. Keep requests serialized and retry with backoff;
            # parallel websocket requests make this failure mode more likely.
            last_exc: Optional[Exception] = None
            for voice_index, voice_id in enumerate(voices_to_try):
                # The configured proxy is tried first. If it fails, also try a
                # direct connection before moving to another voice.
                proxy_candidates = [self.proxy]
                if self.proxy is not None:
                    proxy_candidates.append(None)

                for proxy_index, proxy in enumerate(proxy_candidates):
                    attempts = 3 if voice_index == 0 and proxy_index == 0 else 2
                    for attempt in range(1, attempts + 1):
                        try:
                            path.unlink(missing_ok=True)
                            communicate = edge_tts.Communicate(
                                chunk,
                                voice_id,
                                rate="+0%",
                                volume="+0%",
                                proxy=proxy,
                                connect_timeout=20,
                                receive_timeout=90,
                            )
                            await communicate.save(str(path))
                            if path.exists() and path.stat().st_size >= 100:
                                logger.info(
                                    "Edge TTS chunk ok voice=%s proxy=%s",
                                    voice_id,
                                    "configured" if proxy else "direct",
                                )
                                return
                            last_exc = TTSError(
                                "Edge TTS produced empty file", retryable=True
                            )
                        except Exception as exc:
                            last_exc = exc
                            logger.warning(
                                "Edge TTS voice=%s proxy=%s attempt=%s/%s failed: %s",
                                voice_id,
                                "configured" if proxy else "direct",
                                attempt,
                                attempts,
                                exc,
                            )
                        if attempt < attempts:
                            await asyncio.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))) + random.random())

            raise TTSError(f"Edge TTS chunk failed: {last_exc}", retryable=True)

        try:
            duration = await synthesize_chunked(
                cleaned, output_path, max_chars=2500, concurrency=1,
                synthesize_one=synth_one,
            )
        except Exception as exc:
            if isinstance(exc, TTSError):
                raise
            raise TTSError(f"Edge TTS failed: {exc}", retryable=True) from exc

        if duration <= 0:
            raise TTSError("Edge TTS produced invalid audio", retryable=True)
        logger.info("Edge TTS complete voice=%s duration=%.1fs", voice or self._pick_voice(language), duration)
        return TTSResult(
            path=str(output_path), duration=duration, provider=self.name,
            voice=voice or self._pick_voice(language),
        )

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
            result.append(
                VoiceInfo(
                    id=v.get("ShortName", ""),
                    name=v.get("FriendlyName", v.get("ShortName", "")),
                    language=locale,
                    gender=v.get("Gender"),
                )
            )
        return result

    def _pick_voice(self, language: Optional[str]) -> str:
        if language:
            key = language[:2].lower()
            if key in self.default_voices:
                return self.default_voices[key]
        return self.default_voices.get("en", "en-US-ChristopherNeural")

    @staticmethod
    async def _probe_duration(path: Path) -> float:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
