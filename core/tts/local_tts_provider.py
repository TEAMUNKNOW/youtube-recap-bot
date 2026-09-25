"""Local offline TTS provider using espeak-ng.

This provider is intentionally dependency-light and requires no API key.
It is used as a safety-net when cloud TTS providers are unavailable.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import List, Optional

from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo
from core.tts.audio_merge import synthesize_chunked


class LocalTTSProvider(BaseTTSProvider):
    name = "local"

    def __init__(self) -> None:
        if not shutil.which("espeak-ng"):
            raise TTSError("espeak-ng is not installed", retryable=False)

    @staticmethod
    def _voice(language: Optional[str]) -> str:
        key = (language or "en")[:2].lower()
        return {"hi": "hi", "bn": "bn", "es": "es"}.get(key, "en-us")

    async def synthesize(
        self, text: str, output_path: Path, *,
        voice: Optional[str] = None, language: Optional[str] = None,
    ) -> TTSResult:
        if not text.strip():
            raise TTSError("Empty text for TTS", retryable=False)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path = output_path.with_suffix(".mp3")
        selected_voice = voice or self._voice(language)

        async def synth_one(chunk: str, path: Path) -> None:
            wav_path = path.with_suffix(".wav")
            cmd = [
                "espeak-ng", "-v", selected_voice, "-s", "155",
                "-w", str(wav_path), chunk,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size < 100:
                raise TTSError(
                    f"Local espeak-ng failed: {stderr.decode(errors='replace')[-500:]}",
                    retryable=True,
                )
            convert = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", "128k",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, convert_err = await convert.communicate()
            wav_path.unlink(missing_ok=True)
            if convert.returncode != 0 or not path.exists() or path.stat().st_size < 100:
                raise TTSError(
                    f"Local audio conversion failed: {convert_err.decode(errors='replace')[-500:]}",
                    retryable=True,
                )

        try:
            duration = await synthesize_chunked(
                text, output_path, max_chars=1800, concurrency=1,
                synthesize_one=synth_one,
            )
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"Local TTS failed: {exc}", retryable=True) from exc

        if duration <= 0:
            raise TTSError("Local TTS produced invalid audio", retryable=True)
        return TTSResult(
            path=str(output_path), duration=duration,
            provider=self.name, voice=selected_voice,
        )

    async def list_voices(self, language: Optional[str] = None) -> List[VoiceInfo]:
        return [VoiceInfo(id=self._voice(language), name="espeak-ng local", language=language or "en")]
