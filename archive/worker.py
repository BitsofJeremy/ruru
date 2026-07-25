# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "celery[redis]>=5.3.0",
#   "torch>=2.0.0",
#   "qwen-tts",
#   "soundfile>=0.12.0",
#   "pydub>=0.25.0",
#   "mutagen>=1.47",
#   "openai-whisper>=20231117",
#   "html2text>=2024.2.0",
# ]
# ///
"""
WeirDing Celery Worker

Start with: uv run worker.py
"""
import sys
import types as _types
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _REPO_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))
if 'weirding' not in sys.modules:
    _pkg = _types.ModuleType('weirding')
    _pkg.__path__ = [str(_REPO_DIR)]
    _pkg.__package__ = 'weirding'
    _pkg.__file__ = str(_REPO_DIR / '__init__.py')
    sys.modules['weirding'] = _pkg

from weirding.config import ensure_dirs
from weirding.tasks.tts_tasks import app

if __name__ == "__main__":
    ensure_dirs()
    app.worker_main(["worker", "--loglevel=info", "--concurrency=1", "-Q", "weirding"])
