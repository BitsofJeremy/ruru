"""Seed the database with Ephergent character voices.

Ephergent character seeding is an OPTIONAL, local-only feature: the public
repo does not ship `characters_data.py` or `ephergent_voices/*.wav` (they
contain a private fiction project's character profiles/audio). When that
module isn't present, `seed_voices()` simply skips ephergent seeding rather
than raising — the bundled example voices are seeded separately
via `models.scan_voice_clones`.
"""
import json
import logging
import shutil
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Allow importing characters_data from the repo root, if present.
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from characters_data import CHARACTERS as EPHERGENT_CHARACTERS
except ImportError:
    EPHERGENT_CHARACTERS = {}
    logger.info(
        "characters_data.py not found — skipping optional Ephergent character "
        "voice seeding (this is expected in the public release)."
    )


def seed_voices(db_path: Path, ephergent_repo: Path, voice_dir: Path):
    """Insert Ephergent characters into voices table if not already present.

    No-op when `characters_data.py` isn't importable (see module docstring).
    """
    if not EPHERGENT_CHARACTERS:
        return

    db = sqlite3.connect(str(db_path))

    # Voices the user deleted via the UI must stay deleted, even though a
    # restart re-runs this seed. (Table absent on fresh/legacy DBs.)
    try:
        tombstoned = {
            r[0] for r in db.execute("SELECT slug FROM deleted_voice_slugs").fetchall()
        }
    except sqlite3.OperationalError:
        tombstoned = set()

    for slug, char in EPHERGENT_CHARACTERS.items():
        if slug in tombstoned:
            continue
        row = db.execute("SELECT id FROM voices WHERE slug=?", (slug,)).fetchone()

        ref_src = ephergent_repo / char.ref_audio_path
        sample_dst = voice_dir / f"{slug}_sample.wav"
        ref_dst = voice_dir / f"{slug}_ref.wav"

        if row:
            # Idempotent ref-file integrity: if a voice row exists but its ref/sample
            # files vanished from disk (e.g. after a clean-only that deleted *_ref.wav),
            # re-copy them and repair the row's paths. No churn when files are present.
            if ref_src.exists():
                repaired = False
                if not sample_dst.exists():
                    shutil.copy2(ref_src, sample_dst)
                    repaired = True
                if not ref_dst.exists():
                    shutil.copy2(ref_src, ref_dst)
                    repaired = True
                if repaired:
                    db.execute(
                        "UPDATE voices SET sample_audio_path=?, ref_audio_path=? WHERE slug=?",
                        (str(sample_dst), str(ref_dst), slug),
                    )
            continue

        if ref_src.exists():
            shutil.copy2(ref_src, sample_dst)
            shutil.copy2(ref_src, ref_dst)
        else:
            sample_dst = None
            ref_dst = None

        db.execute(
            """INSERT INTO voices (name, slug, description, voice_type, sample_audio_path,
               ref_audio_path, ref_text, speaking_style, emotional_range)
               VALUES (?, ?, ?, 'ephergent', ?, ?, ?, ?, ?)""",
            (
                char.name, slug, char.voice_description,
                str(sample_dst) if sample_dst else None,
                str(ref_dst) if ref_dst else None,
                char.ref_text, char.speaking_style,
                json.dumps(char.emotional_range),
            ),
        )

    db.commit()
    db.close()
