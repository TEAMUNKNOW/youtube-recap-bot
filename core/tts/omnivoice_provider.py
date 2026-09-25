"""OmniVoice TTS provider with lazy model loading and safe fallback behavior.

OmniVoice is an optional local/GPU TTS backend. It is intentionally lazy-loaded so
normal Railway/CPU deployments do not download model weights at startup.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Optional

from bot.exceptions import TTSError
from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.dtype = dtype
        self.ref_audio = Path(ref_audio) if ref_audio else None
        self.ref_text = ref_text
        self.instruct = instruct
        self.num_steps = num_steps

    async def _load_model(self):
        key = (self.model_id, self._resolve_device(), self.dtype)
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
                    "OmniVoice dependencies are not installed; install omnivoice and PyTorch.",
                    retryable=False,
                ) from exc

            device = key[1]
            if device == "cpu":
                raise TTSError(
                    "OmniVoice is configured for CPU, but this provider requires a GPU backend "
                    "(CUDA/MPS/XPU) for production inference.",
                    retryable=False,
                )

            dtype = getattr(torch, self.dtype, torch.float16)
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
            return model

    def _resolve_device(self) -> str:
        if self.device and self.device.lower() != "auto":
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

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,
    ) -> TTSResult:
        if not text.strip():
            raise TTSError("Empty text for TTS", retryable=False)
        if self.ref_audio and not self.ref_audio.exists():
            raise TTSError(f"OmniVoice reference audio not found: {self.ref_audio}", retryable=False)

        model = await self._load_model()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path = output_path.with_suffix(".wav")

        kwargs = {
            "text": text.strip(),
            "num_step": self.num_steps,
        }
        if language:
            kwargs["language_id"] = language
        if self.ref_audio:
            kwargs["ref_audio"] = str(self.ref_audio)
            if self.ref_text:
                kwargs["ref_text"] = self.ref_text
        elif self.instruct:
            kwargs["instruct"] = self.instruct
        elif voice and voice not in ("auto", "omnivoice"):
            kwargs["instruct"] = voice

        try:
            audio = await asyncio.to_thread(model.generate, **kwargs)
            if not audio:
                raise RuntimeError("OmniVoice returned empty audio")
            first = audio[0]
            try:
                import soundfile as sf
                import numpy as np
                arr = first.detach().cpu().numpy() if hasattr(first, "detach") else np.asarray(first)
                arr = np.squeeze(arr)
                sf.write(str(output_path), arr, 24000)
            except Exception as exc:
                raise RuntimeError(f"failed to write OmniVoice audio: {exc}") from exc
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"OmniVoice synthesis failed: {exc}", retryable=True) from exc

        if not output_path.exists() or output_path.stat().st_size < 100:
            raise TTSError("OmniVoice produced invalid audio", retryable=True)

        duration = await self._probe_duration(output_path)
        if duration <= 0:
            raise TTSError("OmniVoice produced zero-duration audio", retryable=True)
        return TTSResult(
            path=str(output_path),
            duration=duration,
            provider=self.name,
            voice=voice or ("clone" if self.ref_audio else self.instruct or "auto"),
        )

    async def list_voices(self, language: Optional[str] = None) -> List[VoiceInfo]:
        label = "OmniVoice cloned voice" if self.ref_audio else "OmniVoice designed/auto voice"
        return [VoiceInfo(
            id="omnivoice",
            name=label,
            language=language or "multi",
            gender=None,
        )]

    @staticmethod
    async def _probe_duration(path: Path) -> float:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
