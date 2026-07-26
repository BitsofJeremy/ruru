"""Abstract base class shared by every ruru TTS backend.

Deliberately **synchronous**: the job runner already pushes handlers through
``asyncio.to_thread()`` (``jobs/queue.py:74``), so an async engine would just
add a second layer of executor ceremony around GPU-bound work.

Deliberately *without* ``list_voices`` / ``validate_voice``: voice inventory is
a database concern and lives in ``models/db.py``.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from engine.result import TTSResult

logger = logging.getLogger(__name__)


class TTSEngineBase(ABC):
    """The contract every backend (`MLXEngine`, `CUDAEngine`) satisfies."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def load(self, size: str = "auto") -> None:
        """Pre-warm models. Generation methods also lazy-load on first use."""

    @abstractmethod
    def unload(self) -> None:
        """Free memory by dropping loaded models."""

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """True if any model is currently resident in memory."""

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    @abstractmethod
    def generate_clone(
        self,
        text: str,
        ref_audio_path: str | Path,
        ref_text: str,
        language: str = "English",
    ) -> TTSResult:
        """Clone the voice in ``ref_audio_path`` and synthesise ``text``."""

    @abstractmethod
    def generate_design(
        self,
        text: str,
        voice_description: str,
        language: str = "English",
    ) -> TTSResult:
        """Synthesise ``text`` in a voice described by ``voice_description``."""

    # ------------------------------------------------------------------
    # Shared concrete helpers — identical across backends
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_ref_text(ref_text: str) -> str:
        """Accept ref_text as either a literal transcript or a path to a .txt."""
        try:
            candidate = Path(ref_text)
            if candidate.is_file():
                return candidate.read_text().strip()
        except OSError:
            # ref_text is a literal transcript (e.g. too long to be a filename).
            pass
        return ref_text

    def generate_to_file(
        self,
        text: str,
        output_path: str | Path,
        *,
        voice_description: str | None = None,
        ref_audio_path: str | Path | None = None,
        ref_text: str | None = None,
        language: str = "English",
    ) -> Path:
        """Generate speech and write it directly to ``output_path``.

        Picks clone mode when ``ref_audio_path`` + ``ref_text`` are supplied,
        otherwise design mode when ``voice_description`` is supplied. Extension
        of ``output_path`` decides the container (``.mp3`` or ``.wav``).

        Returns:
            The resolved :class:`Path` to the written file.
        """
        if ref_audio_path is not None and ref_text is not None:
            result = self.generate_clone(text, ref_audio_path, ref_text, language)
        elif voice_description is not None:
            result = self.generate_design(text, voice_description, language)
        else:
            raise ValueError(
                "Provide either ref_audio_path + ref_text (clone) or "
                "voice_description (design)."
            )
        return result.write(output_path)
