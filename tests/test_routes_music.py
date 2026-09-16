"""Tests for the YuE2 music routes (/music page + /api/music)."""
from unittest.mock import AsyncMock

import pytest


class TestMusicPage:
    def test_get_music_page(self, client, seeded_db):
        response = client.get("/music")
        assert response.status_code == 200
        assert b"Music" in response.content


class TestAPIMusic:
    def test_missing_style_rejected(self, client, seeded_db):
        response = client.post("/api/music", data={"lyrics": "[instrumental]"})
        assert response.status_code == 400

    def test_missing_lyrics_rejected(self, client, seeded_db):
        response = client.post("/api/music", data={"style": "lofi"})
        assert response.status_code == 400

    def test_completed_job_returns_audio_fragment(self, client, seeded_db, mocker):
        """Happy path: enqueues GENERATE_MUSIC and swaps in an audio player."""
        import app as app_module

        mock_runner = app_module.get_runner.return_value

        async def _resolve():
            return {"status": "completed", "output_path": "/fake/music_1.wav"}

        # route contract: `future = await runner.enqueue(...)` then
        # `result = await future` — AsyncMock + coroutine side_effect gives
        # exactly that double-await shape.
        mock_runner.enqueue = AsyncMock(side_effect=lambda *a, **kw: _resolve())

        response = client.post("/api/music", data={
            "style": "lofi hip hop, vinyl crackle",
            "lyrics": "[instrumental]",
            "instrumental": "on",
            "seed": "21",
            "audio_format": "wav",
        })
        assert response.status_code == 200
        assert b"<audio" in response.content
        assert mock_runner.enqueue.await_args.args[1].value == "generate_music"
        payload = mock_runner.enqueue.await_args.args[2]
        assert payload["instrumental"] is True
        assert payload["seed"] == 21

    def test_failed_job_returns_error(self, client, seeded_db, mocker):
        import app as app_module

        mock_runner = app_module.get_runner.return_value

        async def _resolve_failed():
            return {"status": "failed", "message": "GPU on fire"}

        mock_runner.enqueue = AsyncMock(side_effect=lambda *a, **kw: _resolve_failed())

        response = client.post("/api/music", data={
            "style": "lofi",
            "lyrics": "[instrumental]",
        })
        assert response.status_code == 500
        assert b"GPU on fire" in response.content
