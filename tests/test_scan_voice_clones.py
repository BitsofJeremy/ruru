"""Tests for models/scan_voice_clones.py — voice_clones/ import + prompt parsing."""
import sqlite3

from models.scan_voice_clones import scan_voice_clones

import pytest


VOICE_COLUMNS = """CREATE TABLE voices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    slug TEXT NOT NULL UNIQUE,
    voice_type TEXT NOT NULL DEFAULT 'cloned',
    ref_audio_path TEXT NOT NULL DEFAULT '',
    ref_text TEXT NOT NULL DEFAULT '',
    sample_audio_path TEXT NOT NULL DEFAULT '',
    speaking_style TEXT NOT NULL DEFAULT '',
    emotional_range TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT ''
)"""


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "ruru.db")
    conn.execute(VOICE_COLUMNS)
    conn.commit()
    conn.close()
    return tmp_path / "ruru.db"


@pytest.fixture
def dirs(tmp_path):
    clones = tmp_path / "voice_clones"
    voices = tmp_path / "voices"
    clones.mkdir()
    voices.mkdir()
    return clones, voices


GUS_PROMPT = """Gus Guy

Warm Ordinary Everyman - A middle-aged American man's voice, mid-register and unpolished.
"""


def _write_voice(clones, stem, prompt=None):
    (clones / f"{stem}.mp3").write_bytes(b"fake audio")
    (clones / f"{stem}.txt").write_text(f"transcript for {stem}")
    if prompt is not None:
        (clones / f"{stem}.prompt.txt").write_text(prompt)


def _voices(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM voices").fetchall()
    conn.close()
    return rows


def test_import_with_prompt_populates_character_data(db, dirs):
    clones, voices = dirs
    _write_voice(clones, "gus", GUS_PROMPT)
    n = scan_voice_clones(db, clones, voices)
    assert n == 1
    row = _voices(db)[0]
    assert row["name"] == "Gus Guy"
    assert row["description"] == (
        "Warm Ordinary Everyman — A middle-aged American man's voice, "
        "mid-register and unpolished."
    )
    assert row["speaking_style"] == "Warm Ordinary Everyman"
    assert row["voice_type"] == "cloned"
    assert (voices / "gus_clone.mp3").exists()
    assert (voices / "gus_clone.txt").exists()


def test_import_without_prompt_keeps_current_behavior(db, dirs):
    clones, voices = dirs
    _write_voice(clones, "vera")
    n = scan_voice_clones(db, clones, voices)
    assert n == 1
    row = _voices(db)[0]
    assert row["name"] == "Vera"
    assert row["description"] == ""
    assert row["speaking_style"] == ""


def test_prompt_without_separator_whole_body_is_description(db, dirs):
    clones, voices = dirs
    _write_voice(clones, "wesley", "Wesley Wilder\n\nJust a paragraph, no separator.\n")
    scan_voice_clones(db, clones, voices)
    row = _voices(db)[0]
    assert row["name"] == "Wesley Wilder"
    assert row["description"] == "Just a paragraph, no separator."
    assert row["speaking_style"] == ""


def test_rescan_does_not_duplicate_or_alter(db, dirs):
    clones, voices = dirs
    _write_voice(clones, "gus", GUS_PROMPT)
    scan_voice_clones(db, clones, voices)
    first = _voices(db)[0]
    assert scan_voice_clones(db, clones, voices) == 0
    assert len(_voices(db)) == 1
    assert _voices(db)[0] == first


def test_backfills_row_with_empty_description(db, dirs):
    clones, voices = dirs
    _write_voice(clones, "gus", GUS_PROMPT)
    scan_voice_clones(db, clones, voices)
    # Simulate a legacy import: wipe the character data but keep the row.
    conn = sqlite3.connect(db)
    conn.execute("UPDATE voices SET name='gus', description='', speaking_style=''")
    conn.commit(); conn.close()
    assert scan_voice_clones(db, clones, voices) == 0  # backfill isn't a new import
    row = _voices(db)[0]
    assert row["name"] == "Gus Guy"
    assert row["description"].startswith("Warm Ordinary Everyman — A middle-aged")
    assert row["speaking_style"] == "Warm Ordinary Everyman"


def test_does_not_clobber_hand_edited_description(db, dirs):
    clones, voices = dirs
    _write_voice(clones, "gus", GUS_PROMPT)
    scan_voice_clones(db, clones, voices)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE voices SET name='My Gus', description='hand edited'")
    conn.commit(); conn.close()
    scan_voice_clones(db, clones, voices)
    row = _voices(db)[0]
    assert row["name"] == "My Gus"
    assert row["description"] == "hand edited"


def test_undecodable_prompt_file_is_skipped(db, dirs):
    clones, voices = dirs
    _write_voice(clones, "ruth")
    (clones / "ruth.prompt.txt").write_bytes(b"Ruth \xff\xfe bad bytes")
    assert scan_voice_clones(db, clones, voices) == 1
    row = _voices(db)[0]
    assert row["name"] == "Ruth"
    assert row["description"] == ""


def _seed_ui_voice(db, name="Gus Guy", slug="gus-guy"):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO voices (name,slug,voice_type,description) VALUES (?,?,?,?)",
        (name, slug, "cloned", "added via the UI"),
    )
    conn.commit(); conn.close()


def test_name_match_skips_ui_created_clone(db, dirs):
    clones, voices = dirs
    _seed_ui_voice(db)
    _write_voice(clones, "gus", GUS_PROMPT)
    assert scan_voice_clones(db, clones, voices) == 0
    rows = _voices(db)
    assert len(rows) == 1
    assert rows[0]["slug"] == "gus-guy"
    assert rows[0]["description"] == "added via the UI"
    assert not list(voices.iterdir())  # nothing copied


def test_name_match_is_case_insensitive(db, dirs):
    clones, voices = dirs
    _seed_ui_voice(db, name="gus guy", slug="gus-guy")
    _write_voice(clones, "gus", GUS_PROMPT)
    assert scan_voice_clones(db, clones, voices) == 0
    assert len(_voices(db)) == 1


def test_new_voice_still_imports_alongside_name_matched_one(db, dirs):
    clones, voices = dirs
    _seed_ui_voice(db)
    _write_voice(clones, "gus", GUS_PROMPT)
    _write_voice(clones, "mabel", "Mabel Marsh\n\nDry Wit - A wry older woman's voice.\n")
    assert scan_voice_clones(db, clones, voices) == 1
    slugs = {row["slug"] for row in _voices(db)}
    assert slugs == {"gus-guy", "mabel"}


def test_tombstoned_slug_stays_deleted(db, dirs):
    # Deleting a voice via the UI used to resurrect on the next restart or
    # Refresh: the scan re-imports any voice whose files remain in
    # voice_clones/. A row in deleted_voice_slugs marks the delete as
    # intentional so the scan skips it.
    clones, voices = dirs
    _write_voice(clones, "gus", GUS_PROMPT)
    assert scan_voice_clones(db, clones, voices) == 1

    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM voices")
    conn.execute("CREATE TABLE deleted_voice_slugs (slug TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO deleted_voice_slugs VALUES ('gus')")
    conn.commit(); conn.close()

    assert scan_voice_clones(db, clones, voices) == 0
    assert _voices(db) == []


def test_tombstone_skips_only_its_own_slug(db, dirs):
    clones, voices = dirs
    _write_voice(clones, "gus")
    _write_voice(clones, "mabel")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE deleted_voice_slugs (slug TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO deleted_voice_slugs VALUES ('gus')")
    conn.commit(); conn.close()
    assert scan_voice_clones(db, clones, voices) == 1
    assert [row["slug"] for row in _voices(db)] == ["mabel"]


def test_missing_tombstone_table_is_tolerated(db, dirs):
    # Fresh/legacy DBs created outside init_db have no deleted_voice_slugs
    # table — the scan must behave exactly as before, not crash.
    clones, voices = dirs
    _write_voice(clones, "gus")
    assert scan_voice_clones(db, clones, voices) == 1
