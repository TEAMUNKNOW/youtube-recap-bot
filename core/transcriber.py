"""Pluggable transcription: Groq Whisper primary, faster-whisper fallback."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from bot.config import Settings
from bot.exceptions import TranscriptionError
from core.retry import retry_async

logger = logging.getLogger(__name__)


class Word(BaseModel):
    word: str
    start: float
    end: float
    confidence: Optional[float] = None


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: List[Word] = Field(default_factory=list)
    confidence: Optional[float] = None


class Transcript(BaseModel):
    language: str
    duration: float
    text: str
    segments: List[Segment] = Field(default_factory=list)
    words: List[Word] = Field(default_factory=list)
    confidence: Optional[float] = None
    provider: str = "unknown"


class Transcriber:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(self, audio_path: Path, *, language: Optional[str] = None) -> Transcript:
        if not audio_path.exists():
            raise TranscriptionError(f"Audio not found: {audio_path.name}", retryable=False)
        errors: list[str] = []
        if self.settings.groq_api_key:
            try:
                return await retry_async(self._groq_transcribe, max_attempts=2, audio_path=audio_path, language=language)
            except Exception as exc:
                logger.warning("Groq transcription failed: %s", exc)
                errors.append(f"groq: {exc}")
        try:
            return await self._local_transcribe(audio_path, language=language)
        except Exception as exc:
            logger.error("Local transcription failed: %s", exc)
            errors.append(f"local: {exc}")
        raise TranscriptionError("All transcription providers failed: " + "; ".join(errors), retryable=True)

    async def _groq_transcribe(self, audio_path: Path, language: Optional[str] = None) -> Transcript:
        import httpx
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.settings.groq_api_key}"}
        data: dict[str, Any] = {"model": "whisper-large-v3", "response_format": "verbose_json", "timestamp_granularities[]": "word"}
        if language and language not in ("auto", "original"):
            data["language"] = language[:2]
        async with httpx.AsyncClient(timeout=600.0) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path.name, f, "application/octet-stream")}
                resp = await client.post(url, headers=headers, data=data, files=files)
        if resp.status_code == 429:
            raise TranscriptionError("Groq rate limited", retryable=True)
        if resp.status_code >= 500:
            raise TranscriptionError(f"Groq server error {resp.status_code}", retryable=True)
        if resp.status_code != 200:
            raise TranscriptionError(f"Groq error {resp.status_code}: {resp.text[:300]}", retryable=False)
        return self._parse_openai_style(resp.json(), provider="groq")

    async def _local_transcribe(self, audio_path: Path, language: Optional[str] = None) -> Transcript:
        def _run() -> Transcript:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError("faster-whisper not installed and Groq unavailable", retryable=False) from exc
            model = WhisperModel("base", device="cpu", compute_type="int8")
            lang = None if not language or language in ("auto", "original") else language[:2]
            segments_iter, info = model.transcribe(str(audio_path), language=lang, word_timestamps=True, vad_filter=True)
            segments: list[Segment] = []
            all_words: list[Word] = []
            texts: list[str] = []
            for i, seg in enumerate(segments_iter):
                words = []
                if seg.words:
                    for w in seg.words:
                        words.append(Word(word=w.word.strip(), start=float(w.start), end=float(w.end), confidence=getattr(w, "probability", None)))
                all_words.extend(words)
                texts.append(seg.text.strip())
                segments.append(Segment(id=i, start=float(seg.start), end=float(seg.end), text=seg.text.strip(), words=words, confidence=getattr(seg, "avg_logprob", None)))
            return Transcript(language=info.language or "unknown", duration=float(info.duration or 0), text=" ".join(texts), segments=segments, words=all_words, provider="faster-whisper")
        return await asyncio.to_thread(_run)

    @staticmethod
    def _parse_openai_style(payload: dict[str, Any], provider: str) -> Transcript:
        text = payload.get("text") or ""
        if not text.strip():
            raise TranscriptionError("Empty transcript returned", retryable=True)
        language = payload.get("language") or "unknown"
        duration = float(payload.get("duration") or 0)
        segments: list[Segment] = []
        all_words: list[Word] = []
        for i, seg in enumerate(payload.get("segments") or []):
            words = []
            for w in seg.get("words") or []:
                word = Word(word=str(w.get("word", "")).strip(), start=float(w.get("start") or 0), end=float(w.get("end") or 0))
                words.append(word)
                all_words.append(word)
            segments.append(Segment(id=i, start=float(seg.get("start") or 0), end=float(seg.get("end") or 0), text=str(seg.get("text") or "").strip(), words=words))
        if not all_words:
            for w in payload.get("words") or []:
                all_words.append(Word(word=str(w.get("word", "")).strip(), start=float(w.get("start") or 0), end=float(w.get("end") or 0)))
        return Transcript(language=language, duration=duration, text=text.strip(), segments=segments, words=all_words, provider=provider)
