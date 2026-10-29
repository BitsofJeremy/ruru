"""Phonon live-TTS engine — optional, CPU-only, playback-only.

Deliberately NOT a ``TTSEngineBase`` subclass: the base contract is wired to
the voice DB and the job queue (``generate_clone``/``generate_design``), which
is exactly the machinery live TTS must not touch. Phonon generations create no
Job row, no Voice row, and write nothing under OUTPUT_DIR — they exist only
long enough to stream back to the caller.

``ptts`` is an optional dependency (the ``phonon`` extra, a torch-free Rust
wheel). Every import is funneled through :func:`_import_ptts`, so a box
without the wheel behaves identically to one with ``RURU_PHONON=0``.
"""
from __future__ import annotations

import io
import logging
import threading
from typing import Iterator, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Only the Apache-2.0 31m family may be loaded; the 40m family is CC BY-NC.
_FORBIDDEN_MODEL_MARKERS = ("phonon-40m",)

_import_ok: Optional[bool] = None  # cached import probe (None = not probed)
_import_lock = threading.Lock()
_handle = None  # cached ptts.TTS instance
_handle_lock = threading.Lock()
# Serializes generation across requests (handlers run via asyncio.to_thread).
# At ~6x realtime the queue drains fast; this just keeps CPU sane under load.
_gen_lock = threading.Lock()


def _import_ptts():
    """Import ptts lazily, caching the probe result. Returns module or None.

    Module-scoped so a Mac or plain install never touches ptts (same pattern
    as the cuda engine's method-scoped imports).
    """
    global _import_ok
    if _import_ok is None:
        with _import_lock:
            if _import_ok is None:
                try:
                    import ptts  # noqa: F401
                    _import_ok = True
                except Exception:
                    logger.info("ptts not importable — Phonon live TTS disabled")
                    _import_ok = False
    if not _import_ok:
        return None
    import ptts

    return ptts


def reset_cache() -> None:
    """Forget the import probe and any cached handle (test hook)."""
    global _import_ok, _handle
    with _import_lock:
        _import_ok = None
    with _handle_lock:
        _handle = None


def phonon_available() -> bool:
    """True when live TTS can serve: not switched off and ptts importable.

    The mode is re-read on every call (tests flip it via monkeypatch); only
    the import probe is cached.
    """
    import config

    if (config.PHONON_MODE or "auto").strip().lower() == "0":
        return False
    return _import_ptts() is not None


def _check_model(model: str) -> str:
    """Refuse non-Apache repos up front (the 40m family is CC BY-NC)."""
    lowered = (model or "").lower()
    for marker in _FORBIDDEN_MODEL_MARKERS:
        if marker in lowered:
            raise ValueError(
                f"Phonon model {model!r} is not permitted (noncommercial "
                "license). Use the Apache-2.0 phonon-31m model."
            )
    return model


def _load_handle():
    """Build (once) and return the shared ptts.TTS instance."""
    global _handle
    if _handle is not None:
        return _handle
    with _handle_lock:
        if _handle is not None:
            return _handle
        ptts = _import_ptts()
        if ptts is None:
            raise RuntimeError("ptts is not installed")
        import config

        if config.PHONON_THREADS > 0:
            # Sizes a global thread pool that is built once — must land
            # BEFORE the first TTS() construction.
            ptts.set_num_threads(int(config.PHONON_THREADS))
        model = _check_model(config.PHONON_MODEL)
        _handle = ptts.TTS(model, lang=config.PHONON_LANG, quant=config.PHONON_QUANT)
        return _handle


def voices() -> list[dict]:
    """Canned catalog voices as ``{name, voice_id, language, description}``,
    sorted by language then name. Empty when the feature is unavailable."""
    ptts = _import_ptts()
    if ptts is None:
        return []
    rows = ptts.catalog_voices()
    return sorted(rows, key=lambda r: (r.get("language") or "", r.get("name") or ""))


def voice_exists(voice: str) -> bool:
    """True when ``voice`` matches a catalog name or voice_id."""
    if not voice:
        return False
    for row in voices():
        if voice in (row.get("name"), row.get("voice_id")):
            return True
    return False


def generate_stream(text: str, voice: str) -> Iterator[tuple[np.ndarray, int]]:
    """Yield ``(float32 chunk, sample_rate)`` pairs as the decoder produces
    them (wraps ``ptts.TTS.stream``)."""
    handle = _load_handle()
    with _gen_lock:
        stream = handle.stream(text, voice=voice)
        try:
            for chunk in stream:
                yield chunk, int(stream.sample_rate)
        finally:
            stream.close()


def generate_wav_bytes(text: str, voice: str) -> bytes:
    """Synthesize to a complete 16-bit PCM WAV in memory (blob path)."""
    import soundfile as sf

    handle = _load_handle()
    with _gen_lock:
        wav = handle.synth(text, voice=voice)
    sr = int(handle.sample_rate)
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def warm() -> bool:
    """Pre-load the model (lifespan background thread). Log-only, never raises."""
    try:
        _load_handle()
        return True
    except Exception:
        logger.warning("Phonon warm-up failed; will retry on demand", exc_info=True)
        return False
