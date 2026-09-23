"""Multi-format export of generated audio (WAV / MP3 / M4A).

``encode_variants`` stages every requested format in a temporary directory
and only publishes the files beside ``out_stem`` once *all* encodes have
succeeded, so a missing ffmpeg never leaves half-written outputs behind.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Sequence

import soundfile as sf

import config
from engine.result import TTSResult

logger = logging.getLogger(__name__)

ALLOWED_FORMATS = ("wav", "mp3", "m4a")

_FFMPEG_ERROR = (
    "MP3/M4A export requires ffmpeg — install ffmpeg or export WAV only"
)


def encode_variants(
    result: TTSResult,
    out_stem: Path,
    formats: Sequence[str],
    *,
    bitrate: str | None = None,
) -> dict[str, Path]:
    """Encode ``result`` to every format in ``formats`` beside ``out_stem``.

    Each variant is written as ``out_stem.with_suffix(".<ext>")`` and the
    returned dict maps each requested format string to its final path.
    Files only appear at the destination after *all* encodes succeed.

    Args:
        result: In-memory audio (``.wav`` array + ``.sr``).
        out_stem: Destination path without an extension (suffix is replaced).
        formats: Any mix of ``"wav"``, ``"mp3"``, ``"m4a"``.
        bitrate: Bitrate for lossy formats; defaults to
            ``config.AUDIO_BITRATE`` ("128k", env ``RURU_AUDIO_BITRATE``).

    Returns:
        ``{"wav": Path(...), "mp3": Path(...), ...}`` for requested formats
        only, in the order requested.

    Raises:
        ValueError: If any requested format is not in ``ALLOWED_FORMATS``.
        RuntimeError: If a pydub/ffmpeg encode fails (message explains how
            to fix it — install ffmpeg or export WAV only).
    """
    requested = list(formats)
    for fmt in requested:
        if fmt not in ALLOWED_FORMATS:
            raise ValueError(
                f"unsupported export format {fmt!r}; "
                f"allowed: {', '.join(ALLOWED_FORMATS)}"
            )
    if bitrate is None:
        bitrate = config.AUDIO_BITRATE

    stem = Path(out_stem)
    staged: dict[str, Path] = {}

    with tempfile.TemporaryDirectory(prefix="ruru-export-") as tmp:
        tmpdir = Path(tmp)

        # -- encode everything into the staging directory --------------------
        for fmt in requested:
            staged_path = tmpdir / f"out.{fmt}"
            if fmt == "wav":
                sf.write(str(staged_path), result.wav, result.sr, subtype="PCM_16")
            else:
                _encode_lossy(result, staged_path, fmt, bitrate)
            staged[fmt] = staged_path

        # -- all encodes succeeded: publish to the destination --------------
        # shutil.move renames in place on the same filesystem and falls back
        # to copy+delete across devices (e.g. tmpfs /tmp staging vs a
        # disk-backed destination), where os.replace would raise EXDEV.
        stem.parent.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, Path] = {}
        for fmt, staged_path in staged.items():
            dest = stem.with_suffix(f".{fmt}")
            shutil.move(str(staged_path), str(dest))
            outputs[fmt] = dest

    logger.info("Exported: %s", ", ".join(str(p) for p in outputs.values()))
    return outputs


def _encode_lossy(
    result: TTSResult, dest: Path, fmt: str, bitrate: str
) -> None:
    """Encode ``result`` to *fmt* (``mp3``/``m4a``) at *dest* via pydub/ffmpeg.

    Follows the ``TTSResult._encode_mp3`` pattern: WAV buffer ->
    ``AudioSegment.from_file`` -> ``export``. pydub is imported lazily so a
    WAV-only ``encode_variants`` call never requires pydub (or ffmpeg).
    """
    from pydub import AudioSegment
    from pydub.exceptions import CouldntEncodeError

    try:
        buf = BytesIO()
        sf.write(buf, result.wav, result.sr, format="WAV", subtype="PCM_16")
        buf.seek(0)
        segment = AudioSegment.from_file(buf, format="wav")
        # pydub uses "mp4" as the format name for the m4a container
        pydub_fmt = "mp4" if fmt == "m4a" else fmt
        segment.export(str(dest), format=pydub_fmt, bitrate=bitrate)
    except (FileNotFoundError, CouldntEncodeError) as exc:
        raise RuntimeError(_FFMPEG_ERROR) from exc
