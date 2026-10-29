"""ruru configuration.

Override with RURU_ prefixed env vars.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

def _app_version() -> str:
    """Single-source the version so it can't drift between files.

    `pyproject.toml` is checked first and is authoritative: the package is the
    repo root itself, so ruru always runs from its source tree, and installed
    metadata goes stale the moment you bump the version without re-syncing.
    Falls back to package metadata (installed elsewhere), then to a literal.
    """
    pyproject = Path(__file__).parent / "pyproject.toml"
    try:
        for line in pyproject.read_text().splitlines():
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass

    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ruru")
    except PackageNotFoundError:
        return "0.0.0"


APP_VERSION = _app_version()


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
# Which TTS backend to run: "mlx" (Apple Silicon) or "cuda" (NVIDIA).
TTS_BACKEND = _env("TTS_BACKEND", "mlx")
MLX_MODELS_PATH = _env("MLX_MODELS_PATH", str(Path.home() / ".cache/huggingface/hub"))
AUDIO_FORMAT = _env("AUDIO_FORMAT", "mp3")
AUDIO_BITRATE = _env("AUDIO_BITRATE", "128k")
CHUNK_SIZE_WORDS = int(_env("CHUNK_SIZE_WORDS", "120"))
CHUNK_MAX_CHARS = int(_env("CHUNK_MAX_CHARS", "700"))
MAX_RETRIES = int(_env("MAX_RETRIES", "3"))
CREATOR_TAG = _env("CREATOR_TAG", "Created by ruru")

# --- YuE2 music generation --------------------------------------------------
# Runtime backend for music: "gguf" (audio.cpp subprocess, Q4 quant, no LoRA),
# "pytorch" (yue2_infer wheel, supports the instrumental LoRA), "mlx" (Apple
# Silicon via ahmadw/YuE2-3B-MLX scripts), or "" = auto (gguf on Linux, mlx on
# Darwin). The instrumental LoRA only exists for the pytorch runtime.
YUE2_RUNTIME = _env("YUE2_RUNTIME", "")
YUE2_MODEL_REPO = _env("YUE2_MODEL_REPO", "m-a-p/YuE2-3B")
YUE2_GGUF_DIR = Path(_env("YUE2_GGUF_DIR", str(DATA_DIR / "models/yue2-gguf")))
YUE2_AUDIOCPP_BIN = _env("YUE2_AUDIOCPP_BIN", "")
YUE2_INST_LORA_REPO = _env(
    "YUE2_INST_LORA_REPO", "Mothersuperior/YuE2-instrumental-cot-full-loras"
)
YUE2_INST_LORA_SCALE = float(_env("YUE2_INST_LORA_SCALE", "1.0"))
# Chain-of-thought preset — engine fallback and the form default for the
# Music page's Advanced CoT select (blank field → this value).
YUE2_COT = _env("YUE2_COT", "full")
# PyTorch-runtime song length cap (seconds) — keeps peak VRAM inside 12 GB.
# Per-job overridable from the Music form's Length field (blank = this cap).
YUE2_MAX_SECONDS = int(_env("YUE2_MAX_SECONDS", "90"))

# --- Phonon live TTS (optional, CPU-only, playback-only) ---------------------
# Feature switch: "1" = on, "0" = off, "auto" = on iff `import ptts` succeeds.
# With ptts absent (or this "0") ruru behaves exactly as before.
PHONON_MODE = _env("PHONON", "auto")
# Model repo or local dir. Only the Apache-2.0 31m family is permitted; the
# engine rejects any repo matching phonon-40m (CC BY-NC, noncommercial).
PHONON_MODEL = _env("PHONON_MODEL", "hf://gradium/phonon-31m-en")
PHONON_QUANT = _env("PHONON_QUANT", "q8")
# Text-normalization language ("en", "fr", "de", "es", "pt", "none").
PHONON_LANG = _env("PHONON_LANG", "en")
# Cap on tensor-op CPU threads, so live TTS can't starve the box.
PHONON_THREADS = int(_env("PHONON_THREADS", "4"))
# Hard cap on live-endpoint text length (characters).
PHONON_MAX_CHARS = int(_env("PHONON_MAX_CHARS", "2000"))


def ensure_dirs():
    """Create all required data directories."""
    for d in (DATA_DIR, UPLOAD_DIR, CHUNK_DIR, OUTPUT_DIR, VOICE_DIR):
        d.mkdir(parents=True, exist_ok=True)
