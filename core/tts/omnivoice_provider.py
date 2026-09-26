"""OmniVoice TTS — zero-shot cloning + voice design (Hindi adult presets).

Lazy-loads model weights only when this provider is selected.
Best quality on GPU (CUDA/MPS/XPU). CPU allowed only if OMNIVOICE_ALLOW_CPU=true.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import List, Optional

from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo

logger = logging.getLogger(__name__)

HINDI_ADULT_MALE = "male, adult, medium pitch"
HINDI_ADULT_FEMALE = "female, adult, medium pitch"
ENGLISH_ADULT_MALE = "male, adult, medium pitch"
ENGLISH_ADULT_FEMALE = "female, adult, medium pitch"

VOICE_INSTRUCT = {
    "hi-male": HINDI_ADULT_MALE,
    "hi-female": HINDI_ADULT_FEMALE,
    "hi-adult-male": HINDI_ADULT_MALE,
    "hi-adult-female": HINDI_ADULT_FEMALE,
    "en-male": ENGLISH_ADULT_MALE,
    "en-female": ENGLISH_ADULT_FEMALE,
    "male": HINDI_ADULT_MALE,
    "female": HINDI_ADULT_FEMALE,
    "adult-male": HINDI_ADULT_MALE,
    "adult-female": HINDI_ADULT_FEMALE,
}


class OmniVoiceProvider(BaseTTSProvider):
    name = "omnivoice"
    _model = None
    _model_key: Optional[tuple[str, str, str]] = None
    _load_lock = asyncio.Lock()

    def __init__(
        self,
        model_id: str = "k2-fsa/OmniVoice",
        device: str = "auto",
        dtype: str = "float16",
        ref_audio: Optional[Path] = None,
        ref_text: Optional[str] = None,
        instruct: Optional[str] = None,
        num_steps: int = 32,
        allow_cpu: bool = False,
        default_language: str = "hi",
        default_gender: str = "male",
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.dtype = dtype
        self.ref_audio = Path(ref_audio) if ref_audio else None
        self.ref_text = ref_text
        self.instruct = instruct
        self.num_steps = num_steps
        self.allow_cpu = allow_cpu
        self.default_language = (default_language or "hi")[:2].lower()
        self.default_gender = (default_gender or "male").lower()

    def _resolve_device(self) -> str:
        if self.device and self.device.lower() not in ("auto", ""):
            return self.device
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda:0"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps"
            if hasattr(torch, "xpu") and torch.xpu.is_available():
                return "xpu"
        except Exception:
            pass
        return "cpu"

    async def _load_model(self):
        device = self._resolve_device()
        key = (self.model_id, device, self.dtype)
        if self.__class__._model is not None and self.__class__._model_key == key:
            return self.__class__._model

        async with self.__class__._load_lock:
            if self.__class__._model is not None and self.__class__._model_key == key:
                return self.__class__._model
            try:
                import torch
                from omnivoice import OmniVoice
            except ImportError as exc:
                raise TTSError(
                    "OmniVoice not installed. On GPU host run: "
                    "pip install torch torchaudio omnivoice soundfile",
                    retryable=False,
                ) from exc

            if device == "cpu" and not self.allow_cpu:
                raise TTSError(
                    "OmniVoice needs GPU (CUDA/MPS/XPU). "
                    "Set OMNIVOICE_ALLOW_CPU=true only for tiny tests, "
                    "or use TTS_PROVIDER=local/edge on Railway CPU.",
                    retryable=False,
                )

            dtype = getattr(torch, self.dtype, torch.float16)
            if device == "cpu":
                dtype = torch.float32
            logger.info("Loading OmniVoice model=%s device=%s dtype=%s", self.model_id, device, dtype)
            try:
                model = await asyncio.to_thread(
                    OmniVoice.from_pretrained,
                    self.model_id,
                    device_map=device,
                    dtype=dtype,
                )
            except Exception as exc:
                raise TTSError(f"OmniVoice model load failed: {exc}", retryable=True) from exc

            self.__class__._model = model
            self.__class__._model_key = key
            logger.info("OmniVoice model ready")
            return model

    def _pick_instruct(self, voice: Optional[str], language: Optional[str]) -> str:
        if self.instruct:
            return self.instruct
        if voice:
            key = voice.strip().lower()
            if key in VOICE_INSTRUCT:
                return VOICE_INSTRUCT[key]
            if "," in key or "male" in key or "female" in key or "adult" in key:
                return voice.strip()

        lang = (language or self.default_language)[:2].lower()
        gender = self.default_gender
        if voice and "female" in voice.lower():
            gender = "female"
        if voice and "male" in voice.lower():
            gender = "male"

        if lang == "hi":
            return HINDI_ADULT_FEMALE if gender == "female" else HINDI_ADULT_MALE
        if lang in ("en", "es", "bn"):
            return ENGLISH_ADULT_FEMALE if gender == "female" else ENGLISH_ADULT_MALE
        return HINDI_ADULT_MALE

    @staticmethod
    def _clean_text(text: str) -> str:
        t = text.strip()
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t)
        return t

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 500) -> list[str]:
        if len(text) <= max_chars:
            return [text]
        chunks: list[str] = []
        buf = ""
        for part in re.split(r"(?<=[।.!?])\s+", text):
            if not part:
                continue
            if buf and len(buf) + len(part) + 1 > max_chars:
                chunks.append(buf.strip())
                buf = part
            else:
                buf = f"{buf} {part}".strip() if buf else part
        if buf:
            chunks.append(buf.strip())
        return chunks or [text[:max_chars]]

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,
    ) -> TTSResult:
        cleaned = self._clean_text(text)
        if not cleaned:
            raise TTSError("Empty text for TTS", retryable=False)
        if self.ref_audio and not self.ref_audio.exists():
            raise TTSError(f"OmniVoice ref audio missing: {self.ref_audio}", retryable=False)

        model = await self._load_model()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path = output_path.with_suffix(".wav")
        mp3_path = output_path.with_suffix(".mp3")

        instruct = None if self.ref_audio else self._pick_instruct(voice, language)
        lang = (language or self.default_language)[:2].lower()
        chunks = self._chunk_text(cleaned, max_chars=500)
        logger.info(
            "OmniVoice synth lang=%s instruct=%s chunks=%s ref=%s",
            lang, instruct, len(chunks), bool(self.ref_audio),
        )

        import numpy as np

        arrays: list = []
        for i, chunk in enumerate(chunks):
            kwargs: dict = {"text": chunk, "num_step": self.num_steps}
            if lang:
                kwargs["language_id"] = lang
            if self.ref_audio:
                kwargs["ref_audio"] = str(self.ref_audio)
                if self.ref_text:
                    kwargs["ref_text"] = self.ref_text
            elif instruct:
                kwargs["instruct"] = instruct

            try:
                audio = await asyncio.to_thread(model.generate, **kwargs)
            except Exception as exc:
                raise TTSError(f"OmniVoice generate failed chunk {i+1}: {exc}", retryable=True) from exc
            if not audio:
                raise TTSError(f"OmniVoice empty audio chunk {i+1}", retryable=True)
            first = audio[0]
            arr = first.detach().cpu().numpy() if hasattr(first, "detach") else np.asarray(first)
            arrays.append(np.squeeze(arr).astype(np.float32))

        combined = np.concatenate(arrays) if len(arrays) > 1 else arrays[0]
        try:
            import soundfile as sf
            sf.write(str(wav_path), combined, 24000)
        except Exception as exc:
            raise TTSError(f"OmniVoice write failed: {exc}", retryable=True) from exc

        final_path = wav_path
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-b:a", "128k",
                str(mp3_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0 and mp3_path.exists() and mp3_path.stat().st_size > 100:
                final_path = mp3_path
                wav_path.unlink(missing_ok=True)
        except Exception:
            pass

        duration = await self._probe_duration(final_path)
        if duration <= 0:
            raise TTSError("OmniVoice zero-duration audio", retryable=True)

        voice_label = "clone" if self.ref_audio else (instruct or voice or "auto")
        logger.info("OmniVoice ok duration=%.1fs voice=%s", duration, voice_label)
        return TTSResult(
            path=str(final_path),
            duration=duration,
            provider=self.name,
            voice=voice_label,
        )

    async def list_voices(self, language: Optional[str] = None) -> List[VoiceInfo]:
        lang = language or "hi"
        return [
            VoiceInfo(id="hi-adult-male", name="Hindi Adult Male", language=lang, gender="male"),
            VoiceInfo(id="hi-adult-female", name="Hindi Adult Female", language=lang, gender="female"),
            VoiceInfo(id="en-male", name="English Adult Male", language="en", gender="male"),
            VoiceInfo(id="en-female", name="English Adult Female", language="en", gender="female"),
        ]

    @staticmethod
    async def _probe_duration(path: Path) -> float:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
