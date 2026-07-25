"""Tests for TTS routes."""
import pytest


class TestTTSPage:
    def test_get_tts_page(self, client, seeded_db):
        response = client.get("/tts")
        assert response.status_code == 200


class TestAPITTS:
    def test_tts_missing_text(self, client, seeded_db):
        response = client.post("/api/tts", data={"voice_id": "1"})
        assert response.status_code == 400

    def test_tts_missing_voice_id(self, client, seeded_db):
        # voice_id=99 doesn't exist → 404 Not Found
        response = client.post("/api/tts", data={"text": "Hello world", "voice_id": "99"})
        assert response.status_code == 404


class TestAPITTSClips:
    def test_no_clips_yet(self, client, seeded_db):
        response = client.get("/api/tts/clips")
        assert response.status_code == 200
        assert b"No clips generated yet" in response.content

    def test_lists_completed_quick_clip(self, client, seeded_db):
        from models.db import Job, get_sync_session

        with next(get_sync_session()) as session:
            clip_job = Job(
                title="Hello there",
                voice_id=1,
                status="completed",
                source_path="Hello there",
                scope="quick",
                output_path="/fake/output/tts_42.wav",
            )
            session.add(clip_job)
            session.commit()

        response = client.get("/api/tts/clips")
        assert response.status_code == 200
        assert b"Hello there" in response.content
        assert b"tts_42.wav" in response.content

    def test_excludes_document_scope_jobs(self, client, seeded_db):
        # seeded_db's "Test Job" is a completed document-scope job — must not
        # appear in the Quick TTS clip history.
        response = client.get("/api/tts/clips")
        assert response.status_code == 200
        assert b"Test Job" not in response.content