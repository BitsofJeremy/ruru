"""Import a voice library from another ruru/WeirDing install.

Copies each voice's reference audio into this install's voice directory and
inserts the DB row with paths rewritten to local ones. Source rows typically
carry paths from the machine they were created on (e.g. a macOS
`/Users/jeremy/bin/WeirDing/...`), which are meaningless here.

Idempotent: a slug that already exists is skipped, so re-running is safe.

Usage:
    uv run python -m models.import_voices \\
        --source-db  /path/to/other/data/ruru.db \\
        --source-dir /path/to/other/data/cache/voices
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

# Columns carried across, in order. `id` and `created_at` are deliberately not
# copied — the destination assigns its own.
FIELDS = (
    "name", "slug", "description", "voice_type", "sample_audio_path",
    "ref_audio_path", "ref_text", "speaking_style", "emotional_range",
)


def _copy_asset(src_path: str | None, source_dir: Path, voice_dir: Path) -> str:
    """Copy one referenced file into voice_dir, returning its new path.

    Source paths are usually foreign, so match on basename within source_dir.
    Returns "" when there's nothing to copy or the file is missing.
    """
    if not src_path:
        return ""
    src = source_dir / Path(src_path).name
    if not src.is_file():
        return ""
    dest = voice_dir / src.name
    if not dest.exists():
        shutil.copy2(src, dest)
    return str(dest.resolve())


def import_voices(source_db: Path, source_dir: Path, db_path: Path, voice_dir: Path) -> tuple[int, int]:
    """Import voices from *source_db*. Returns (imported, skipped)."""
    voice_dir.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(source_db)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(db_path)
    imported = skipped = 0

    try:
        existing = {r[0] for r in dst.execute("SELECT slug FROM voices")}
        rows = src.execute(f"SELECT {', '.join(FIELDS)} FROM voices ORDER BY id").fetchall()

        for row in rows:
            slug = row["slug"]
            if slug in existing:
                print(f"  skip   {slug} (already present)")
                skipped += 1
                continue

            ref_audio = _copy_asset(row["ref_audio_path"], source_dir, voice_dir)
            if not ref_audio:
                print(f"  SKIP   {slug} — reference audio missing in {source_dir}")
                skipped += 1
                continue
            sample = _copy_asset(row["sample_audio_path"], source_dir, voice_dir) or ref_audio

            # ref_text is either a literal transcript or a path to a .txt.
            # engine/base.py:_resolve_ref_text() accepts both, but a foreign
            # path resolves to nothing here — so copy the file when it is one,
            # and write a .txt when it is literal text. Either way the
            # transcript ends up on disk rather than trapped in the database.
            raw = (row["ref_text"] or "").strip()
            if raw.endswith(".txt"):
                ref_text = _copy_asset(raw, source_dir, voice_dir)
                if not ref_text:
                    print(f"  SKIP   {slug} — transcript {Path(raw).name} missing")
                    skipped += 1
                    continue
            elif raw:
                out = voice_dir / f"{slug}_ref.txt"
                out.write_text(raw)
                ref_text = str(out.resolve())
            else:
                print(f"  SKIP   {slug} — no transcript; cloning needs one")
                skipped += 1
                continue

            values = dict(row)
            values.update(
                ref_audio_path=ref_audio,
                sample_audio_path=sample,
                ref_text=ref_text,
            )
            dst.execute(
                f"INSERT INTO voices ({', '.join(FIELDS)}) "
                f"VALUES ({', '.join('?' * len(FIELDS))})",
                [values[f] for f in FIELDS],
            )
            existing.add(slug)
            imported += 1
            print(f"  import {slug:<16} ({row['voice_type']})")

        dst.commit()
    finally:
        src.close()
        dst.close()

    return imported, skipped


def main() -> None:
    import config

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-db", required=True, type=Path)
    p.add_argument("--source-dir", required=True, type=Path,
                   help="The source install's cache/voices directory")
    p.add_argument("--db", type=Path, default=config.DB_PATH)
    p.add_argument("--voice-dir", type=Path, default=config.VOICE_DIR)
    args = p.parse_args()

    for path in (args.source_db, args.source_dir):
        if not path.exists():
            raise SystemExit(f"Not found: {path}")

    print(f"Importing from {args.source_db} -> {args.db}")
    imported, skipped = import_voices(
        args.source_db, args.source_dir, args.db, args.voice_dir
    )
    print(f"\nImported {imported}, skipped {skipped}.")


if __name__ == "__main__":
    main()
