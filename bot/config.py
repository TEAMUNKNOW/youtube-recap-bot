"""Application configuration using Pydantic v2 Settings."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional, Set

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Telegram / Pyrogram
    api_id: int
    api_hash: str
    bot_token: str
    user_session_string: Optional[str] = None
    owner_ids: List[int] = Field(default_factory=list)
    admin_ids: List[int] = Field(default_factory=list)

    # Workspace
    workspace_root: str = "./data"
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    log_level: str = "INFO"
    orphan_cleanup_interval_hours: float = 6.0

    # Limits
    max_video_duration_minutes: int = Field(default=240, ge=1)
    max_concurrent_tasks: int = Field(default=2, ge=1, le=8)
    words_per_minute: int = Field(default=130, ge=90, le=180)
    # 1.0 = output narration/video same length as source
    recap_duration_ratio: float = Field(default=1.0, ge=0.5, le=1.2)
    av_sync_tolerance_seconds: float = Field(default=3.0, ge=0.5)

    # LLM
    llm_provider: str = "groq"
    groq_api_key: Optional[str] = None
    groq_model: str = "qwen/qwen3.8-27b"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.0-flash"

    # TTS
    tts_provider: str = "edge"
    edge_tts_voice: str = "en-US-ChristopherNeural"
    openai_tts_voice: str = "alloy"
    elevenlabs_api_key: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None

    # Proxy
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None

    # YouTube OAuth / Shorts
    shorts_enabled: bool = False
    youtube_client_id: Optional[str] = None
    youtube_client_secret: Optional[str] = None
    youtube_client_secrets: Optional[str] = None
    youtube_oauth_redirect_uri: Optional[str] = None
    shorts_oauth_encryption_key: Optional[str] = None
    shorts_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    shorts_oauth_bind_host: str = "0.0.0.0"
    shorts_oauth_bind_port: int = Field(default=8080, ge=1, le=65535)
    shorts_default_duration: int = Field(default=60, ge=30, le=180)

    # Export
    export_resolution: str = Field(default="1280x720")
    export_fps: int = Field(default=30, ge=15, le=60)
    export_crf: int = Field(default=20, ge=0, le=51)
    export_preset: str = Field(default="veryfast")
    export_audio_bitrate: str = Field(default="192k")
    narration_volume: float = Field(default=1.0, ge=0.0, le=2.0)
    bgm_volume: float = Field(default=0.15, ge=0.0, le=1.0)

    @field_validator("owner_ids", "admin_ids", mode="before")
    @classmethod
    def _parse_id_list(cls, v: Any) -> List[int]:
        if v is None or v == "":
            return []
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, int):
            return [v]
        s = str(v).strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                return [int(x) for x in json.loads(s)]
            except Exception:
                pass
        return [int(x.strip()) for x in s.replace(";", ",").split(",") if x.strip()]

    def is_owner(self, user_id: int) -> bool:
        return user_id in set(self.owner_ids)

    def is_authorized(self, user_id: int) -> bool:
        allowed: Set[int] = set(self.owner_ids) | set(self.admin_ids)
        if not allowed:
            return True
        return user_id in allowed

    def ensure_directories(self) -> None:
        root = Path(self.workspace_root)
        for sub in ("", "sessions", "tmp", "outputs", "uploads"):
            (root / sub if sub else root).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
