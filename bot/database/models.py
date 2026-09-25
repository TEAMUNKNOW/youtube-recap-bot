"""SQLAlchemy async models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class TaskStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RECOVERING = "RECOVERING"
    DOWNLOADING = "DOWNLOADING"
    PROBING = "PROBING"
    VALIDATING = "VALIDATING"
    EXTRACTING_AUDIO = "EXTRACTING_AUDIO"
    TRANSCRIBING = "TRANSCRIBING"
    SCRIPTING = "SCRIPTING"
    TTS = "TTS"
    AUDIO_PROCESSING = "AUDIO_PROCESSING"
    MUTE_VIDEO = "MUTE_VIDEO"
    MIX_AUDIO = "MIX_AUDIO"
    RENDERING = "RENDERING"
    SUBTITLING = "SUBTITLING"
    THUMBNAIL = "THUMBNAIL"
    SEO = "SEO"
    VALIDATING_OUTPUT = "VALIDATING_OUTPUT"
    UPLOADING = "UPLOADING"
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TaskState = TaskStatus


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
    voice: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tts_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tts_voice: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    partition_mode: Mapped[Optional[PartitionMode]] = mapped_column(Enum(PartitionMode), nullable=True)
    export_target: Mapped[Optional[ExportTarget]] = mapped_column(Enum(ExportTarget), nullable=True)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.QUEUED, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    input_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
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
    current_stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ui_state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    rights_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    user: Mapped["User"] = relationship(back_populates="tasks")



class YouTubeAccount(Base):
    __tablename__ = "youtube_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel_title: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_youtube_accounts_user_channel", "user_id", "channel_id", unique=True),)

class ShortsProjectStatus(str, enum.Enum):
    CREATED="CREATED"; ANALYZING="ANALYZING"; MANIFEST_READY="MANIFEST_READY"; RUNNING="RUNNING"; PAUSED="PAUSED"; SCHEDULED="SCHEDULED"; COMPLETED="COMPLETED"; FAILED="FAILED"; STOPPED="STOPPED"
class ShortsSelectionMode(str, enum.Enum):
    CONTINUOUS="CONTINUOUS"; HIGHLIGHT="HIGHLIGHT"
class ShortsClipStatus(str, enum.Enum):
    PLANNED="PLANNED"; RENDERING="RENDERING"; RENDERED="RENDERED"; UPLOADING="UPLOADING"; UPLOADED="UPLOADED"; SCHEDULED="SCHEDULED"; PUBLISHED="PUBLISHED"; FAILED="FAILED"; DELETED="DELETED"

class ShortsProject(Base):
    __tablename__="shorts_projects"
    id: Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    user_id: Mapped[int]=mapped_column(ForeignKey("users.id"),nullable=False,index=True)
    source_file: Mapped[Optional[str]]=mapped_column(Text); source_url: Mapped[Optional[str]]=mapped_column(Text)
    source_duration: Mapped[float]=mapped_column(Float,default=0.0); clip_duration: Mapped[int]=mapped_column(Integer,default=60)
    playback_speed: Mapped[float]=mapped_column(Float,default=1.5); output_format: Mapped[str]=mapped_column(String(32),default="mp4")
    total_parts: Mapped[int]=mapped_column(Integer,default=0); processed_parts: Mapped[int]=mapped_column(Integer,default=0)
    status: Mapped[ShortsProjectStatus]=mapped_column(Enum(ShortsProjectStatus),default=ShortsProjectStatus.CREATED,index=True)
    selection_mode: Mapped[ShortsSelectionMode]=mapped_column(Enum(ShortsSelectionMode),default=ShortsSelectionMode.CONTINUOUS)
    daily_limit: Mapped[int]=mapped_column(Integer,default=3); timezone: Mapped[str]=mapped_column(String(64),default="Asia/Kolkata")
    schedule_mode: Mapped[str]=mapped_column(String(32),default="fallback"); retention_days: Mapped[int]=mapped_column(Integer,default=1)
    rights_acknowledged: Mapped[bool]=mapped_column(Boolean,default=False)
    keep_original: Mapped[bool]=mapped_column(Boolean,default=True); keep_rendered: Mapped[bool]=mapped_column(Boolean,default=False)
    chat_id: Mapped[Optional[int]]=mapped_column(Integer); status_message_id: Mapped[Optional[int]]=mapped_column(Integer)
    workspace_path: Mapped[Optional[str]]=mapped_column(Text); error: Mapped[Optional[str]]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),index=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now())

class ShortClip(Base):
    __tablename__="short_clips"
    id: Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    project_id: Mapped[int]=mapped_column(ForeignKey("shorts_projects.id"),nullable=False,index=True)
    part_number: Mapped[int]=mapped_column(Integer,nullable=False); source_start: Mapped[float]=mapped_column(Float,nullable=False); source_end: Mapped[float]=mapped_column(Float,nullable=False)
    target_duration: Mapped[float]=mapped_column(Float,nullable=False); actual_duration: Mapped[Optional[float]]=mapped_column(Float)
    selection_mode: Mapped[ShortsSelectionMode]=mapped_column(Enum(ShortsSelectionMode),nullable=False)
    selection_reason: Mapped[Optional[str]]=mapped_column(Text); overlap_allowed: Mapped[bool]=mapped_column(Boolean,default=False)
    chapter: Mapped[Optional[str]]=mapped_column(String(255)); title: Mapped[Optional[str]]=mapped_column(String(100))
    description: Mapped[Optional[str]]=mapped_column(Text); tags: Mapped[list[str]]=mapped_column(JSON,default=list); hashtags: Mapped[list[str]]=mapped_column(JSON,default=list)
    local_path: Mapped[Optional[str]]=mapped_column(Text); thumbnail_path: Mapped[Optional[str]]=mapped_column(Text)
    status: Mapped[ShortsClipStatus]=mapped_column(Enum(ShortsClipStatus),default=ShortsClipStatus.PLANNED,index=True)
    youtube_video_id: Mapped[Optional[str]]=mapped_column(String(64),index=True); scheduled_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),index=True)
    published_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True)); error: Mapped[Optional[str]]=mapped_column(Text); attempt: Mapped[int]=mapped_column(Integer,default=0)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now()); updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now())
    __table_args__=(Index("ix_short_clips_project_part","project_id","part_number",unique=True),Index("ix_short_clips_status_schedule","status","scheduled_at"))

class ShortsSchedule(Base):
    __tablename__="shorts_schedules"
    id: Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True); project_id: Mapped[int]=mapped_column(ForeignKey("shorts_projects.id"),nullable=False,index=True)
    daily_limit: Mapped[int]=mapped_column(Integer,default=3); auto_best_time: Mapped[bool]=mapped_column(Boolean,default=True); fallback_times: Mapped[list[str]]=mapped_column(JSON,default=list)
    timezone: Mapped[str]=mapped_column(String(64),default="Asia/Kolkata"); reason: Mapped[Optional[str]]=mapped_column(Text); enabled: Mapped[bool]=mapped_column(Boolean,default=True)

class ShortsSettings(Base):
    __tablename__="shorts_settings"
    id: Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True); user_id: Mapped[int]=mapped_column(ForeignKey("users.id"),nullable=False,index=True)
    default_duration: Mapped[int]=mapped_column(Integer,default=60); default_speed: Mapped[float]=mapped_column(Float,default=1.5); daily_limit: Mapped[int]=mapped_column(Integer,default=3)
    resolution: Mapped[str]=mapped_column(String(16),default="1080x1920"); fps: Mapped[str]=mapped_column(String(16),default="source"); bitrate: Mapped[str]=mapped_column(String(32),default="auto")
    audio_bitrate: Mapped[str]=mapped_column(String(16),default="128k"); crop_mode: Mapped[str]=mapped_column(String(32),default="smart"); thumbnail_mode: Mapped[str]=mapped_column(String(32),default="auto")
    timezone: Mapped[str]=mapped_column(String(64),default="Asia/Kolkata"); retention_days: Mapped[int]=mapped_column(Integer,default=1); auto_best_time: Mapped[bool]=mapped_column(Boolean,default=True)
    __table_args__=(Index("ix_shorts_settings_user","user_id",unique=True),)

class ShortsOAuthState(Base):
    __tablename__="shorts_oauth_states"
    id: Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True); state: Mapped[str]=mapped_column(String(128),unique=True,index=True); user_id: Mapped[int]=mapped_column(ForeignKey("users.id"),nullable=False,index=True)
    code_verifier: Mapped[Optional[str]]=mapped_column(Text); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),index=True); expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,index=True)

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
