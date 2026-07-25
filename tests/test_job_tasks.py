"""Tests for jobs/tasks.py — sync job handlers against a real temp DB with the
TTS engine mocked.

The stale tests mocked `sqlite3.connect`, but the ORM-refactored handlers now
use `next(get_sync_session())` + `session.get(Job, job_id)`, so each test builds
a real DB (via init_db) and seeds a Job through the ORM. Routing is asserted on
whether a reference audio file exists on disk (BUG 1), not on the voice_type
label.
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def job_db(tmp_db, tmp_work_dir):
    """Initialize a real DB (sync session factory set) and seed a Voice + Job.

    Returns the seeded job's id. Handlers call ``next(get_sync_session())`` and
    ``session.get(Job, job_id)``, so the DB must be created via ``init_db`` and a
    Job row must exist — otherwise get_sync_session() raises "Database path not
    set" and session.get() returns None.
    """
    import asyncio
    from models.db import init_db, Voice, Job, get_sync_session

    asyncio.run(init_db(tmp_db))

    with next(get_sync_session()) as session:
        voice = Voice(
            name="Test Voice",
            slug="test-voice",
            voice_type="",
            description="A calm voice",
            speaking_style="calm",
            emotional_range="[]",
        )
        session.add(voice)
        session.commit()
        session.refresh(voice)

        job = Job(title="Test Job", voice_id=voice.id, status="queued",
                  source_path="/test")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    return job_id


@pytest.fixture
def task_env(job_db, tmp_work_dir, monkeypatch):
    """Real DB + Job, with jobs.tasks.output_dir redirected to a temp dir.

    Handlers write to the module-level ``output_dir`` (real data/cache/output);
    redirecting it keeps tests from polluting the live data directory.
    """
    out = tmp_work_dir / "task_output"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("jobs.tasks.output_dir", out)
    return {"job_id": job_db, "output_dir": out}


def _fake_audio(duration_s: float = 1.0, sr: int = 24000):
    """A fake TTS result whose .write() actually creates the file on disk.

    Handlers call result.write(output_path); a plain MagicMock would record the
    call but leave no file behind, so write() is wired to create a real (tiny)
    file and the output-path assertions stay meaningful.

    ``.wav``/``.sr`` are set to a real array so run_narrate_document's
    duration-based glitch check (len(wav)/sr) can compare against a genuine
    number instead of a MagicMock; 1.0s comfortably covers the short (2-word)
    chunk texts used in these tests (expected ~0.8s at the assumed 150 wpm).
    """
    import numpy as np

    audio = MagicMock()
    audio.wav = np.zeros(int(duration_s * sr), dtype=np.float32)
    audio.sr = sr

    def _write(path):
        from pathlib import Path
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-audio")

    audio.write.side_effect = _write
    return audio


class TestRunGenerateTTS:
    @patch("engine.tts.TTSEngine")
    def test_clone_when_ref_audio_exists(self, mock_engine_cls, task_env, tmp_path):
        """A payload whose ref_audio_path points to a real file routes to clone,
        even though voice_type is the seeded '' label (BUG 1)."""
        from jobs.tasks import run_generate_tts

        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF\x00\x00")  # a real file on disk

        audio = _fake_audio()
        engine = MagicMock()
        engine.generate_clone.return_value = audio
        mock_engine_cls.return_value = engine

        result = run_generate_tts(
            task_env["job_id"],
            text="Hello world", voice_id=1, voice_type="",
            ref_audio_path=str(ref), ref_text="Reference text",
            audio_format="wav",
        )

        assert result["status"] == "completed"
        engine.generate_clone.assert_called_once()
        engine.generate_design.assert_not_called()
        audio.write.assert_called_once()
        assert (task_env["output_dir"] / f"tts_{task_env['job_id']}.wav").exists()

    @patch("engine.tts.TTSEngine")
    def test_design_when_ref_audio_missing(self, mock_engine_cls, task_env):
        """No usable ref audio on disk -> design branch (the original bug: every
        '' voice wrongly took design, but here it is the correct call)."""
        from jobs.tasks import run_generate_tts

        audio = _fake_audio()
        engine = MagicMock()
        engine.generate_design.return_value = audio
        mock_engine_cls.return_value = engine

        result = run_generate_tts(
            task_env["job_id"],
            text="Hello world", voice_id=1, voice_type="",
            ref_audio_path="/does/not/exist.wav",  # missing -> design
            voice_description="A calm British male voice",
            audio_format="wav",
        )

        assert result["status"] == "completed"
        engine.generate_design.assert_called_once()
        engine.generate_clone.assert_not_called()
        audio.write.assert_called_once()


class TestRunNarrateDocument:
    @patch("engine.tts.TTSEngine")
    def test_narrate_calls_clone_per_chunk(self, mock_engine_cls, task_env, tmp_path):
        """Narration with a real ref audio clones once per chunk."""
        from jobs.tasks import run_narrate_document

        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF\x00\x00")

        audio = _fake_audio()
        engine = MagicMock()
        engine.generate_clone.return_value = audio
        mock_engine_cls.return_value = engine

        # assemble_chunks now writes the final file itself and returns a bool.
        # Have the mock create the output file so the completion path is exercised.
        def _fake_assemble(wav_files, output_path, **kw):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"ID3final")
            return True

        with patch("engine.assembler.assemble_chunks", side_effect=_fake_assemble) as mock_assemble:
            result = run_narrate_document(
                task_env["job_id"],
                chunks=["Chunk one.", "Chunk two."],
                ref_audio_path=str(ref), ref_text="ref", audio_format="mp3",
                job_title="Test Book", chapter_boundaries=[],
                author="Test Author", cover_path="",
            )

        assert result["status"] == "completed"
        assert engine.generate_clone.call_count == 2
        engine.generate_design.assert_not_called()
        # Each chunk was written to its own WAV, and the assembler was called once
        # with that list of paths plus an output path.
        assert audio.write.call_count == 2
        mock_assemble.assert_called_once()
        assert (task_env["output_dir"] / f"narrate_{task_env['job_id']}.mp3").exists()

    @patch("engine.tts.TTSEngine")
    def test_narrate_falls_back_to_design(self, mock_engine_cls, task_env):
        """Narration without ref audio uses the design branch per chunk."""
        from jobs.tasks import run_narrate_document

        audio = _fake_audio()
        engine = MagicMock()
        engine.generate_design.return_value = audio
        mock_engine_cls.return_value = engine

        mock_final = _fake_audio()
        with patch("engine.assembler.assemble_chunks", return_value=mock_final):
            result = run_narrate_document(
                task_env["job_id"],
                chunks=["Only chunk."],
                ref_audio_path="",  # no ref -> design
                voice_description="Warm narrator", audio_format="mp3",
                job_title="Test Book", chapter_boundaries=[],
                author="Test Author", cover_path="",
            )

        assert result["status"] == "completed"
        engine.generate_design.assert_called_once()
        engine.generate_clone.assert_not_called()

    @patch("engine.tts.TTSEngine")
    def test_narrate_retries_glitched_chunk(self, mock_engine_cls, task_env):
        """A too-short (truncated) first attempt is retried and the good
        second attempt is what gets written."""
        from jobs.tasks import run_narrate_document

        bad = _fake_audio(duration_s=0.05)  # way under expected ~0.8s
        good = _fake_audio(duration_s=1.0)
        engine = MagicMock()
        engine.generate_design.side_effect = [bad, good]
        mock_engine_cls.return_value = engine

        with patch("engine.assembler.assemble_chunks", return_value=True):
            result = run_narrate_document(
                task_env["job_id"],
                chunks=["Only chunk."],
                ref_audio_path="",
                voice_description="Warm narrator", audio_format="mp3",
                job_title="Test Book", chapter_boundaries=[],
                author="Test Author", cover_path="",
            )

        assert result["status"] == "completed"
        assert engine.generate_design.call_count == 2
        good.write.assert_called_once()
        bad.write.assert_not_called()

    @patch("engine.tts.TTSEngine")
    def test_narrate_keeps_last_attempt_after_exhausting_retries(self, mock_engine_cls, task_env):
        """If every attempt still looks glitched, keep the last one and let
        the job complete rather than aborting the whole narration."""
        from jobs.tasks import run_narrate_document
        from config import MAX_RETRIES

        always_bad = [_fake_audio(duration_s=0.05) for _ in range(MAX_RETRIES)]
        engine = MagicMock()
        engine.generate_design.side_effect = always_bad
        mock_engine_cls.return_value = engine

        with patch("engine.assembler.assemble_chunks", return_value=True):
            result = run_narrate_document(
                task_env["job_id"],
                chunks=["Only chunk."],
                ref_audio_path="",
                voice_description="Warm narrator", audio_format="mp3",
                job_title="Test Book", chapter_boundaries=[],
                author="Test Author", cover_path="",
            )

        assert result["status"] == "completed"
        assert engine.generate_design.call_count == MAX_RETRIES
        always_bad[-1].write.assert_called_once()
        for earlier in always_bad[:-1]:
            earlier.write.assert_not_called()


class TestRunRegenerateChapter:
    @patch("engine.tts.TTSEngine")
    def test_regenerate_clone(self, mock_engine_cls, task_env, tmp_path):
        """Chapter regeneration clones when a ref audio file is present."""
        from jobs.tasks import run_regenerate_chapter

        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF\x00\x00")

        audio = _fake_audio()
        engine = MagicMock()
        engine.generate_clone.return_value = audio
        mock_engine_cls.return_value = engine

        result = run_regenerate_chapter(
            task_env["job_id"],
            chapter_id=3, text="New chapter text", voice_type="",
            ref_audio_path=str(ref), ref_text="ref",
        )

        assert result["status"] == "completed"
        engine.generate_clone.assert_called_once()
        engine.generate_design.assert_not_called()
        audio.write.assert_called_once()
        assert (task_env["output_dir"] / f"chapter_3_{task_env['job_id']}.wav").exists()
