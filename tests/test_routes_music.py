"""Tests for the YuE2 music routes (/music page + /api/music + clips/status)."""
import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock


def _add_job(
    *,
    title="Music: test",
    status="completed",
    scope="music",
    output_path=None,
    seed=None,
    progress=0,
    error_message=None,
    created_at=None,
):
    """Insert a Job row via the app's sync session; return its id."""
    from models.db import Job, get_sync_session

    with next(get_sync_session()) as session:
        job = Job(
            title=title,
            voice_id=None,
            status=status,
            source_path="style\n---\nlyrics",
            scope=scope,
            output_path=output_path,
            seed=seed,
            progress=progress,
            error_message=error_message,
            created_at=created_at,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


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

    def test_music_page_shows_history_panel(self, client, seeded_db):
        response = client.get("/music")
        assert response.status_code == 200
        assert b'id="music-history"' in response.content
        assert b'hx-get="/api/music/clips"' in response.content

    def test_music_page_shows_format_checkboxes(self, client, seeded_db):
        response = client.get("/music")
        assert response.status_code == 200
        assert response.content.count(b'name="formats"') == 3
        assert b'value="wav" checked' in response.content
        assert b'value="mp3" checked' in response.content
        assert b'value="m4a" checked' not in response.content

    def test_music_page_shows_length_and_advanced_fields(self, client, seeded_db):
        response = client.get("/music")
        assert response.status_code == 200
        assert b'name="max_seconds"' in response.content
        assert b'name="cot"' in response.content
        assert b'value="off"' in response.content
        assert b'value="melody"' in response.content
        assert b'value="full"' in response.content
        assert b'name="cfg_scale"' in response.content
        assert b'name="steps"' in response.content


class TestAPIMusic:
    def test_missing_style_rejected(self, client, seeded_db):
        response = client.post("/api/music", data={"lyrics": "[instrumental]"})
        assert response.status_code == 400

    def test_missing_lyrics_rejected(self, client, seeded_db):
        response = client.post("/api/music", data={"style": "lofi"})
        assert response.status_code == 400

    def test_garbage_seed_rejected(self, client, seeded_db):
        """Regression: int(seed) used to 500 on non-numeric seed input."""
        response = client.post(
            "/api/music",
            data={"style": "lofi", "lyrics": "[instrumental]", "seed": "abc"},
        )
        assert response.status_code == 400
        assert b"text-red-500" in response.content

    def test_seed_out_of_range_rejected(self, client, seeded_db):
        response = client.post(
            "/api/music",
            data={"style": "lofi", "lyrics": "[instrumental]", "seed": "-5"},
        )
        assert response.status_code == 400
        assert b"text-red-500" in response.content

    def test_blank_seed_generates_random_and_persists(self, client, seeded_db):
        import app as app_module
        from models.db import Job, get_sync_session

        mock_runner = app_module.get_runner.return_value
        mock_runner.enqueue = AsyncMock()

        response = client.post(
            "/api/music",
            data={"style": "lofi", "lyrics": "[instrumental]", "formats": ["wav", "mp3"]},
        )
        assert response.status_code == 200

        job_id, _, payload = mock_runner.enqueue.await_args.args
        assert isinstance(payload["seed"], int)
        assert 0 <= payload["seed"] <= 2**31 - 1

        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            assert job.seed == payload["seed"]

    def test_explicit_seed_persisted(self, client, seeded_db):
        import app as app_module
        from models.db import Job, get_sync_session

        mock_runner = app_module.get_runner.return_value
        mock_runner.enqueue = AsyncMock()

        response = client.post(
            "/api/music",
            data={
                "style": "lofi",
                "lyrics": "[instrumental]",
                "seed": "21",
                "formats": "wav",
            },
        )
        assert response.status_code == 200

        job_id, _, payload = mock_runner.enqueue.await_args.args
        assert payload["seed"] == 21

        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            assert job.seed == 21

    def test_no_formats_checked_rejected(self, client, seeded_db):
        # No formats field at all...
        response = client.post(
            "/api/music", data={"style": "lofi", "lyrics": "[instrumental]"}
        )
        assert response.status_code == 400
        assert b"Pick at least one export format." in response.content
        # ...and only formats outside {wav, mp3, m4a} (filter empties the list).
        response = client.post(
            "/api/music",
            data={"style": "lofi", "lyrics": "[instrumental]", "formats": "flac"},
        )
        assert response.status_code == 400
        assert b"Pick at least one export format." in response.content

    def test_formats_pass_through(self, client, seeded_db):
        import app as app_module

        mock_runner = app_module.get_runner.return_value
        mock_runner.enqueue = AsyncMock()

        response = client.post(
            "/api/music",
            data={
                "style": "lofi",
                "lyrics": "[instrumental]",
                "formats": ["wav", "m4a"],
            },
        )
        assert response.status_code == 200

        _, _, payload = mock_runner.enqueue.await_args.args
        assert payload["formats"] == ["wav", "m4a"]

    def test_max_seconds_validated(self, client, seeded_db):
        import app as app_module

        for bad in ("abc", "0", "301"):
            response = client.post(
                "/api/music",
                data={
                    "style": "lofi",
                    "lyrics": "[instrumental]",
                    "formats": "wav",
                    "max_seconds": bad,
                },
            )
            assert response.status_code == 400, bad
            assert b"text-red-500" in response.content

        mock_runner = app_module.get_runner.return_value
        mock_runner.enqueue = AsyncMock()
        response = client.post(
            "/api/music",
            data={
                "style": "lofi",
                "lyrics": "[instrumental]",
                "formats": "wav",
                "max_seconds": "240",
            },
        )
        assert response.status_code == 200
        _, _, payload = mock_runner.enqueue.await_args.args
        assert payload["max_seconds"] == 240

    def test_advanced_params_validated_and_forwarded(self, client, seeded_db):
        import app as app_module

        mock_runner = app_module.get_runner.return_value
        mock_runner.enqueue = AsyncMock()

        response = client.post(
            "/api/music",
            data={
                "style": "lofi",
                "lyrics": "[instrumental]",
                "formats": "wav",
                "cot": "melody",
                "cfg_scale": "1.5",
                "steps": "16",
            },
        )
        assert response.status_code == 200
        _, _, payload = mock_runner.enqueue.await_args.args
        assert payload["cot"] == "melody"
        assert payload["cfg_scale"] == 1.5
        assert payload["steps"] == 16

        for bad in ({"cot": "bogus"}, {"cfg_scale": "high"}, {"steps": "x"}):
            data = {
                "style": "lofi",
                "lyrics": "[instrumental]",
                "formats": "wav",
                **bad,
            }
            response = client.post("/api/music", data=data)
            assert response.status_code == 400, bad
            assert b"text-red-500" in response.content

    def test_generate_returns_polling_status_without_awaiting(self, client, seeded_db):
        """Fire-and-forget contract: the route awaits enqueue (to get the
        future) but never awaits the future itself, so a job that already has
        an exception lands on it without blowing up the HTTP response."""
        import app as app_module
        from jobs.queue import JobType

        mock_runner = app_module.get_runner.return_value

        # Build the future inside side_effect: asyncio.Future() needs a
        # current event loop, which exists in the TestClient portal thread
        # while the route runs (same shape the old blocking-contract tests
        # used). The future already carries an exception.
        futures = []

        def _future_with_exception(*args, **kwargs):
            f = asyncio.Future()
            f.set_exception(RuntimeError("engine exploded"))
            futures.append(f)
            return f

        mock_runner.enqueue = AsyncMock(side_effect=_future_with_exception)

        response = client.post(
            "/api/music",
            data={
                "style": "lofi hip hop, vinyl crackle",
                "lyrics": "[instrumental]",
                "instrumental": "on",
                "seed": "21",
                "formats": ["wav", "mp3"],
                "max_seconds": "60",
                "cot": "full",
                "cfg_scale": "1.5",
                "steps": "16",
            },
        )
        # If the route had awaited `future`, RuntimeError would propagate and
        # TestClient would raise it here instead of returning the fragment.
        assert response.status_code == 200

        job_id, job_type, payload = mock_runner.enqueue.await_args.args
        assert job_type == JobType.GENERATE_MUSIC
        assert payload == {
            "style": "lofi hip hop, vinyl crackle",
            "lyrics": "[instrumental]",
            "instrumental": True,
            "seed": 21,
            "cot": "full",
            "cfg_scale": 1.5,
            "steps": 16,
            "max_seconds": 60,
            "formats": ["wav", "mp3"],
        }
        assert f'hx-get="/api/music/status/{job_id}"' in response.text

        # The route left the exception unretrieved (that's the point — a route
        # that awaited it would have raised into the response above); retrieve
        # it here so the future's GC doesn't print "Future exception was never
        # retrieved" into the test output.
        assert isinstance(futures[0].exception(), RuntimeError)


class TestMusicStatus:
    def test_status_running_shows_polling_fragment(self, client, seeded_db):
        job_id = _add_job(status="running", progress=77)
        response = client.get(f"/api/music/status/{job_id}")
        assert response.status_code == 200
        assert b"77" in response.content
        assert f"/jobs/{job_id}".encode() in response.content
        assert "HX-Trigger" not in response.headers

    def test_status_completed_shows_player_seed_and_triggers(self, client, seeded_db, tmp_work_dir):
        out_dir = tmp_work_dir / "output"
        job_id = _add_job(title="Music: lofi", status="completed", seed=21)
        primary = out_dir / f"music_{job_id}.wav"
        primary.write_bytes(b"fake-wav")

        from models.db import Job, get_sync_session

        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            job.output_path = str(primary)
            session.commit()

        response = client.get(f"/api/music/status/{job_id}")
        assert response.status_code == 200
        assert b"Done" in response.content
        assert b"Music: lofi" in response.content
        assert b"<audio" in response.content
        assert b"Seed: 21" in response.content
        assert b">WAV</a>" in response.content  # per-format download link
        assert json.loads(response.headers["HX-Trigger"]) == {
            "job:complete": True,
            "refreshHistory": True,
        }

    def test_status_failed_shows_error(self, client, seeded_db):
        job_id = _add_job(status="failed", error_message="GPU on fire")
        response = client.get(f"/api/music/status/{job_id}")
        assert response.status_code == 200
        assert b"GPU on fire" in response.content
        assert response.headers["HX-Trigger"] == "job:complete"

    def test_status_missing_job_404(self, client, seeded_db):
        response = client.get("/api/music/status/99999")
        assert response.status_code == 404


class TestMusicClips:
    def test_clips_empty_state(self, client, seeded_db):
        response = client.get("/api/music/clips")
        assert response.status_code == 200
        assert b"No songs generated yet." in response.content

    def test_clips_lists_music_jobs_newest_first(self, client, seeded_db):
        _add_job(
            title="Song one",
            output_path="/fake/output/music_a.wav",
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )
        _add_job(
            title="Song two",
            output_path="/fake/output/music_b.wav",
            created_at=datetime(2026, 1, 3, 12, 0, 0),
        )
        _add_job(
            title="Song three",
            output_path="/fake/output/music_c.wav",
            created_at=datetime(2026, 1, 2, 12, 0, 0),
        )
        _add_job(title="Quick clip", scope="quick", output_path="/fake/output/tts_1.mp3")
        _add_job(title="Broken song", status="failed", output_path=None)

        response = client.get("/api/music/clips")
        assert response.status_code == 200
        text = response.text
        assert "Song two" in text
        assert "Song three" in text
        assert "Song one" in text
        # newest (Jan 3) before middle (Jan 2) before oldest (Jan 1)
        assert text.index("Song two") < text.index("Song three") < text.index("Song one")
        assert "Quick clip" not in text
        assert "Broken song" not in text

    def test_clips_shows_seed_player_and_format_links(self, client, seeded_db, tmp_work_dir):
        out_dir = tmp_work_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        job_id = _add_job(title="Seeded song", seed=21, output_path=str(out_dir / "music_1.wav"))
        (out_dir / "music_1.wav").write_bytes(b"fake-wav")
        (out_dir / "music_1.mp3").write_bytes(b"fake-mp3")
        # music_1.m4a deliberately NOT staged

        response = client.get("/api/music/clips")
        assert response.status_code == 200
        assert b"seed 21" in response.content
        assert b"<audio" in response.content
        assert b"/audio/output/music_1.wav" in response.content
        assert b"/audio/output/music_1.mp3" in response.content
        assert b"/audio/output/music_1.m4a" not in response.content

    def test_clips_limit_12(self, client, seeded_db):
        for i in range(13):
            _add_job(title=f"Song {i}", output_path=f"/fake/output/music_{i}.wav")

        response = client.get("/api/music/clips")
        assert response.status_code == 200
        # one delete button (hence one card) per rendered song
        assert response.content.count(b'hx-delete="/api/music/clips/') == 12


class TestMusicClipDelete:
    def test_delete_removes_row_files_and_returns_empty(self, client, seeded_db, tmp_work_dir):
        from models.db import Job, get_sync_session

        out_dir = tmp_work_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        job_id = _add_job(output_path=str(out_dir / "music_5.wav"))
        wav = out_dir / "music_5.wav"
        mp3 = out_dir / "music_5.mp3"
        wav.write_bytes(b"fake-wav")
        mp3.write_bytes(b"fake-mp3")

        response = client.delete(f"/api/music/clips/{job_id}")
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["HX-Trigger"] == "refreshHistory"
        assert not wav.exists()
        assert not mp3.exists()
        with next(get_sync_session()) as session:
            assert session.get(Job, job_id) is None

    def test_delete_removes_all_sibling_exports(self, client, seeded_db, tmp_work_dir):
        out_dir = tmp_work_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        job_id = _add_job(output_path=str(out_dir / "music_6.wav"))
        paths = [out_dir / f"music_6.{ext}" for ext in ("wav", "mp3", "m4a")]
        for p in paths:
            p.write_bytes(b"fake")

        response = client.delete(f"/api/music/clips/{job_id}")
        assert response.status_code == 200
        for p in paths:
            assert not p.exists(), p

    def test_delete_blocks_running_job(self, client, seeded_db):
        job_id = _add_job(status="running")
        response = client.delete(f"/api/music/clips/{job_id}")
        assert response.status_code == 400
        assert b"text-red-500" in response.content

    def test_delete_rejects_non_music_scope_and_missing(self, client, seeded_db):
        quick_job_id = _add_job(title="Quick clip", scope="quick")
        response = client.delete(f"/api/music/clips/{quick_job_id}")
        assert response.status_code == 404

        response = client.delete("/api/music/clips/99999")
        assert response.status_code == 404
