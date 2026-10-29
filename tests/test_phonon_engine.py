"""Unit tests for the optional Phonon live-TTS engine.

These never import the real ptts module — the import probe and the TTS handle
are faked/monkeypatched so the suite runs on any box, with or without ptts.
"""
import sys

import numpy as np
import pytest

import config
from engine import phonon_engine


@pytest.fixture(autouse=True)
def _reset_phonon_cache():
    phonon_engine.reset_cache()
    yield
    phonon_engine.reset_cache()


# ---------------------------------------------------------------------------
# Availability probe
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_mode_off_blocks_even_when_ptts_importable(self, monkeypatch):
        monkeypatch.setattr(config, "PHONON_MODE", "0")
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: object())
        assert phonon_engine.phonon_available() is False

    def test_mode_on_without_ptts_is_false(self, monkeypatch):
        monkeypatch.setattr(config, "PHONON_MODE", "1")
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: None)
        assert phonon_engine.phonon_available() is False

    def test_auto_on_with_ptts(self, monkeypatch):
        monkeypatch.setattr(config, "PHONON_MODE", "auto")
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: object())
        assert phonon_engine.phonon_available() is True

    def test_import_probe_survives_broken_ptts(self, monkeypatch):
        """A ptts install that raises on import must not take ruru down.

        ``sys.modules['ptts'] = None`` makes ``import ptts`` raise ImportError
        (without monkeypatching the import machinery out from under pytest).
        """
        monkeypatch.setitem(sys.modules, "ptts", None)
        assert phonon_engine._import_ptts() is None
        assert phonon_engine.phonon_available() is False


# ---------------------------------------------------------------------------
# Model guard (40m family is CC BY-NC — must never load)
# ---------------------------------------------------------------------------

class TestModelGuard:
    def test_default_31m_model_allowed(self):
        assert phonon_engine._check_model("hf://gradium/phonon-31m-en") == (
            "hf://gradium/phonon-31m-en"
        )

    def test_40m_model_rejected(self):
        with pytest.raises(ValueError, match="40m"):
            phonon_engine._check_model("hf://gradium/phonon-40m-en")

    def test_40m_hidden_in_local_path_rejected(self):
        with pytest.raises(ValueError):
            phonon_engine._check_model("/models/phonon-40m-fp16")


# ---------------------------------------------------------------------------
# Voice catalog
# ---------------------------------------------------------------------------

class TestVoices:
    def test_voices_sorted_by_language_then_name(self, monkeypatch):
        import types

        fake = types.SimpleNamespace(
            catalog_voices=lambda catalog=None: [
                {"name": "Zephyr", "voice_id": "z", "language": "en", "description": ""},
                {"name": "Alice", "voice_id": "a", "language": "en", "description": ""},
                {"name": "Bernard", "voice_id": "b", "language": "fr", "description": ""},
            ]
        )
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: fake)
        rows = phonon_engine.voices()
        # sorted by language ("en" < "fr"), then name
        assert [r["name"] for r in rows] == ["Alice", "Zephyr", "Bernard"]

    def test_voices_empty_when_disabled(self, monkeypatch):
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: None)
        assert phonon_engine.voices() == []

    def test_voice_exists_by_name_or_id(self, monkeypatch):
        import types

        fake = types.SimpleNamespace(
            catalog_voices=lambda catalog=None: [
                {"name": "Bryony", "voice_id": "gfP44RlHwr2kEwk7",
                 "language": "en", "description": ""}
            ]
        )
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: fake)
        assert phonon_engine.voice_exists("Bryony")
        assert phonon_engine.voice_exists("gfP44RlHwr2kEwk7")
        assert not phonon_engine.voice_exists("Nobody")
        assert not phonon_engine.voice_exists("")


# ---------------------------------------------------------------------------
# WAV bytes generation (fake handle, no ptts)
# ---------------------------------------------------------------------------

class _FakeStream:
    sample_rate = 24000

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __iter__(self):
        return iter(self._chunks)

    def close(self):
        pass


class _FakeTTS:
    sample_rate = 24000

    def __init__(self):
        self.calls = []

    def stream(self, text, *, voice=None, **kw):
        self.calls.append(("stream", text, voice))
        t = np.linspace(0, 0.1, 2400, endpoint=False, dtype=np.float32)
        return _FakeStream([np.sin(2 * np.pi * 440 * t)])

    def synth(self, text, *, voice=None, **kw):
        self.calls.append(("synth", text, voice))
        t = np.linspace(0, 0.1, 2400, endpoint=False, dtype=np.float32)
        return np.sin(2 * np.pi * 440 * t).astype(np.float32)


class TestGenerate:
    def test_generate_wav_bytes_yields_riff_24k_mono(self, monkeypatch):
        fake = _FakeTTS()
        monkeypatch.setattr(phonon_engine, "_load_handle", lambda: fake)
        wav = phonon_engine.generate_wav_bytes("hello", "Bryony")
        assert wav[:4] == b"RIFF"
        assert b"WAVE" in wav[:12]
        # 2400 samples * 2 bytes (PCM16) + 44-byte header
        assert len(wav) == 44 + 2400 * 2

    def test_generate_stream_yields_chunk_and_sample_rate(self, monkeypatch):
        fake = _FakeTTS()
        monkeypatch.setattr(phonon_engine, "_load_handle", lambda: fake)
        chunks = list(phonon_engine.generate_stream("hello", "Bryony"))
        assert len(chunks) == 1
        arr, sr = chunks[0]
        assert sr == 24000
        assert arr.dtype == np.float32

    def test_generate_passes_voice_through(self, monkeypatch):
        fake = _FakeTTS()
        monkeypatch.setattr(phonon_engine, "_load_handle", lambda: fake)
        phonon_engine.generate_wav_bytes("hello", "gfP44RlHwr2kEwk7")
        assert fake.calls[-1][2] == "gfP44RlHwr2kEwk7"


# ---------------------------------------------------------------------------
# Handle loading (config threading + lang wiring)
# ---------------------------------------------------------------------------

class TestLoadHandle:
    def _fake_ptts_module(self, recordings):
        import types

        def set_num_threads(n):
            recordings.append(("threads", n))

        def make_tts(model, lang=None, quant=None):
            recordings.append(("tts", model, lang, quant))
            return _FakeTTS()

        return types.SimpleNamespace(
            set_num_threads=set_num_threads,
            TTS=make_tts,
        )

    def test_load_sets_threads_before_model_and_uses_config(self, monkeypatch):
        seen = []
        fake_mod = self._fake_ptts_module(seen)
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: fake_mod)
        monkeypatch.setattr(config, "PHONON_THREADS", 3)
        monkeypatch.setattr(config, "PHONON_MODEL", "hf://gradium/phonon-31m-en")
        monkeypatch.setattr(config, "PHONON_LANG", "en")
        monkeypatch.setattr(config, "PHONON_QUANT", "q8")

        handle = phonon_engine._load_handle()
        assert isinstance(handle, _FakeTTS)
        # set_num_threads must land before TTS construction (global pool is
        # built once at thread-pool creation).
        assert seen[0] == ("threads", 3)
        assert seen[1] == ("tts", "hf://gradium/phonon-31m-en", "en", "q8")

    def test_handle_cached_across_calls(self, monkeypatch):
        seen = []
        fake_mod = self._fake_ptts_module(seen)
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: fake_mod)
        monkeypatch.setattr(config, "PHONON_THREADS", 0)
        h1 = phonon_engine._load_handle()
        h2 = phonon_engine._load_handle()
        assert h1 is h2
        assert len(seen) == 1

    def test_load_rejects_40m(self, monkeypatch):
        seen = []
        fake_mod = self._fake_ptts_module(seen)
        monkeypatch.setattr(phonon_engine, "_import_ptts", lambda: fake_mod)
        monkeypatch.setattr(config, "PHONON_MODEL", "hf://gradium/phonon-40m-en")
        with pytest.raises(ValueError):
            phonon_engine._load_handle()


# ---------------------------------------------------------------------------
# Warm
# ---------------------------------------------------------------------------

class TestWarm:
    def test_warm_ok(self, monkeypatch):
        monkeypatch.setattr(phonon_engine, "_load_handle", lambda: _FakeTTS())
        assert phonon_engine.warm() is True

    def test_warm_failure_false(self, monkeypatch):
        def boom():
            raise RuntimeError("download failed")
        monkeypatch.setattr(phonon_engine, "_load_handle", boom)
        assert phonon_engine.warm() is False
