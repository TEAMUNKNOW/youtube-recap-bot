"""Typed exception hierarchy for the bot."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    INPUT_ERROR = "INPUT_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
    TRANSCRIPTION_ERROR = "TRANSCRIPTION_ERROR"
    LLM_ERROR = "LLM_ERROR"
    TTS_ERROR = "TTS_ERROR"
    FFMPEG_ERROR = "FFMPEG_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    YOUTUBE_ERROR = "YOUTUBE_ERROR"
    QUOTA_ERROR = "QUOTA_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    CANCELLED = "CANCELLED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class BotError(Exception):
    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.UNKNOWN_ERROR, retryable: bool = False, details: Optional[dict[str, Any]] = None, user_message: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.details = details or {}
        self.user_message = user_message or self._default_user_message()

    def _default_user_message(self) -> str:
        mapping = {
            ErrorCode.INPUT_ERROR: "Invalid input. Please check the URL or file and try again.",
            ErrorCode.AUTH_ERROR: "You are not authorized to use this bot.",
            ErrorCode.DOWNLOAD_ERROR: "Failed to download media. The source may be unavailable.",
            ErrorCode.TRANSCRIPTION_ERROR: "Transcription failed. Please try again later.",
            ErrorCode.LLM_ERROR: "Script generation failed. Please try again later.",
            ErrorCode.TTS_ERROR: "Voice synthesis failed. Please try a different provider.",
            ErrorCode.FFMPEG_ERROR: "Video processing failed. The media may be unsupported.",
            ErrorCode.STORAGE_ERROR: "Storage error. Please try again later.",
            ErrorCode.YOUTUBE_ERROR: "YouTube upload failed. Please check credentials/quota.",
            ErrorCode.QUOTA_ERROR: "YouTube API quota exceeded. Try again tomorrow.",
            ErrorCode.VALIDATION_ERROR: "Output validation failed. Processing aborted.",
            ErrorCode.TIMEOUT_ERROR: "Operation timed out. Please try again.",
            ErrorCode.CANCELLED: "Task was cancelled.",
            ErrorCode.UNKNOWN_ERROR: "An unexpected error occurred. Please try again.",
        }
        return mapping.get(self.code, "An unexpected error occurred.")


class InputError(BotError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.INPUT_ERROR, retryable=False, **kwargs)

class AuthError(BotError):
    def __init__(self, message: str = "Unauthorized", **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.AUTH_ERROR, retryable=False, **kwargs)

class DownloadError(BotError):
    def __init__(self, message: str, *, retryable: bool = True, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.DOWNLOAD_ERROR, retryable=retryable, **kwargs)

class TranscriptionError(BotError):
    def __init__(self, message: str, *, retryable: bool = True, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.TRANSCRIPTION_ERROR, retryable=retryable, **kwargs)

class LLMError(BotError):
    def __init__(self, message: str, *, retryable: bool = True, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.LLM_ERROR, retryable=retryable, **kwargs)

class TTSError(BotError):
    def __init__(self, message: str, *, retryable: bool = True, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.TTS_ERROR, retryable=retryable, **kwargs)

class FFmpegError(BotError):
    def __init__(self, message: str, *, retryable: bool = False, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.FFMPEG_ERROR, retryable=retryable, **kwargs)

class StorageError(BotError):
    def __init__(self, message: str, *, retryable: bool = True, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.STORAGE_ERROR, retryable=retryable, **kwargs)

class YouTubeError(BotError):
    def __init__(self, message: str, *, retryable: bool = True, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.YOUTUBE_ERROR, retryable=retryable, **kwargs)

class QuotaError(BotError):
    def __init__(self, message: str = "Quota exceeded", **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.QUOTA_ERROR, retryable=False, **kwargs)

class ValidationError(BotError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.VALIDATION_ERROR, retryable=False, **kwargs)

class TimeoutError_(BotError):
    def __init__(self, message: str = "Operation timed out", **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.TIMEOUT_ERROR, retryable=True, **kwargs)

class CancelledError(BotError):
    def __init__(self, message: str = "Task cancelled", **kwargs: Any) -> None:
        super().__init__(message, code=ErrorCode.CANCELLED, retryable=False, **kwargs)
