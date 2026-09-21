"""Tests for the YuE2 music routes (/music page + /api/music)."""
from unittest.mock import AsyncMock

import pytest


class TestMusicPage:
    def test_get_music_page(self, client, seeded_db):
        response = client.get("/music")
        assert response.status_code == 200
        assert b"Music" in response.content

    def test_instrumental_checkbox_disabled_without_pytorch_runtime(
        self, client, seeded_db, monkeypatch
    ):
        """Mac default: no pytorch runtime -> checkbox must be disabled and
        NOT checked (a checked default would 500 every submit)."""
        from engine.yue2_engine import YuE2Engine

        monkeypatch.setattr(
            YuE2Engine, "_pytorch_ready", staticmethod(lambda: (False, "no torch"))
        )
        response = client.get("/music")
        assert response.status_code == 200
        assert b"name=\"instrumental\" id=\"instrumental\" disabled" in response.content
        assert b"id=\"instrumental\" checked" not in response.content
        assert b"unavailable on this machine" in response.content

    def test_instrumental_checkbox_enabled_with_pytorch_runtime(
        self, client, seeded_db, monkeypatch
    ):
        from engine.yue2_engine import YuE2Engine

        monkeypatch.setattr(
            YuE2Engine, "_pytorch_ready", staticmethod(lambda: (True, ""))
        )
        response = client.get("/music")
        assert response.status_code == 200
        assert b"id=\"instrumental\" checked" in response.content


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

    def test_job_exception_returns_error_not_blank_500(self, client, seeded_db, mocker):
        """A job that raises (e.g. instrumental on a Mac) sets an exception on
        the future — the route must surface the message, not blow up into the
        generic 500 error page."""
        import asyncio

        import app as app_module

        mock_runner = app_module.get_runner.return_value

        def _raising_future():
            f = asyncio.Future()
            f.set_exception(RuntimeError("Instrumental LoRA needs the PyTorch runtime"))
            return f

        mock_runner.enqueue = AsyncMock(side_effect=lambda *a, **kw: _raising_future())

        response = client.post("/api/music", data={
            "style": "lofi",
            "lyrics": "[instrumental]",
            "instrumental": "on",
        })
        assert response.status_code == 500
        assert b"Job failed" in response.content
        assert b"PyTorch runtime" in response.content
