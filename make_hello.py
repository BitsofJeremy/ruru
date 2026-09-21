#!/usr/bin/env python3
"""Generate hello_ruru.mp3 by cloning a bundled example voice with Qwen3-TTS.

Loads the cached 8-bit Base clone model (MLX backend) and synthesises a short
greeting using Vera's reference clip as the voice, writing an MP3 to the repo
root. Useful as a real (non-mocked) engine smoke test.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Surface the engine's INFO logs so the model-load + generation trail is
# captured on stderr.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

from engine import get_engine_class  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent
REF_AUDIO = REPO_ROOT / "voice_clones" / "vera.mp3"
REF_TEXT = REPO_ROOT / "voice_clones" / "vera.txt"
OUTPUT = REPO_ROOT / "hello_ruru.mp3"
TEXT = """You're here because you already know. Hello."""
LANGUAGE = "English"


def main() -> int:
    for p in (REF_AUDIO, REF_TEXT):
        if not p.exists():
            print(f"ERROR: missing reference file: {p}", file=sys.stderr)
            return 2

    print(
        f"Cloning voice: ref_audio={REF_AUDIO.name}, ref_text={REF_TEXT.name}, "
        f"text={TEXT!r}, language={LANGUAGE}, out={OUTPUT.name}",
        file=sys.stderr,
    )

    engine = get_engine_class()()
    written = engine.generate_to_file(
        text=TEXT,
        output_path=OUTPUT,
        ref_audio_path=REF_AUDIO,
        ref_text=REF_TEXT,  # wrapper reads the .txt file contents itself
        language=LANGUAGE,
    )

    written = Path(written)
    size = written.stat().st_size
    print(
        f"DONE: wrote {written} ({size} bytes)",
        file=sys.stderr,
    )
    if size == 0:
        print("ERROR: output file is empty", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
