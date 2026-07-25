"""Tests for models/seed_voices.py.

Ephergent character seeding is an optional/local-only feature: the public
release does not ship `characters_data.py` or `ephergent_voices/`, so
`seed_voices()` must tolerate their absence rather than raising ImportError.
"""
import pytest
from pathlib import Path


class TestSeedVoices:
    @pytest.mark.asyncio
    async def test_seed_is_noop_without_characters_data(self, tmp_db, tmp_path):
        """When characters_data.py is not importable (public repo), seed_voices
        must not raise and must simply skip ephergent seeding."""
        from models.db import init_db
        from models.seed_voices import seed_voices

        await init_db(tmp_db)

        voices_dir = tmp_path / "ephergent_voices"
        voices_dir.mkdir()

        # Should not raise even though no characters_data module is on sys.path
        # and no ephergent_voices reference files exist.
        seed_voices(tmp_db, tmp_path, voices_dir)

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        cur = conn.execute("SELECT COUNT(*) FROM voices")
        count = cur.fetchone()[0]
        conn.close()

        assert count == 0

    @pytest.mark.asyncio
    async def test_seed_idempotent_when_noop(self, tmp_db, tmp_path):
        from models.db import init_db
        from models.seed_voices import seed_voices

        await init_db(tmp_db)
        voices_dir = tmp_path / "ephergent_voices"
        voices_dir.mkdir()

        seed_voices(tmp_db, tmp_path, voices_dir)
        seed_voices(tmp_db, tmp_path, voices_dir)  # Run again — still fine

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        cur = conn.execute("SELECT COUNT(*) FROM voices")
        count = cur.fetchone()[0]
        conn.close()

        assert count == 0

    @pytest.mark.asyncio
    async def test_seed_uses_characters_data_when_available(self, tmp_db, tmp_path, monkeypatch):
        """If a local characters_data module IS importable (developer's own
        local checkout), ephergent seeding still works as before."""
        import sys
        import types

        from models.db import init_db

        await init_db(tmp_db)
        voices_dir = tmp_path / "ephergent_voices"
        voices_dir.mkdir()

        # Fabricate a minimal characters_data module on sys.path for this test only.
        fake_module = types.ModuleType("characters_data")

        class FakeChar:
            def __init__(self, name):
                self.name = name
                self.ref_audio_path = "ephergent_voices/does_not_exist.wav"
                self.voice_description = "test voice"
                self.ref_text = "hello"
                self.speaking_style = "neutral"
                self.emotional_range = ["calm"]

        fake_module.CHARACTERS = {"testchar": FakeChar("Test Char")}
        monkeypatch.setitem(sys.modules, "characters_data", fake_module)

        # Force re-import of seed_voices so it picks up the fake module fresh.
        sys.modules.pop("models.seed_voices", None)
        from models.seed_voices import seed_voices

        seed_voices(tmp_db, tmp_path, voices_dir)

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        cur = conn.execute("SELECT COUNT(*) FROM voices")
        count = cur.fetchone()[0]
        conn.close()

        assert count == 1

        sys.modules.pop("models.seed_voices", None)
