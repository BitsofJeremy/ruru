"""ruru configuration.

Override with RURU_ prefixed env vars. The legacy WEIRDING_ prefix (from
this app's original name) is still read as a deprecated fallback during the
migration window -- see docs/migration.md. RURU_X always wins if both
RURU_X and WEIRDING_X are set.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

APP_VERSION = "0.0.3"


def _env(name: str, default: str | None = None) -> str | None:
    """Read RURU_{name}, falling back to the deprecated WEIRDING_{name}.

    Logs a deprecation warning when only the legacy WEIRDING_ prefix is set,
    so operators upgrading in place know to migrate their env/.env/launchd
    config to the RURU_ prefix.
    """
    new_key = f"RURU_{name}"
    old_key = f"WEIRDING_{name}"
    if new_key in os.environ:
        return os.environ[new_key]
    if old_key in os.environ:
        logger.warning(
            "%s is deprecated and will stop working in a future release -- "
            "use %s instead. See docs/migration.md.",
            old_key, new_key,
        )
        return os.environ[old_key]
    return default


WORK_DIR = Path(_env("WORK_DIR", str(Path(__file__).parent.resolve() / "data")))
DATA_DIR = WORK_DIR / "cache"
UPLOAD_DIR = DATA_DIR / "uploads"
CHUNK_DIR = DATA_DIR / "chunks"
OUTPUT_DIR = DATA_DIR / "output"
VOICE_DIR = DATA_DIR / "voices"
DB_PATH = WORK_DIR / "ruru.db"

HOST = _env("HOST", "0.0.0.0")
PORT = int(_env("PORT", "5151"))
MODEL_SIZE = _env("MODEL_SIZE", "auto")
MLX_MODELS_PATH = _env("MLX_MODELS_PATH", str(Path.home() / ".cache/huggingface/hub"))
AUDIO_FORMAT = _env("AUDIO_FORMAT", "mp3")
AUDIO_BITRATE = _env("AUDIO_BITRATE", "128k")
CHUNK_SIZE_WORDS = int(_env("CHUNK_SIZE_WORDS", "120"))
CHUNK_MAX_CHARS = int(_env("CHUNK_MAX_CHARS", "700"))
MAX_RETRIES = int(_env("MAX_RETRIES", "3"))
CREATOR_TAG = _env("CREATOR_TAG", "Created by ruru")

_REPO = Path(_env(
    "_REPO",
    str(Path(__file__).parent.parent.resolve())
))


def migrate_legacy_db_if_needed(work_dir: Path = None, db_path: Path = None) -> bool:
    """One-shot migration: rename a pre-rebrand `weirding.db` to `ruru.db`.

    The WeirDing -> ruru rebrand changed the SQLite filename. Without this,
    an existing install would silently lose its job/voice history on
    upgrade (init_db would just create a fresh, empty ruru.db next to the
    orphaned weirding.db). Only renames when the new path doesn't already
    exist; never touches/deletes the legacy file otherwise. Call once at
    app startup, before init_db().
    """
    work_dir = work_dir if work_dir is not None else WORK_DIR
    db_path = db_path if db_path is not None else DB_PATH
    legacy_db_path = Path(work_dir) / "weirding.db"

    if db_path.exists() or not legacy_db_path.exists():
        return False

    legacy_db_path.rename(db_path)
    logger.info("Migrated legacy database %s -> %s", legacy_db_path, db_path)
    return True


def ensure_dirs():
    """Create all required data directories."""
    for d in (DATA_DIR, UPLOAD_DIR, CHUNK_DIR, OUTPUT_DIR, VOICE_DIR):
        d.mkdir(parents=True, exist_ok=True)
