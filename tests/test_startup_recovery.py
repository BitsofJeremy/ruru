"""Startup recovery for orphaned jobs.

The JobRunner is in-process: a freshly started ruru process owns no workers,
so any job row still 'queued' or 'running' at boot is a leftover from a
previous process that died mid-job (restart, crash, systemd kill). Left
unswept, such rows hold the Quick TTS form lock forever — tts_page counts
status IN ('queued', 'running') with no staleness check (regression:
a music job orphaned on 2026-09-27 kept the form disabled for weeks).
"""
import asyncio

import pytest


def _add_job(status: str, scope: str = "narrate") -> int:
    """Insert one job row via the ORM and return its id."""
    from models.db import Job, get_sync_session

    with next(get_sync_session()) as session:
        job = Job(
            title=f"orphan {status} {scope}",
            voice_id=None,
            status=status,
            source_path="/test/path",
            scope=scope,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


@pytest.fixture(autouse=True)
def _hermetic_db(tmp_db, monkeypatch):
    """Pin app.DB_PATH to the tmp DB so the lifespan test can NEVER touch
    the real ruru.db, even when this file runs before any client-fixture
    test has set RURU_WORK_DIR (app.py imports DB_PATH from config at
    module import time)."""
    import app

    monkeypatch.setattr(app, "DB_PATH", tmp_db)


class TestRecoverOrphanedJobs:
    def test_sweeps_queued_and_running_to_failed(self, seeded_db):
        from models.db import Job, get_sync_session

        import app

        running_id = _add_job("running", scope="music")
        queued_id = _add_job("queued")
        completed_id = _add_job("completed")
        failed_id = _add_job("failed")

        swept = asyncio.run(app.recover_orphaned_jobs())

        assert swept == 2
        with next(get_sync_session()) as session:
            running = session.get(Job, running_id)
            assert running.status == "failed"
            assert "restart" in (running.error_message or "").lower()
            assert session.get(Job, queued_id).status == "failed"
            # Terminal rows are untouched
            assert session.get(Job, completed_id).status == "completed"
            assert session.get(Job, failed_id).status == "failed"

    def test_no_orphans_is_a_noop(self, seeded_db):
        import app

        assert asyncio.run(app.recover_orphaned_jobs()) == 0

    def test_lifespan_runs_the_sweep(self, tmp_db, seeded_db, monkeypatch):
        """app_lifespan must call the sweep before the runner starts."""
        import app

        _add_job("running", scope="music")

        calls = []
        real_sweep = app.recover_orphaned_jobs

        async def spy():
            calls.append("swept")
            return await real_sweep()

        monkeypatch.setattr(app, "recover_orphaned_jobs", spy)

        from unittest.mock import patch

        with patch("app.get_runner") as mock_get_runner, patch(
            "config.ensure_dirs"
        ):
            mock_get_runner.return_value = MagicMock_runner()
            from fastapi.testclient import TestClient

            with TestClient(app.app):
                pass  # entering the client runs the lifespan

        assert calls, "recover_orphaned_jobs was not called during startup"

        from models.db import Job, get_sync_session

        with next(get_sync_session()) as session:
            orphans = (
                session.query(Job).filter(Job.status.in_(("queued", "running"))).count()
            )
        assert orphans == 0


def MagicMock_runner():
    from unittest.mock import MagicMock

    runner = MagicMock()
    return runner
