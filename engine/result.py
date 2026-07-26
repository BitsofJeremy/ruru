"""Backend-agnostic TTS output container.

Lives outside ``engine/tts.py`` so ``CUDAEngine`` can import it without
dragging in any MLX code (and vice versa).
"""
from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


class TTSResult:
    """In-memory TTS output: decoded wav array + optional raw mp3 bytes.

    Tuple-unpackable as ``(wav, sr)`` so legacy callers
    (``wav, sr = engine.generate_clone(...)``) keep working, while also
    exposing ``.wav`` / ``.sr`` attributes and ``.write(path)`` for
    ``jobs/tasks.py``.

    ``mp3_bytes`` is optional. The MLX backend generates mp3 natively and
    passes the encoded bytes through so ``.write("x.mp3")`` is a byte copy with
    no re-encode. The CUDA backend produces raw float arrays; it omits
    ``mp3_bytes`` and ``.write()`` encodes on demand, only if an mp3 is actually
    asked for.
    """

    __slots__ = ("wav", "sr", "mp3_bytes")

    def __init__(self, wav: np.ndarray, sr: int, mp3_bytes: bytes = b"") -> None:
        self.wav = np.asarray(wav)
        self.sr = int(sr)
        self.mp3_bytes = bytes(mp3_bytes or b"")

    # -- tuple-style access -------------------------------------------------
    def __iter__(self) -> Iterator:
        yield self.wav
        yield self.sr

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int):
        return (self.wav, self.sr)[index]

    # -- persistence --------------------------------------------------------
    def _encode_mp3(self) -> bytes:
        """Encode ``self.wav`` to mp3 via pydub/ffmpeg."""
        from pydub import AudioSegment

        import config

        buf = BytesIO()
        sf.write(buf, self.wav, self.sr, format="WAV", subtype="PCM_16")
        buf.seek(0)
        segment = AudioSegment.from_file(buf, format="wav")

        out = BytesIO()
        segment.export(out, format="mp3", bitrate=config.AUDIO_BITRATE)
        return out.getvalue()

    def write(self, path: str | Path) -> Path:
        """Write the generated audio to ``path``.

        ``.mp3`` -> writes the original encoded mp3 bytes when the backend
        supplied them (no re-encode), otherwise encodes from the wav array.
        ``.wav`` (or anything else) -> writes 16-bit PCM wav.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ext = out.suffix.lower()

        if ext == ".mp3":
            out.write_bytes(self.mp3_bytes or self._encode_mp3())
        else:
            sf.write(str(out), self.wav, self.sr, subtype="PCM_16")

        logger.info(f"  Wrote audio to: {out}")
        return out
