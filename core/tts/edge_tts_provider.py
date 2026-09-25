"""Microsoft Edge neural TTS provider."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import List, Optional

from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo

logger = logging.getLogger(__name__)

_FALLBACK_VOICES = {
    "hi": ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural", "en-IN-NeerjaNeural"],
    "en": ["en-US-ChristopherNeural", "en-US-JennyNeural", "en-GB-SoniaNeural"],
    "bn": ["bn-IN-TanishaaNeural", "bn-IN-BashkarNeural", "en-IN-NeerjaNeural"],
    "es": ["es-ES-AlvaroNeural", "es-MX-JorgeNeural", "en-US-JennyNeural"],
}


class EdgeTTSProvider(BaseTTSProvider):
    name = "edge"

    def __init__(self, default_voices: Optional[dict[str, str]] = None) -> None:
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
        if len(t) > 3000:
            t = t[:3000].rsplit(" ", 1)[0] + "."
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

        last_exc: Optional[Exception] = None
        for voice_id in voices_to_try:
            try:
                if output_path.exists():
                    output_path.unlink(missing_ok=True)
                communicate = edge_tts.Communicate(cleaned, voice_id, rate="+0%", volume="+0%")
                await communicate.save(str(output_path))
                if output_path.exists() and output_path.stat().st_size >= 100:
                    duration = await self._probe_duration(output_path)
                    logger.info("Edge TTS ok voice=%s duration=%.1fs", voice_id, duration)
                    return TTSResult(
                        path=str(output_path),
                        duration=duration,
                        provider=self.name,
                        voice=voice_id,
                    )
                last_exc = TTSError("Edge TTS produced empty file", retryable=True)
            except Exception as exc:
                logger.warning("Edge TTS voice %s failed: %s", voice_id, exc)
                last_exc = exc
                continue

        raise TTSError(
            f"Edge TTS failed after {len(voices_to_try)} voices: {last_exc}",
            retryable=True,
        ) from last_exc

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
