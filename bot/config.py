"""Application configuration using Pydantic v2 Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Set

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(..., description="Telegram Bot API token")
    api_id: int = Field(..., description="Telegram API ID")
    api_hash: str = Field(..., description="Telegram API hash")
    user_session_string: Optional[str] = Field(default=None, description="Optional Pyrogram user session string")

    owner_ids: str = Field(default="", description="Comma-separated owner Telegram IDs")
    admin_ids: str = Field(default="", description="Comma-separated admin Telegram IDs")

    database_url: str = Field(default="sqlite+aiosqlite:///./data/bot.db", description="SQLAlchemy async database URL")
    workspace_root: Path = Field(default=Path("/tmp/youtube_recap"), description="Root directory for task workspaces")

    max_concurrent_tasks: int = Field(default=2, ge=1, le=16)
    max_video_duration_minutes: int = Field(default=240, ge=1)
    max_input_size_gb: float = Field(default=10.0, gt=0)
    max_output_size_gb: float = Field(default=8.0, gt=0)
    task_timeout_seconds: int = Field(default=7200, ge=60)
    ffmpeg_timeout_seconds: int = Field(default=7200, ge=60)
    stage_timeout_seconds: int = Field(default=3600, ge=30)
    stale_task_threshold_seconds: int = Field(default=900, ge=60)
    max_retries: int = Field(default=3, ge=0)
    disk_space_min_gb: float = Field(default=5.0, gt=0)

    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    elevenlabs_api_key: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None

    tts_provider: str = Field(default="edge")
    # Ordered comma-separated failover providers used when the primary TTS fails.
    tts_fallback_providers: str = Field(default="local,openai,elevenlabs")
    hindi_voice: str = Field(default="hi-IN-MadhurNeural")
    english_voice: str = Field(default="en-US-ChristopherNeural")
    bengali_voice: str = Field(default="bn-IN-TanishaaNeural")
    spanish_voice: str = Field(default="es-ES-AlvaroNeural")
    openai_tts_model: str = Field(default="tts-1-hd")
    openai_tts_voice: str = Field(default="alloy")
    elevenlabs_model: str = Field(default="eleven_multilingual_v2")

    gemini_model: str = Field(default="gemini-3.8-flash")
    groq_model: str = Field(default="openai/gpt-oss-120b")
    openai_model: str = Field(default="gpt-4o-mini")
    llm_provider: str = Field(default="groq")

    youtube_client_secrets: Optional[Path] = Field(default=None)
    youtube_token_file: Path = Field(default=Path("./data/youtube_token.json"))
    youtube_quota_daily_budget: int = Field(default=10000)
    youtube_quota_safety_percent: int = Field(default=80, ge=1, le=100)

    target_timezone: str = Field(default="Asia/Kolkata")
    peak_start: str = Field(default="18:00")
    peak_end: str = Field(default="21:00")


    # Autonomous Shorts Factory
    shorts_enabled: bool = Field(default=True)
    shorts_default_duration: int = Field(default=60, ge=30, le=180)
    shorts_default_speed: float = Field(default=1.5, ge=0.75, le=2.0)
    shorts_default_daily_limit: int = Field(default=3, ge=1, le=5)
    shorts_max_daily_limit: int = Field(default=5, ge=1, le=5)
    shorts_output_width: int = Field(default=1080, ge=320)
    shorts_output_height: int = Field(default=1920, ge=320)
    shorts_max_parts: int = Field(default=10000, ge=1, le=10000)
    shorts_retention_days: int = Field(default=1, ge=0)
    shorts_auto_best_time: bool = True
    shorts_default_timezone: str = "Asia/Kolkata"
    shorts_boundary_tolerance_seconds: float = Field(default=5.0, ge=0, le=30)
    shorts_render_concurrency: int = Field(default=1, ge=1, le=4)
    shorts_upload_concurrency: int = Field(default=1, ge=1, le=2)
    youtube_client_id: Optional[str] = None
    youtube_client_secret: Optional[str] = None
    youtube_oauth_redirect_uri: Optional[str] = None
    shorts_oauth_encryption_key: Optional[str] = None
    shorts_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    shorts_oauth_bind_host: str = "0.0.0.0"
    shorts_oauth_bind_port: int = Field(default=8081, ge=1, le=65535)

    proxy_url: Optional[str] = None
    cookie_file: Optional[Path] = None
    po_token: Optional[str] = None
    ytdlp_retries: int = Field(default=3, ge=0)
    ytdlp_socket_timeout: int = Field(default=30, ge=5)

    bgm_directory: Path = Field(default=Path("./assets/bgm"))
    font_directory: Path = Field(default=Path("./assets/fonts"))
    default_font: str = Field(default="DejaVuSans.ttf")

    av_sync_tolerance_seconds: float = Field(default=8.0, gt=0)
    words_per_minute: int = Field(default=135, ge=80, le=200)

    export_resolution: str = Field(default="1280x720")
    export_fps: int = Field(default=30, ge=15, le=60)
    export_crf: int = Field(default=20, ge=0, le=51)
    export_preset: str = Field(default="veryfast")
    export_audio_bitrate: str = Field(default="128k")
    bgm_volume: float = Field(default=0.12, ge=0.0, le=1.0)
    narration_volume: float = Field(default=1.0, ge=0.0, le=2.0)

    preserve_debug_package: bool = Field(default=True)
    orphan_cleanup_interval_hours: int = Field(default=6, ge=1)

    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True)

    @field_validator("workspace_root", "bgm_directory", "font_directory", mode="before")
    @classmethod
    def _ensure_path(cls, v: object) -> Path:
        return Path(v) if not isinstance(v, Path) else v

    @field_validator("youtube_client_secrets", "youtube_token_file", "cookie_file", mode="before")
    @classmethod
    def _optional_path(cls, v: object) -> Optional[Path]:
        if v is None or v == "" or v == "None":
            return None
        return Path(v) if not isinstance(v, Path) else v

    @model_validator(mode="after")
    def _validate_required(self) -> "Settings":
        if not self.bot_token:
            raise ValueError("BOT_TOKEN is required")
        if not self.api_id or not self.api_hash:
            raise ValueError("API_ID and API_HASH are required")
        return self

    def owner_id_set(self) -> Set[int]:
        return self._parse_ids(self.owner_ids)

    def admin_id_set(self) -> Set[int]:
        return self.owner_id_set() | self._parse_ids(self.admin_ids)

    @staticmethod
    def _parse_ids(raw: str) -> Set[int]:
        if not raw or not raw.strip():
            return set()
        result: Set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                result.add(int(part))
        return result

    def is_authorized(self, user_id: int) -> bool:
        return user_id in self.admin_id_set()

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_id_set()

    def max_input_bytes(self) -> int:
        return int(self.max_input_size_gb * 1024**3)

    def max_output_bytes(self) -> int:
        return int(self.max_output_size_gb * 1024**3)

    def ensure_directories(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.bgm_directory.mkdir(parents=True, exist_ok=True)
        self.font_directory.mkdir(parents=True, exist_ok=True)
        Path("./data").mkdir(parents=True, exist_ok=True)
        if self.youtube_token_file:
            self.youtube_token_file.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
