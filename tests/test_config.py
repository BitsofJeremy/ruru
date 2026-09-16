"""Tests for config.py settings and ensure_dirs."""
import os
import tempfile
from pathlib import Path

import pytest


class TestEnsureDirs:
    def test_creates_data_dir(self, tmp_path):
        from config import ensure_dirs, DATA_DIR, UPLOAD_DIR, CHUNK_DIR, OUTPUT_DIR, VOICE_DIR

        # Override config paths temporarily
        import config as c
        c.DATA_DIR = tmp_path / "data"
        c.UPLOAD_DIR = tmp_path / "data" / "uploads"
        c.CHUNK_DIR = tmp_path / "data" / "chunks"
        c.OUTPUT_DIR = tmp_path / "data" / "output"
        c.VOICE_DIR = tmp_path / "data" / "voices"

        c.ensure_dirs()

        assert (tmp_path / "data").exists()
        assert (tmp_path / "data" / "uploads").exists()
        assert (tmp_path / "data" / "chunks").exists()
        assert (tmp_path / "data" / "output").exists()
        assert (tmp_path / "data" / "voices").exists()


class TestConfigDefaults:
    """Tests for the current RURU_ prefixed env vars."""

    def test_work_dir_from_env(self, monkeypatch):
        monkeypatch.setenv("RURU_WORK_DIR", "/custom/path")
        # Re-import to pick up env var
        import importlib, config as c
        importlib.reload(c)
        assert str(c.WORK_DIR) == "/custom/path"

    def test_port_from_env(self, monkeypatch):
        monkeypatch.setenv("RURU_PORT", "9999")
        import importlib, config as c
        importlib.reload(c)
        assert c.PORT == 9999

    def test_chunk_size_from_env(self, monkeypatch):
        monkeypatch.setenv("RURU_CHUNK_SIZE_WORDS", "300")
        import importlib, config as c
        importlib.reload(c)
        assert c.CHUNK_SIZE_WORDS == 300

    def test_chunk_size_default_is_120(self, monkeypatch):
        monkeypatch.delenv("RURU_CHUNK_SIZE_WORDS", raising=False)
        import importlib, config as c
        importlib.reload(c)
        assert c.CHUNK_SIZE_WORDS == 120

    def test_chunk_max_chars_from_env(self, monkeypatch):
        monkeypatch.setenv("RURU_CHUNK_MAX_CHARS", "500")
        import importlib, config as c
        importlib.reload(c)
        assert c.CHUNK_MAX_CHARS == 500

    def test_chunk_max_chars_default_is_700(self, monkeypatch):
        monkeypatch.delenv("RURU_CHUNK_MAX_CHARS", raising=False)
        import importlib, config as c
        importlib.reload(c)
        assert c.CHUNK_MAX_CHARS == 700


class TestYue2Config:
    """YuE2 music-generation config knobs (v0.7.0)."""

    def test_yue2_defaults(self, monkeypatch):
        for var in ("YUE2_RUNTIME", "YUE2_AUDIOCPP_BIN", "YUE2_COT",
                    "YUE2_INST_LORA_REPO", "YUE2_INST_LORA_SCALE",
                    "YUE2_MAX_SECONDS", "YUE2_GGUF_DIR", "YUE2_MODEL_REPO"):
            monkeypatch.delenv(f"RURU_{var}", raising=False)
        import importlib, config as c
        importlib.reload(c)
        assert c.YUE2_RUNTIME == ""          # "" = auto: gguf on Linux, mlx on Darwin
        assert c.YUE2_COT == "full"
        assert c.YUE2_MAX_SECONDS == 90
        assert c.YUE2_INST_LORA_SCALE == 1.0
        assert c.YUE2_INST_LORA_REPO == "Mothersuperior/YuE2-instrumental-cot-full-loras"
        assert c.YUE2_MODEL_REPO == "m-a-p/YuE2-3B"
        assert str(c.YUE2_GGUF_DIR).endswith("yue2-gguf")

    def test_yue2_runtime_from_env(self, monkeypatch):
        monkeypatch.setenv("RURU_YUE2_RUNTIME", "gguf")
        import importlib, config as c
        importlib.reload(c)
        assert c.YUE2_RUNTIME == "gguf"

    def test_yue2_max_seconds_from_env(self, monkeypatch):
        monkeypatch.setenv("RURU_YUE2_MAX_SECONDS", "60")
        import importlib, config as c
        importlib.reload(c)
        assert c.YUE2_MAX_SECONDS == 60
