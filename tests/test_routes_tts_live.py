"""Route tests for the Phonon live-TTS endpoints.

Contract under test:
  GET  /api/tts/live/voices — catalog probe; `available: false` when disabled.
  POST /api/tts/live        — binary WAV; NO Job row, NO Voice row, nothing
                              under OUTPUT_DIR (the "no save" guarantee).
  /tts page renders the Phonon toggle only when the feature is available.
"""
import pytest

import config
from engine import phonon_engine


FAKE_VOICES = [
    {"name": "Bryony", "voice_id": "gfP44RlHwr2kEwk7", "language": "en",
     "description": "Full, rich mid-range tones."},
    {"name": "Arnaud", "voice_id": "abc123", "language": "fr",
     "description": "French voice."},
]


@pytest.fixture
def phonon_on(monkeypatch):
    """Feature available, with a canned fake engine wired into the routes."""
    monkeypatch.setattr(config, "PHONON_MODE", "1")
    monkeypatch.setattr(phonon_engine, "phonon_available", lambda: True)
    monkeypatch.setattr(phonon_engine, "voices", lambda: list(FAKE_VOICES))
    monkeypatch.setattr(phonon_engine, "voice_exists",
                        lambda v: v in {"Bryony", "gfP44RlHwr2kEwk7", "Arnaud"})

    def fake_wav(text, voice):
        # 44-byte canonical PCM16 WAV header + 2 samples of silence
        return bytes.fromhex(
            "524946463000000057415645666d7420100000000100010058"
            "02000058020000010008000000"
        ) + b"\x00\x00\x00\x00"

    monkeypatch.setattr(phonon_engine, "generate_wav_bytes", fake_wav)
    return phonon_engine


class TestLiveVoices:
    def test_available_with_catalog(self, client, seeded_db, phonon_on):
        r = client.get("/api/tts/live/voices")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["voices"][0]["name"] == "Bryony"

    def test_disabled_shape(self, client, seeded_db, monkeypatch):
        monkeypatch.setattr(config, "PHONON_MODE", "0")
        monkeypatch.setattr(phonon_engine, "phonon_available", lambda: False)
        r = client.get("/api/tts/live/voices")
        assert r.status_code == 200
        assert r.json() == {"available": False, "voices": []}


class TestLiveGenerate:
    def test_happy_path_wav(self, client, seeded_db, phonon_on):
        r = client.post("/api/tts/live", json={"text": "Hello", "voice": "Bryony"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/wav")
        assert r.content[:4] == b"RIFF"

    def test_disabled_503(self, client, seeded_db, monkeypatch):
        monkeypatch.setattr(config, "PHONON_MODE", "0")
        monkeypatch.setattr(phonon_engine, "phonon_available", lambda: False)
        r = client.post("/api/tts/live", json={"text": "Hello", "voice": "Bryony"})
        assert r.status_code == 503
        assert r.json()["status"] == "feature_disabled"

    def test_empty_text_400(self, client, seeded_db, phonon_on):
        r = client.post("/api/tts/live", json={"text": "   ", "voice": "Bryony"})
        assert r.status_code == 400

    def test_bad_format_400(self, client, seeded_db, phonon_on):
        r = client.post("/api/tts/live",
                        json={"text": "Hello", "voice": "Bryony", "format": "mp3"})
        assert r.status_code == 400

    def test_oversize_text_413(self, client, seeded_db, phonon_on, monkeypatch):
        monkeypatch.setattr(config, "PHONON_MAX_CHARS", 10)
        r = client.post("/api/tts/live",
                        json={"text": "x" * 11, "voice": "Bryony"})
        assert r.status_code == 413

    def test_unknown_voice_404(self, client, seeded_db, phonon_on):
        r = client.post("/api/tts/live", json={"text": "Hello", "voice": "Nobody"})
        assert r.status_code == 404

    def test_engine_error_500(self, client, seeded_db, phonon_on, monkeypatch):
        def boom(text, voice):
            raise RuntimeError("decoder exploded")
        monkeypatch.setattr(phonon_engine, "generate_wav_bytes", boom)
        r = client.post("/api/tts/live", json={"text": "Hello", "voice": "Bryony"})
        assert r.status_code == 500

    def test_nothing_saved_purity(self, client, seeded_db, phonon_on, tmp_work_dir):
        """Live TTS is playback-only: no Job, no Voice, no output file."""
        from models.db import Job, Voice, get_sync_session

        with next(get_sync_session()) as session:
            jobs_before = session.query(Job).count()
            voices_before = session.query(Voice).count()
        output_before = sorted(p.name for p in tmp_work_dir.joinpath("output").iterdir())

        r = client.post("/api/tts/live", json={"text": "Hello", "voice": "Bryony"})
        assert r.status_code == 200

        with next(get_sync_session()) as session:
            assert session.query(Job).count() == jobs_before
            assert session.query(Voice).count() == voices_before
        assert sorted(p.name for p in tmp_work_dir.joinpath("output").iterdir()) == output_before


class TestTTSPageToggle:
    def test_toggle_hidden_when_disabled(self, client, seeded_db, monkeypatch):
        monkeypatch.setattr(phonon_engine, "phonon_available", lambda: False)
        html = client.get("/tts").text
        assert "Phonon live" not in html

    def test_toggle_present_when_enabled(self, client, seeded_db, phonon_on):
        html = client.get("/tts").text
        assert "Phonon live" in html
        assert "phonon_voice" in html
