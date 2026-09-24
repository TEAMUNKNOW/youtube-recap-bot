"""TTS package."""

from core.tts.base import BaseTTSProvider, TTSResult, VoiceInfo
from core.tts.factory import create_tts_provider

__all__ = ["BaseTTSProvider", "TTSResult", "VoiceInfo", "create_tts_provider"]
