"""Tests for models/db.py — ORM models, init_db, and schema creation.

The old TestSchema class pattern-matched a `SCHEMA` string constant that the
SQLAlchemy ORM refactor removed; it is replaced here by inspector-based column
checks and ORM-default assertions, which test the real schema the app builds.
"""
import pytest


class TestInitDb:
    @pytest.mark.asyncio
    async def test_creates_all_tables(self, tmp_db):
        from models.db import init_db
        await init_db(tmp_db)

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cur.fetchall()}
        conn.close()

        assert "voices" in tables
        assert "jobs" in tables
        assert "chapters" in tables
        assert "tts_clips" in tables

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self, tmp_db):
        from models.db import init_db
        await init_db(tmp_db)

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        cur = conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
        conn.close()
        assert mode.upper() == "WAL"


class TestOrmModels:
    """Verify the ORM models declare the columns the routes/handlers rely on."""

    @pytest.mark.asyncio
    async def test_voices_columns(self, tmp_db):
        from models.db import init_db
        from sqlalchemy import create_engine, inspect

        await init_db(tmp_db)
        engine = create_engine(f"sqlite:///{tmp_db}")
        try:
            cols = {c["name"] for c in inspect(engine).get_columns("voices")}
        finally:
            engine.dispose()

        # Columns required by app routes + clone routing (BUG 1 reads ref_audio_path).
        assert {
            "id", "name", "slug", "voice_type", "description",
            "ref_audio_path", "ref_text", "speaking_style", "emotional_range",
        }.issubset(cols)

    @pytest.mark.asyncio
    async def test_jobs_columns(self, tmp_db):
        from models.db import init_db
        from sqlalchemy import create_engine, inspect

        await init_db(tmp_db)
        engine = create_engine(f"sqlite:///{tmp_db}")
        try:
            cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
        finally:
            engine.dispose()

        assert {
            "id", "title", "voice_id", "status", "source_path", "output_path",
            "audio_format", "progress", "author", "cover_path", "scope",
        }.issubset(cols)

    @pytest.mark.asyncio
    async def test_job_orm_defaults_apply(self, tmp_db):
        """Inserting a Job via ORM populates Python-side defaults so NOT NULL
        columns are satisfied — the exact bug the raw-SQL seed fixture hit."""
        from models.db import init_db, Job, get_sync_session

        await init_db(tmp_db)
        with next(get_sync_session()) as session:
            job = Job(title="Defaults", status="queued", source_path="/x")
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.audio_format == "mp3"
            assert job.scope == "document"
            assert job.author == ""
            assert job.cover_path == ""
            assert job.progress == 0
            assert job.needs_rebuild is False

        @pytest.mark.asyncio
        async def test_voice_orm_defaults_apply(self, tmp_db):
            """Voice Python-side defaults (emotional_range, speaking_style, etc.)
            persist to the NOT NULL columns when inserted via the ORM.
    
            SQLAlchemy applies ``default=`` at INSERT (flush), not at construction,
            so the values are checked after a commit + refresh against the DB.
            """
            from models.db import init_db, Voice, get_sync_session
    
            await init_db(tmp_db)
            with next(get_sync_session()) as session:
                v = Voice(name="N", slug="n", voice_type="cloned")
                session.add(v)
                session.commit()
                session.refresh(v)
                assert v.emotional_range == "[]"
                assert v.description == ""
                assert v.speaking_style == ""
