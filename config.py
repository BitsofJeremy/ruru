"""ruru configuration.

Override with RURU_ prefixed env vars.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

APP_VERSION = "0.0.3"


def _env(name: str, default: str | None = None) -> str | None:
    """Read RURU_{name} from the environment."""
    return os.environ.get(f"RURU_{name}", default)


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

EPHERGENT_REPO = Path(_env(
    "EPHERGENT_REPO",
    str(Path(__file__).parent.parent.resolve())
))


def ensure_dirs():
    """Create all required data directories."""
    for d in (DATA_DIR, UPLOAD_DIR, CHUNK_DIR, OUTPUT_DIR, VOICE_DIR):
        d.mkdir(parents=True, exist_ok=True)
