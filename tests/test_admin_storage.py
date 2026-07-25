"""Tests for storage admin routes."""
import pytest


class TestAdminStoragePage:
    def test_page_loads_with_job_counts(self, client, seeded_db):
        response = client.get("/admin/storage")
        assert response.status_code == 200
        assert b"Job History" in response.content


class TestClearJobs:
    def test_clears_completed_job(self, client, seeded_db):
        response = client.post("/api/storage/clear-jobs")
        assert response.status_code == 200
        assert "Cleared 1 job" in response.headers["HX-Trigger"]

        page = client.get("/admin/storage")
        assert b"0 eligible to clear" in page.content

    def test_keeps_queued_and_running_jobs(self, client, seeded_db):
        from models.db import Job, get_sync_session

        with next(get_sync_session()) as session:
            queued = Job(title="Queued Job", voice_id=None, status="queued", source_path="/test/queued")
            running = Job(title="Running Job", voice_id=None, status="running", source_path="/test/running")
            session.add_all([queued, running])
            session.commit()

        response = client.post("/api/storage/clear-jobs")
        assert response.status_code == 200
        assert "Cleared 1 job" in response.headers["HX-Trigger"]

        page = client.get("/admin/storage")
        assert b"2 job(s) total" in page.content
        assert b"0 eligible to clear" in page.content

    def test_no_op_when_nothing_clearable(self, client, tmp_work_dir, tmp_db):
        from models.db import init_db
        import asyncio
        asyncio.run(init_db(tmp_db))

        response = client.post("/api/storage/clear-jobs")
        assert response.status_code == 200
        assert "Cleared 0 job" in response.headers["HX-Trigger"]
