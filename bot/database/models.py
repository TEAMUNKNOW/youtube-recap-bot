"""SQLAlchemy async models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class TaskStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RECOVERING = "RECOVERING"
    DOWNLOADING = "DOWNLOADING"
    PROBING = "PROBING"
    TRANSCRIBING = "TRANSCRIBING"
    SCRIPTING = "SCRIPTING"
    TTS = "TTS"
    AUDIO_PROCESSING = "AUDIO_PROCESSING"
    RENDERING = "RENDERING"
    SUBTITLING = "SUBTITLING"
    THUMBNAIL = "THUMBNAIL"
    SEO = "SEO"
    VALIDATING = "VALIDATING"
    UPLOADING = "UPLOADING"
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SourceType(str, enum.Enum):
    YOUTUBE = "YOUTUBE"
    DIRECT_URL = "DIRECT_URL"
    TELEGRAM_UPLOAD = "TELEGRAM_UPLOAD"


class PipelineMode(str, enum.Enum):
    AI_RECAP = "AI_RECAP"
    TRANSFORMATIVE = "TRANSFORMATIVE"


class PartitionMode(str, enum.Enum):
    FULL = "FULL"
    SPLIT = "SPLIT"


class ExportTarget(str, enum.Enum):
    TELEGRAM = "TELEGRAM"
    YOUTUBE = "YOUTUBE"
    BOTH = "BOTH"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tasks: Mapped[list["Task"]] = relationship(back_populates="user")


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_file: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mode: Mapped[Optional[PipelineMode]] = mapped_column(Enum(PipelineMode), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tts_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tts_voice: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    partition_mode: Mapped[Optional[PartitionMode]] = mapped_column(Enum(PartitionMode), nullable=True)
    export_target: Mapped[Optional[ExportTarget]] = mapped_column(Enum(ExportTarget), nullable=True)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.QUEUED, index=True)
    current_stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    input_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    workspace_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_video_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ui_state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    rights_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    user: Mapped["User"] = relationship(back_populates="tasks")


class Quota(Base):
    __tablename__ = "quota"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    youtube_units: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    upload_count: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
