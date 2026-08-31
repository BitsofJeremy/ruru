"""Database models and session management for ruru using SQLAlchemy."""
from __future__ import annotations

import sqlite3
import contextlib
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Generator

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from sqlalchemy import create_engine

class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass

class Voice(Base):
    __tablename__ = "voices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    voice_type: Mapped[str] = mapped_column(String, nullable=False)  # 'designed', 'cloned', 'ephergent'
    sample_audio_path: Mapped[str | None] = mapped_column(Text)
    ref_audio_path: Mapped[str | None] = mapped_column(Text)
    ref_text: Mapped[str | None] = mapped_column(Text)
    speaking_style: Mapped[str] = mapped_column(Text, nullable=False, default="")
    emotional_range: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    image_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

class DeletedVoiceSlug(Base):
    """Tombstone for a voice the user deliberately deleted.

    scan_voice_clones() and seed_voices() re-import anything missing from
    `voices` on every startup and every Refresh click — without a tombstone a
    deleted bundled voice resurrects on the next restart. Creating a voice
    through the UI (or import_voices) clears its tombstone: an explicit add
    always wins over an earlier delete.
    """
    __tablename__ = "deleted_voice_slugs"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    voice_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("voices.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")  # 'queued', 'running', 'completed', 'failed', 'cancelled'
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text)
    audio_format: Mapped[str] = mapped_column(String, nullable=False, default="mp3")
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scope: Mapped[str] = mapped_column(String, nullable=False, default="document")  # 'document', 'chapter'
    scope_index: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    celery_task_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    needs_rebuild: Mapped[bool] = mapped_column(Boolean, default=False)
    author: Mapped[str] = mapped_column(String, nullable=False, default="")
    cover_path: Mapped[str] = mapped_column(String, nullable=False, default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)

class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audio_path: Mapped[str | None] = mapped_column(Text)

class TtsClip(Base):
    __tablename__ = "tts_clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    voice_id: Mapped[int | None] = mapped_column(Integer)
    voice_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    audio_path: Mapped[str] = mapped_column(Text, nullable=False)
    audio_format: Mapped[str] = mapped_column(String, nullable=False, default="wav")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

# Global engines and session factories
# These will be initialized during app startup via a set_db_path function.

_async_engine = None
_sync_engine = None
_async_session_factory = None
_sync_session_factory = None

def set_db_path(db_path: str | Path):
    """Initialize the global engines and session factories."""
    global _async_engine, _sync_engine, _async_session_factory, _sync_session_factory

    path_str = str(db_path)
    _async_engine = create_async_engine(f"sqlite+aiosqlite:///{path_str}", echo=False)
    _sync_engine = create_engine(f"sqlite:///{path_str}", echo=False)

    _async_session_factory = async_sessionmaker(_async_engine, expire_on_commit=False)
    _sync_session_factory = sessionmaker(bind=_sync_engine)

@contextlib.asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async session for FastAPI routes."""
    if _async_session_factory is None:
        raise RuntimeError("Database path not set. Call set_db_path() first.")
    async with _async_session_factory() as session:
        yield session

def get_sync_session() -> Generator[Session, None, None]:
    """Provide a sync session for background jobs."""
    if _sync_session_factory is None:
        raise RuntimeError("Database path not set. Call set_db_path() first.")
    with _sync_session_factory() as session:
        yield session

async def init_db(db_path: str | Path) -> None:
    """Initialize database schema and apply migrations."""
    set_db_path(db_path)

    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))

    # Manual migrations
    import aiosqlite
    async with aiosqlite.connect(str(db_path)) as db:
        migrations = [
            "ALTER TABLE chapters ADD COLUMN audio_path TEXT",
            "ALTER TABLE jobs ADD COLUMN needs_rebuild BOOLEAN DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN author TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN cover_path TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE voices ADD COLUMN image_path TEXT",
            "ALTER TABLE jobs ADD COLUMN progress INTEGER DEFAULT 0",
        ]
        for sql in migrations:
            try:
                await db.execute(sql)
                await db.commit()
            except Exception:
                pass
