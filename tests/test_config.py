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
        monkeypatch.delenv("WEIRDING_WORK_DIR", raising=False)
        monkeypatch.setenv("RURU_WORK_DIR", "/custom/path")
        # Re-import to pick up env var
        import importlib, config as c
        importlib.reload(c)
        assert str(c.WORK_DIR) == "/custom/path"

    def test_port_from_env(self, monkeypatch):
        monkeypatch.delenv("WEIRDING_PORT", raising=False)
        monkeypatch.setenv("RURU_PORT", "9999")
        import importlib, config as c
        importlib.reload(c)
        assert c.PORT == 9999

    def test_chunk_size_from_env(self, monkeypatch):
        monkeypatch.delenv("WEIRDING_CHUNK_SIZE_WORDS", raising=False)
        monkeypatch.setenv("RURU_CHUNK_SIZE_WORDS", "300")
        import importlib, config as c
        importlib.reload(c)
        assert c.CHUNK_SIZE_WORDS == 300

    def test_chunk_size_default_is_120(self, monkeypatch):
        monkeypatch.delenv("WEIRDING_CHUNK_SIZE_WORDS", raising=False)
        monkeypatch.delenv("RURU_CHUNK_SIZE_WORDS", raising=False)
        import importlib, config as c
        importlib.reload(c)
        assert c.CHUNK_SIZE_WORDS == 120

    def test_chunk_max_chars_from_env(self, monkeypatch):
        monkeypatch.delenv("WEIRDING_CHUNK_MAX_CHARS", raising=False)
        monkeypatch.setenv("RURU_CHUNK_MAX_CHARS", "500")
        import importlib, config as c
        importlib.reload(c)
        assert c.CHUNK_MAX_CHARS == 500

    def test_chunk_max_chars_default_is_700(self, monkeypatch):
        monkeypatch.delenv("WEIRDING_CHUNK_MAX_CHARS", raising=False)
        monkeypatch.delenv("RURU_CHUNK_MAX_CHARS", raising=False)
        import importlib, config as c
        importlib.reload(c)
        assert c.CHUNK_MAX_CHARS == 700


class TestConfigDeprecatedWeirdingPrefixFallback:
    """WEIRDING_ prefixed env vars must still work during the migration
    window, as a deprecated fallback behind the new RURU_ prefix."""

    def test_work_dir_from_legacy_weirding_prefix(self, monkeypatch):
        monkeypatch.delenv("RURU_WORK_DIR", raising=False)
        monkeypatch.setenv("WEIRDING_WORK_DIR", "/legacy/path")
        import importlib, config as c
        importlib.reload(c)
        assert str(c.WORK_DIR) == "/legacy/path"

    def test_port_from_legacy_weirding_prefix(self, monkeypatch):
        monkeypatch.delenv("RURU_PORT", raising=False)
        monkeypatch.setenv("WEIRDING_PORT", "8123")
        import importlib, config as c
        importlib.reload(c)
        assert c.PORT == 8123

    def test_ruru_prefix_takes_precedence_over_legacy(self, monkeypatch):
        monkeypatch.setenv("WEIRDING_PORT", "1111")
        monkeypatch.setenv("RURU_PORT", "2222")
        import importlib, config as c
        importlib.reload(c)
        assert c.PORT == 2222

    def test_legacy_prefix_logs_deprecation_warning(self, monkeypatch, caplog):
        monkeypatch.delenv("RURU_PORT", raising=False)
        monkeypatch.setenv("WEIRDING_PORT", "8123")
        import importlib, config as c
        import logging
        with caplog.at_level(logging.WARNING):
            importlib.reload(c)
        assert any("WEIRDING_PORT" in rec.message and "deprecated" in rec.message.lower()
                   for rec in caplog.records)


class TestMigrateLegacyDb:
    """weirding.db -> ruru.db rename: existing job history must survive the
    rebrand's DB filename change (see docs/migration.md)."""

    def test_renames_legacy_db_when_new_one_absent(self, tmp_path):
        from config import migrate_legacy_db_if_needed

        work_dir = tmp_path
        legacy = work_dir / "weirding.db"
        new = work_dir / "ruru.db"
        legacy.write_text("legacy-db-contents")

        migrated = migrate_legacy_db_if_needed(work_dir=work_dir, db_path=new)

        assert migrated is True
        assert new.exists()
        assert not legacy.exists()
        assert new.read_text() == "legacy-db-contents"

    def test_noop_when_new_db_already_exists(self, tmp_path):
        from config import migrate_legacy_db_if_needed

        work_dir = tmp_path
        legacy = work_dir / "weirding.db"
        new = work_dir / "ruru.db"
        legacy.write_text("legacy")
        new.write_text("current")

        migrated = migrate_legacy_db_if_needed(work_dir=work_dir, db_path=new)

        assert migrated is False
        assert new.read_text() == "current"
        # legacy file is left alone -- do not silently delete user data
        assert legacy.exists()

    def test_noop_when_neither_db_exists(self, tmp_path):
        from config import migrate_legacy_db_if_needed

        work_dir = tmp_path
        new = work_dir / "ruru.db"

        migrated = migrate_legacy_db_if_needed(work_dir=work_dir, db_path=new)

        assert migrated is False
        assert not new.exists()
