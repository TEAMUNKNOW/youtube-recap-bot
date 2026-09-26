"""LLM agent for recap scripts and SEO metadata with multi-model free fallback."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from bot.config import Settings
from bot.exceptions import LLMError
from core.retry import retry_async

logger = logging.getLogger(__name__)

GROQ_FREE_MODELS: Sequence[str] = (
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
)


class Chapter(BaseModel):
    title: str
    start_seconds: float = 0.0


class RecapScript(BaseModel):
    title: str
    script: str
    word_count: int = 0
    tone: str = "dramatic"
    chapters: List[Chapter] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list)
    chapter_word_counts: List[int] = Field(default_factory=list)


class SEOResult(BaseModel):
    titles: List[str] = Field(min_length=1, max_length=5)
    description: str
    tags: List[str] = Field(default_factory=list)
    hashtags: List[str] = Field(default_factory=list)
    chapters: List[Chapter] = Field(default_factory=list)


class ContentAnalysis(BaseModel):
    summary: str
    language: str = "en"
    content_type: str = "general"
    sensitive: bool = False


class LLMAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _duration_ratio(self) -> float:
        """Output narration length as fraction of source. Default 1.0 = same minutes."""
        r = float(getattr(self.settings, "recap_duration_ratio", 1.0) or 1.0)
        return max(0.5, min(1.2, r))

    async def generate_recap_script(
        self,
        transcript_text: str,
        *,
        duration_seconds: float,
        language: str = "en",
        mode: str = "AI_RECAP",
    ) -> RecapScript:
        wpm = max(110, min(145, int(self.settings.words_per_minute)))
        source_min = max(0.5, duration_seconds / 60.0)
        ratio = self._duration_ratio()
        # Same-length default: ~source_min minutes of spoken audio
        hard_cap = 60000
        target_words = max(150, min(hard_cap, int(source_min * ratio * wpm)))

        lang_name = {
            "hi": "Hindi",
            "en": "English",
            "bn": "Bengali",
            "es": "Spanish",
        }.get(language[:2].lower(), language)

        system = (
            "You are an expert video narrator. Write a full-length spoken script that "
            "covers the entire source from start to finish. The spoken length MUST match "
            "the requested duration — do NOT compress a long video into a short summary. "
            "Do not fabricate facts. Output valid JSON only."
        )
        user = (
            f"Source video duration: {duration_seconds:.0f} seconds ({source_min:.1f} minutes).\n"
            f"REQUIRED spoken length: about {source_min * ratio:.1f} minutes "
            f"≈ {target_words} words at ~{wpm} words/min.\n"
            f"You MUST produce roughly {target_words} words. "
            f"If the transcript is long, cover every major section in order.\n"
            f"Output language: {lang_name}.\n"
            f"Mode: {mode}.\n\n"
            f"Transcript:\n{self._prepare_transcript(transcript_text, 14000)}\n\n"
            "Return JSON with keys: title (string), script (string, the full narration), "
            "word_count (int), tone (string), chapters (array of {title, start_seconds}), "
            "key_points (array of strings)."
        )

        raw = await self._complete(system, user)
        data = self._extract_json(raw)
        try:
            script = RecapScript.model_validate(data)
        except PydanticValidationError as exc:
            repair = await self._complete(
                system,
                f"Fix this into valid JSON matching the schema. Errors: {exc}\n\n{raw[:4000]}",
            )
            data = self._extract_json(repair)
            script = RecapScript.model_validate(data)

        if not script.script.strip():
            raise LLMError("Empty script generated", retryable=True)
        if script.word_count <= 0:
            script.word_count = len(script.script.split())
        return script

    async def generate_long_form_recap(
        self,
        transcript: Any,
        *,
        duration_seconds: float,
        language: str = "en",
        mode: str = "AI_RECAP",
    ) -> RecapScript:
        segments = list(getattr(transcript, "segments", None) or [])
        # Chunk any video longer than 12 minutes so each part can hit full length
        if not segments or duration_seconds <= 12 * 60:
            return await self.generate_recap_script(
                getattr(transcript, "text", str(transcript)),
                duration_seconds=duration_seconds,
                language=language,
                mode=mode,
            )

        target_wpm = max(110, min(145, int(self.settings.words_per_minute)))
        ratio = self._duration_ratio()
        chunk_seconds = 420.0  # ~7 min chunks → safer for free LLM token limits
        chunks: list[tuple[float, float, str]] = []
        start = float(getattr(segments[0], "start", 0.0)) if segments else 0.0
        buf: list[str] = []
        chunk_start = start
        for seg in segments:
            seg_start = float(getattr(seg, "start", chunk_start))
            if buf and seg_start - chunk_start >= chunk_seconds:
                chunks.append((chunk_start, seg_start, " ".join(buf).strip()))
                buf = []
                chunk_start = seg_start
            buf.append(str(getattr(seg, "text", "")).strip())
        if buf:
            chunks.append(
                (
                    chunk_start,
                    float(getattr(segments[-1], "end", duration_seconds)),
                    " ".join(buf).strip(),
                )
            )

        if not chunks:
            return await self.generate_recap_script(
                getattr(transcript, "text", str(transcript)),
                duration_seconds=duration_seconds,
                language=language,
                mode=mode,
            )

        lang_name = {
            "hi": "Hindi",
            "en": "English",
            "bn": "Bengali",
            "es": "Spanish",
        }.get(language[:2].lower(), language)
        titles: list[str] = []
        scripts: list[str] = []
        chapters: list[Chapter] = []
        key_points: list[str] = []
        chapter_word_counts: list[int] = []

        for index, (start_s, end_s, chunk_text) in enumerate(chunks, 1):
            span = max(30.0, end_s - start_s)
            # Match this chunk's wall-clock length
            target_words = max(80, int(span / 60.0 * target_wpm * ratio))
            system = (
                "You are a full-length video narrator. Rewrite this section as spoken "
                "narration of the SAME duration as the source section. Do not compress. "
                "Do not fabricate facts. Output valid JSON only."
            )
            user = (
                f"Language: {lang_name}. Part {index}/{len(chunks)}. "
                f"Source time: {start_s:.1f}-{end_s:.1f}s ({span/60:.1f} min).\n"
                f"REQUIRED narration: about {target_words} words "
                f"(~{span/60 * ratio:.1f} minutes spoken).\n"
                f"Transcript for this part:\n{self._prepare_transcript(chunk_text, 9000)}\n\n"
                "Return JSON with keys: title, script, word_count, tone, chapters, key_points."
            )
            raw = await self._complete(system, user)
            data = self._extract_json(raw)
            part = RecapScript.model_validate(data)
            if not part.script.strip():
                raise LLMError(f"Empty narration for part {index}", retryable=True)
            titles.append(part.title.strip())
            scripts.append(part.script.strip())
            chapters.append(
                Chapter(title=part.title.strip() or f"Part {index}", start_seconds=start_s)
            )
            key_points.extend(part.key_points[:8])
            chapter_word_counts.append(len(part.script.split()))

        return RecapScript(
            title=titles[0] if titles else "Video Recap",
            script="\n\n".join(scripts),
            word_count=sum(len(s.split()) for s in scripts),
            tone="full-length narrator",
            chapters=chapters,
            key_points=key_points[:40],
            chapter_word_counts=chapter_word_counts,
        )

    async def generate_seo(
        self,
        script: str,
        *,
        original_title: Optional[str] = None,
        language: str = "en",
    ) -> SEOResult:
        system = (
            "You are a YouTube SEO specialist. Titles under 60 characters. Output valid JSON only."
        )
        user = (
            f"Language: {language}\n"
            f"Original title hint: {original_title or 'N/A'}\n\n"
            f"Script / content:\n{script[:8000]}\n\n"
            "Return JSON: titles (array of 3 strings), description, tags, hashtags, "
            "chapters (array of {title, start_seconds})."
        )
        raw = await self._complete(system, user)
        data = self._extract_json(raw)
        try:
            seo = SEOResult.model_validate(data)
        except PydanticValidationError:
            repair = await self._complete(system, f"Fix into valid SEO JSON:\n{raw[:4000]}")
            seo = SEOResult.model_validate(self._extract_json(repair))
        seo.titles = [t[:60].strip() for t in seo.titles if t.strip()][:3]
        if not seo.titles:
            raise LLMError("No valid titles generated", retryable=True)
        return seo

    def _model_candidates(self) -> List[str]:
        preferred = (getattr(self.settings, "groq_model", None) or "").strip()
        seen: set[str] = set()
        out: List[str] = []
        for m in list(GROQ_FREE_MODELS) + ([preferred] if preferred else []):
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    async def _complete(self, system: str, user: str) -> str:
        provider = (self.settings.llm_provider or "groq").lower()
        if self.settings.groq_api_key and provider in ("groq", "auto", ""):
            return await self._groq_complete_with_fallback(system, user)
        if provider == "openai" and self.settings.openai_api_key:
            return await retry_async(self._openai_complete, max_attempts=2, system=system, user=user)
        if provider == "gemini" and self.settings.gemini_api_key:
            return await retry_async(self._gemini_complete, max_attempts=2, system=system, user=user)
        if self.settings.groq_api_key:
            return await self._groq_complete_with_fallback(system, user)
        if self.settings.openai_api_key:
            return await retry_async(self._openai_complete, max_attempts=2, system=system, user=user)
        if self.settings.gemini_api_key:
            return await retry_async(self._gemini_complete, max_attempts=2, system=system, user=user)
        raise LLMError("No LLM API key configured", retryable=False)

    async def _groq_complete_with_fallback(self, system: str, user: str) -> str:
        models = self._model_candidates()
        last_err: Optional[Exception] = None
        for model in models:
            try:
                text = await self._groq_complete_one(system, user, model)
                logger.info("Groq LLM success with model=%s", model)
                return text
            except LLMError as exc:
                last_err = exc
                logger.warning("Groq model %s failed, trying next: %s", model, exc)
                continue
        if self.settings.openai_api_key:
            try:
                return await retry_async(self._openai_complete, max_attempts=2, system=system, user=user)
            except Exception as exc:
                last_err = exc
        if self.settings.gemini_api_key:
            try:
                return await retry_async(self._gemini_complete, max_attempts=2, system=system, user=user)
            except Exception as exc:
                last_err = exc
        raise LLMError(f"All LLM providers failed: {last_err}", retryable=True) from last_err

    async def _groq_complete_one(self, system: str, user: str, model: str) -> str:
        import httpx

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code == 429:
            raise LLMError(f"Groq rate limited ({model})", retryable=True)
        if resp.status_code == 413:
            raise LLMError(f"Groq payload too large ({model})", retryable=True)
        if resp.status_code >= 500:
            raise LLMError(f"Groq server error {resp.status_code} ({model})", retryable=True)
        if resp.status_code != 200:
            raise LLMError(
                f"Groq error {resp.status_code} ({model}): {resp.text[:300]}",
                retryable=False,
            )
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Malformed Groq response ({model})", retryable=True) from exc

    async def _gemini_complete(self, system: str, user: str) -> str:
        import httpx

        model = self.settings.gemini_model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.settings.gemini_api_key}"
        )
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
            },
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, json=body)
        if resp.status_code == 429:
            raise LLMError("Gemini rate limited", retryable=True)
        if resp.status_code >= 500:
            raise LLMError(f"Gemini server error {resp.status_code}", retryable=True)
        if resp.status_code != 200:
            raise LLMError(f"Gemini error {resp.status_code}: {resp.text[:300]}", retryable=False)
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exp:
            raise LLMError("Malformed Gemini response", retryable=True) from exp

    async def _openai_complete(self, system: str, user: str) -> str:
        import httpx

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code == 429:
            raise LLMError("OpenAI rate limited", retryable=True)
        if resp.status_code >= 500:
            raise LLMError(f"OpenAI server error {resp.status_code}", retryable=True)
        if resp.status_code != 200:
            raise LLMError(f"OpenAI error {resp.status_code}: {resp.text[:300]}", retryable=False)
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exp:
            raise LLMError("Malformed OpenAI response", retryable=True) from exp

    @staticmethod
    def _prepare_transcript(text: str, max_chars: int = 12000) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text
        third = max_chars // 3
        middle_start = max(0, (len(text) - third) // 2)
        return (
            text[:third]
            + "\n\n[...middle omitted...]\n\n"
            + text[middle_start : middle_start + third]
            + "\n\n[...later omitted...]\n\n"
            + text[-third:]
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        text = text.strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise LLMError("Could not parse JSON from LLM response", retryable=True)
