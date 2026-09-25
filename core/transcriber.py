"""Pluggable transcription: Groq Whisper primary, faster-whisper fallback."""

from __future__ import annotations

import asyncio
import logging
import math
import tempfile
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
                # Keep individual uploads comfortably below Groq's file-size limit.
                # Normal short audio still uses one request; long audio is split
                # into timestamped chunks and transcribed concurrently.
                if audio_path.stat().st_size > 18 * 1024 * 1024:
                    return await self._groq_transcribe_chunked(audio_path, language=language)
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

    async def _groq_transcribe_chunked(self, audio_path: Path, language: Optional[str] = None) -> Transcript:
        """Transcribe long audio in bounded chunks, with up to two requests in parallel."""
        import subprocess

        # 10 minutes is small enough for reliable uploads while keeping request
        # overhead reasonable. The source is already compressed to 64 kbps MP3.
        chunk_seconds = 600.0
        duration = await self._probe_duration(audio_path)
        if duration <= chunk_seconds:
            return await retry_async(
                self._groq_transcribe,
                max_attempts=2,
                audio_path=audio_path,
                language=language,
            )

        chunks: list[tuple[int, float, float, Path]] = []
        temp_dir = Path(tempfile.mkdtemp(prefix="groq_chunks_"))
        try:
            count = int(math.ceil(duration / chunk_seconds))
            for i in range(count):
                start = i * chunk_seconds
                length = min(chunk_seconds, duration - start)
                out = temp_dir / f"chunk_{i:04d}.mp3"
                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start:.3f}", "-i", str(audio_path),
                    "-t", f"{length:.3f}", "-ac", "1", "-ar", "16000",
                    "-c:a", "libmp3lame", "-b:a", "64k", str(out),
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                _, err = await proc.communicate()
                if proc.returncode != 0:
                    raise TranscriptionError(
                        f"Failed to create transcription chunk {i + 1}: {err.decode(errors='replace')[-500:]}",
                        retryable=True,
                    )
                chunks.append((i, start, length, out))

            sem = asyncio.Semaphore(2)

            async def one(item: tuple[int, float, float, Path]) -> tuple[int, float, Transcript]:
                i, start, _, path = item
                async with sem:
                    result = await retry_async(
                        self._groq_transcribe,
                        max_attempts=2,
                        audio_path=path,
                        language=language,
                    )
                logger.info("Groq transcription chunk %s/%s complete", i + 1, len(chunks))
                return i, start, result

            results = await asyncio.gather(*(one(item) for item in chunks))
            results.sort(key=lambda x: x[0])

            all_segments: list[Segment] = []
            all_words: list[Word] = []
            texts: list[str] = []
            detected_language = "unknown"
            for _, offset, result in results:
                detected_language = result.language or detected_language
                if result.text.strip():
                    texts.append(result.text.strip())
                for seg in result.segments:
                    all_segments.append(
                        Segment(
                            id=len(all_segments),
                            start=seg.start + offset,
                            end=seg.end + offset,
                            text=seg.text,
                            words=[
                                Word(
                                    word=w.word,
                                    start=w.start + offset,
                                    end=w.end + offset,
                                    confidence=w.confidence,
                                )
                                for w in seg.words
                            ],
                            confidence=seg.confidence,
                        )
                    )
                for w in result.words:
                    all_words.append(
                        Word(
                            word=w.word,
                            start=w.start + offset,
                            end=w.end + offset,
                            confidence=w.confidence,
                        )
                    )

            return Transcript(
                language=detected_language,
                duration=duration,
                text=" ".join(texts),
                segments=all_segments,
                words=all_words,
                provider="groq",
            )
        finally:
            for p in temp_dir.glob("*"):
                p.unlink(missing_ok=True)
            temp_dir.rmdir()

    @staticmethod
    async def _probe_duration(path: Path) -> float:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0

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
