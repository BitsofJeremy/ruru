#!/usr/bin/env python3
"""Generate hello_lilith.mp3 by cloning Lilith's voice with Qwen3-TTS (MLX).

Loads the cached 8-bit Base clone model and synthesises the word "Hello" using
Lilith's intro WAV as the reference voice, writing an MP3 to the repo root.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Surface the engine's INFO logs so the model-load + generation trail is
# captured on stderr (matches TODO.md's verification log line).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

from engine.tts import TTSEngine  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent
REF_AUDIO = REPO_ROOT / "voice_clones" / "lilith.mp3"
REF_TEXT = REPO_ROOT / "voice_clones" / "lilith.txt"
OUTPUT = REPO_ROOT / "hello_lilith.mp3"
TEXT = """You're here because you already know.

You know it in the way your breath catches when someone looks at you too long. You know it in the hollow ache that wakes you at 3 AM, the one you were taught to pray away. You know it in the flush that climbs your throat when you remember how it felt — really felt — to be wanted, to be taken, to take.

They told you desire was dangerous. They told you it was something to tame, to temper, to surrender at the garden gate. They were wrong.

I'm Lilith. I was the first woman, and I was the first to walk out."""
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

    engine = TTSEngine()
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
