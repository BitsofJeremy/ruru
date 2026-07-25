"""Shared pytest fixtures for ruru tests."""
from __future__ import annotations

import os
import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on sys.path
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Shared mock WAV data (silent 0.1s at 24000Hz)
import numpy as np
MOCK_WAV = np.zeros(2400, dtype=np.float32)


@pytest.fixture
def tmp_work_dir(tmp_path):
    """Temporary work directory matching app.py's expected structure."""
    work = tmp_path
    for sub in ("uploads", "chunks", "output", "voices"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    return work


@pytest.fixture
def tmp_db(tmp_work_dir):
    """Path to SQLite DB — matches app.py's DB_PATH = WORK_DIR / 'ruru.db'."""
    return tmp_work_dir / "ruru.db"


@pytest.fixture
async def init_db_fixture(tmp_db):
    """Initialize the test database schema."""
    from models.db import init_db
    await init_db(tmp_db)
    return tmp_db


@pytest.fixture
def mock_tts_engine():
    """Mock TTSEngine that doesn't need GPU."""
    with patch("engine.tts.TTSEngine") as mock_cls:
        instance = MagicMock()
        instance.is_loaded = True
        instance.load = MagicMock()
        instance.unload = MagicMock()
        instance.generate_clone = MagicMock(return_value=(MOCK_WAV, 24000))
        instance.generate_design = MagicMock(return_value=(MOCK_WAV, 24000))
        mock_cls.return_value = instance
        yield mock_cls


@pytest.fixture
def mock_sqlite_conn():
    """Mock sqlite3 connection for job tasks."""
    conn = MagicMock()
    conn.execute = MagicMock()
    conn.commit = MagicMock()
    conn.close = MagicMock()
    return conn


@pytest.fixture
def client(tmp_work_dir, tmp_db, monkeypatch):
    """FastAPI TestClient with real temp database.

    Sets RURU_WORK_DIR so app.py's DB_PATH = tmp_db.
    Patches get_runner.start so GPU-dependent startup doesn't run.
    """
    monkeypatch.setenv("RURU_WORK_DIR", str(tmp_work_dir))
    monkeypatch.setenv("RURU_PORT", "8400")

    with patch("app.get_runner") as mock_get_runner:
        mock_runner = MagicMock()
        mock_runner.start = MagicMock()
        mock_runner.stop = MagicMock()
        mock_get_runner.return_value = mock_runner

        with patch("config.ensure_dirs"):
            import app as app_module

            from fastapi.testclient import TestClient
            tc = TestClient(app_module.app)
            yield tc


@pytest.fixture
def seeded_db(tmp_db, tmp_work_dir):
    """Populate the test database using the app's own init_db then add seed data.

    Seed rows are inserted via the ORM (not raw SQL) so the Job/Voice models'
    Python-side defaults (audio_format, scope, author, cover_path, etc.) apply.
    The old raw-SQL inserts omitted NOT NULL columns with no DB default, so they
    raised IntegrityError, were silently swallowed by a bare `except: pass`,
    left no seed rows, and caused /jobs/1 and /voice/1/edit to 404.
    """
    import asyncio as _asyncio
    from models.db import init_db as _init_db
    _asyncio.run(_init_db(tmp_db))

    from models.db import Voice, Job, get_sync_session
    with next(get_sync_session()) as session:
        voice = Voice(
            name="Test Voice",
            slug="test-voice",
            voice_type="cloned",
            description="A test voice",
        )
        session.add(voice)
        session.commit()
        session.refresh(voice)

        # voice_id=None keeps this seed job independent of the seed voice, so the
        # /voice/1/delete test isn't blocked by a jobs->voices FK constraint
        # (matching how real transcription jobs carry no voice).
        job = Job(
            title="Test Job",
            voice_id=None,
            status="completed",
            source_path="/test/path",
        )
        session.add(job)
        session.commit()

    return tmp_db
