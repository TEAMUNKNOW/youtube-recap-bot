"""Async SQLAlchemy session management with WAL mode."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import Settings, get_settings
from bot.database.models import Base

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _configure_sqlite(dbapi_conn: object, _connection_record: object) -> None:
    """Enable WAL and sensible pragmas for SQLite."""
    cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


async def init_db(settings: Optional[Settings] = None) -> None:
    """Create engine, enable WAL, create tables."""
    global _engine, _session_factory
    settings = settings or get_settings()

    _engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
    )

    if settings.database_url.startswith("sqlite"):
        event.listen(_engine.sync_engine, "connect", _configure_sqlite)

    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session_dependency() -> AsyncGenerator[AsyncSession, None]:
    async with get_session() as session:
        yield session
