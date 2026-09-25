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

# Free-tier friendly Groq model IDs (Sept 2026). Tried in order on failure.
GROQ_FREE_MODELS: Sequence[str] = (
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
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

    async def generate_recap_script(
        self,
        transcript_text: str,
        *,
        duration_seconds: float,
        language: str = "en",
        mode: str = "AI_RECAP",
    ) -> RecapScript:
        target_words = min(
            1800,
            max(50, int((duration_seconds / 60.0) * self.settings.words_per_minute)),
        )
        lang_name = {
            "hi": "Hindi",
            "en": "English",
            "bn": "Bengali",
            "es": "Spanish",
        }.get(language[:2].lower(), language)

        system = (
            "You are an expert video recap writer. Produce a dramatic, coherent "
            "recap/commentary script. Do NOT fabricate facts. Preserve important "
            "plot and context. Do not unnecessarily reproduce source dialogue "
            "verbatim. Output valid JSON only."
        )
        user = (
            f"Video duration: {duration_seconds:.0f} seconds.\n"
            f"Target word count: approximately {target_words} words "
            f"(this is a target, not a hard limit).\n"
            f"Output language: {lang_name}.\n"
            f"Mode: {mode}.\n\n"
            f"Transcript:\n{self._prepare_transcript(transcript_text)}\n\n"
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

    async def generate_seo(
        self,
        script: str,
        *,
        original_title: Optional[str] = None,
        language: str = "en",
    ) -> SEOResult:
        system = (
            "You are a YouTube SEO specialist. Generate accurate, non-fabricated "
            "metadata. Titles under 60 characters. Output valid JSON only."
        )
        user = (
            f"Language: {language}\n"
            f"Original title hint: {original_title or 'N/A'}\n\n"
            f"Script / content:\n{script[:8000]}\n\n"
            "Return JSON: titles (array of 3 strings), description (string with synopsis, "
            "chapters if any, keywords, disclaimer), tags (array), hashtags (array), "
            "chapters (array of {title, start_seconds})."
        )
        raw = await self._complete(system, user)
        data = self._extract_json(raw)
        try:
            seo = SEOResult.model_validate(data)
        except PydanticValidationError:
            repair = await self._complete(
                system, f"Fix into valid SEO JSON:\n{raw[:4000]}"
            )
            seo = SEOResult.model_validate(self._extract_json(repair))

        seo.titles = [t[:60].strip() for t in seo.titles if t.strip()][:3]
        if not seo.titles:
            raise LLMError("No valid titles generated", retryable=True)
        return seo

    def _model_candidates(self) -> List[str]:
        preferred = (getattr(self.settings, "groq_model", None) or "").strip()
        seen: set[str] = set()
        out: List[str] = []
        for m in ([preferred] if preferred else []) + list(GROQ_FREE_MODELS):
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    async def _complete(self, system: str, user: str) -> str:
        provider = (self.settings.llm_provider or "groq").lower()

        if provider == "groq" or (
            provider not in ("openai", "gemini") and self.settings.groq_api_key
        ):
            if self.settings.groq_api_key:
                return await self._groq_complete_with_fallback(system, user)

        if provider == "openai" and self.settings.openai_api_key:
            return await retry_async(
                self._openai_complete, max_attempts=2, system=system, user=user
            )
        if provider == "gemini" and self.settings.gemini_api_key:
            return await retry_async(
                self._gemini_complete, max_attempts=2, system=system, user=user
            )

        if self.settings.groq_api_key:
            return await self._groq_complete_with_fallback(system, user)
        if self.settings.openai_api_key:
            return await retry_async(
                self._openai_complete, max_attempts=2, system=system, user=user
            )
        if self.settings.gemini_api_key:
            return await retry_async(
                self._gemini_complete, max_attempts=2, system=system, user=user
            )
        raise LLMError(
            "No LLM API key configured (set GROQ_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY)",
            retryable=False,
        )

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
                msg = str(exc).lower()
                if any(
                    x in msg
                    for x in (
                        "404",
                        "model_not_found",
                        "does not exist",
                        "do not have access",
                        "not available",
                        "invalid_request",
                    )
                ):
                    logger.warning("Groq model %s unavailable, trying next: %s", model, exc)
                    continue
                if "rate limited" in msg or "server error" in msg or "429" in msg or "503" in msg:
                    logger.warning("Groq model %s temporary error, trying next: %s", model, exc)
                    continue
                logger.warning("Groq model %s failed, trying next: %s", model, exc)
                continue
        raise LLMError(
            f"All Groq models failed. Last error: {last_err}",
            retryable=True,
        ) from last_err

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
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code == 429:
            raise LLMError(f"Groq rate limited ({model})", retryable=True)
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
        async with httpx.AsyncClient(timeout=120.0) as client:
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
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Malformed Gemini response", retryable=True) from exc

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
        async with httpx.AsyncClient(timeout=120.0) as client:
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
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Malformed OpenAI response", retryable=True) from exc

    @staticmethod
    def _prepare_transcript(text: str, max_chars: int = 60000) -> str:
        """Keep long transcripts representative without exploding LLM context."""
        text = text.strip()
        if len(text) <= max_chars:
            return text
        third = max_chars // 3
        middle_start = max(0, (len(text) - third) // 2)
        return (
            text[:third]
            + "\n\n[...middle of transcript omitted for context...]\n\n"
            + text[middle_start:middle_start + third]
            + "\n\n[...later transcript omitted for context...]\n\n"
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
