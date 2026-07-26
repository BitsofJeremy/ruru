"""MLX-Audio Qwen3-TTS engine wrapper for ruru (Apple Silicon).

Manages model lifecycle (load / unload) and exposes high-level methods for
voice cloning and voice design using the native Apple-Silicon MLX-Audio
runtime:

    from mlx_audio.tts.utils import load_model
    from mlx_audio.tts.generate import generate_audio

Every ``mlx_audio`` import is method-scoped, so this module imports cleanly on
a machine with no MLX installed (which is what lets the shared contract suite
run on Linux).
"""
from __future__ import annotations

import logging
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Union

import numpy as np
import soundfile as sf

from engine.base import TTSEngineBase
from engine.result import TTSResult

logger = logging.getLogger(__name__)

# 8-bit MLX-Audio model variants (already cached under ~/.cache/huggingface).
CLONE_MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
DESIGN_MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"

# Qwen3-TTS-12Hz codec frame rate (qwen3_tts/config.py: frame_rate=12.5). Used to
# size max_tokens per chunk instead of relying on mlx_audio's fixed default of
# 1200 (~96s), which chunks near ~200 words of narration can hit mid-utterance
# and come back truncated or looping.
FRAME_RATE_HZ = 12.5
ASSUMED_WPM = 150
TOKEN_SAFETY_FACTOR = 1.6

ArrayLike = Union[np.ndarray, "list[float]"]


def _estimate_max_tokens(text: str) -> int:
    """Size a generous max_tokens budget for *text* so generation is never
    starved mid-utterance, regardless of chunk word count."""
    words = len(text.split())
    expected_seconds = (words / ASSUMED_WPM) * 60
    return max(200, int(expected_seconds * FRAME_RATE_HZ * TOKEN_SAFETY_FACTOR))


class MLXEngine(TTSEngineBase):
    """Wraps MLX-Audio Qwen3-TTS model loading and inference.

    Models load **on demand**: the first clone/design call lazily pulls in
    just the model it needs. Call :meth:`load` to pre-warm both.

    Typical usage::

        engine = MLXEngine()
        result = engine.generate_clone("Hello", ref.wav, "transcript")
        result.write("out.mp3")
    """

    def __init__(self) -> None:
        self._clone_model = None
        self._design_model = None
        self._size: str = "auto"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self, size: str = "auto") -> None:
        """Eagerly load both the clone (Base) and design models.

        Generation methods also lazy-load on first use, so calling this is
        optional -- use it to pre-warm before serving requests.
        """
        logger.info("=== MLXEngine.load() called ===")
        self._size = size
        self._load_clone()
        self._load_design()

    def _load_clone(self) -> None:
        if self._clone_model is not None:
            return
        from mlx_audio.tts.utils import load_model

        logger.info(f"  Loading clone model: {CLONE_MODEL_ID}")
        self._clone_model = load_model(CLONE_MODEL_ID)
        logger.info("  Clone model loaded successfully")

    def _load_design(self) -> None:
        if self._design_model is not None:
            return
        from mlx_audio.tts.utils import load_model

        logger.info(f"  Loading design model: {DESIGN_MODEL_ID}")
        self._design_model = load_model(DESIGN_MODEL_ID)
        logger.info("  Design model loaded successfully")

    def unload(self) -> None:
        """Free memory by dropping loaded models."""
        self._clone_model = None
        self._design_model = None
        logger.info("MLX models unloaded")

    @property
    def is_loaded(self) -> bool:
        """True if any model is currently resident in memory."""
        return self._clone_model is not None or self._design_model is not None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_output(out_dir: Path, file_prefix: str, ext: str) -> Path:
        """Locate the file generate_audio wrote (handles joined + chunked)."""
        exact = out_dir / f"{file_prefix}.{ext}"
        if exact.exists():
            return exact
        matches = sorted(out_dir.glob(f"{file_prefix}*.{ext}"))
        if not matches:
            raise RuntimeError(
                f"generate_audio produced no '*.{ext}' file in {out_dir}"
            )
        return matches[-1]

    def _run_generate(self, **kwargs) -> TTSResult:
        """Call generate_audio into a temp dir and return a TTSResult."""
        from mlx_audio.tts.generate import generate_audio

        file_prefix = "ruru_tts"
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            generate_audio(
                output_path=str(out_dir),
                file_prefix=file_prefix,
                audio_format="mp3",
                join_audio=True,
                verbose=True,
                **kwargs,
            )
            mp3_file = self._find_output(out_dir, file_prefix, "mp3")
            mp3_bytes = mp3_file.read_bytes()

        # Decode to a numpy array for callers that want raw wav.
        wav, sr = sf.read(BytesIO(mp3_bytes), always_2d=False)
        return TTSResult(wav=wav, sr=sr, mp3_bytes=mp3_bytes)

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_clone(
        self,
        text: str,
        ref_audio_path: str | Path,
        ref_text: str,
        language: str = "English",
    ) -> TTSResult:
        """Clone a reference voice (Qwen3-TTS Base model) and synthesise ``text``.

        Args:
            text: Text to synthesise.
            ref_audio_path: Path to the reference WAV (the voice to clone).
            ref_text: Transcript of the reference audio, or a path to a .txt
                containing it.
            language: Language tag forwarded as ``lang_code``.

        Returns:
            A :class:`TTSResult` (tuple-unpackable as ``(wav, sr)``).
        """
        logger.info("=== MLXEngine.generate_clone() called ===")
        logger.info(f"  text: {text[:80]!r}")
        logger.info(f"  ref_audio: {ref_audio_path}")

        ref_audio = Path(ref_audio_path)
        if not ref_audio.exists():
            raise FileNotFoundError(f"Reference audio not found: {ref_audio}")

        try:
            self._load_clone()
            resolved_ref_text = self._resolve_ref_text(ref_text)
            result = self._run_generate(
                model=self._clone_model,
                text=text,
                lang_code=language,
                ref_audio=str(ref_audio),
                ref_text=resolved_ref_text,
                max_tokens=_estimate_max_tokens(text),
            )
            logger.info(
                f"  Speech generated: {len(result.wav)} samples @ {result.sr} Hz"
            )
            return result
        except Exception as e:
            logger.error(f"  Clone generation failed: {e}")
            raise RuntimeError(f"Failed to generate cloned speech: {e}") from e

    def generate_design(
        self,
        text: str,
        voice_description: str,
        language: str = "English",
    ) -> TTSResult:
        """Synthesise ``text`` with a VoiceDesign model using a text description.

        Args:
            text: Text to synthesise.
            voice_description: Natural-language description of the desired voice,
                forwarded as ``instruct``.
            language: Language tag forwarded as ``lang_code``.

        Returns:
            A :class:`TTSResult` (tuple-unpackable as ``(wav, sr)``).
        """
        logger.info("=== MLXEngine.generate_design() called ===")
        logger.info(f"  text: {text[:80]!r}")
        logger.info(f"  design: {voice_description[:80]!r}")

        try:
            self._load_design()
            result = self._run_generate(
                model=self._design_model,
                text=text,
                lang_code=language,
                instruct=voice_description,
                max_tokens=_estimate_max_tokens(text),
            )
            logger.info(
                f"  Speech generated: {len(result.wav)} samples @ {result.sr} Hz"
            )
            return result
        except Exception as e:
            logger.error(f"  Design generation failed: {e}")
            raise RuntimeError(f"Failed to generate designed speech: {e}") from e

    # ------------------------------------------------------------------
    # Compatibility shims
    # ------------------------------------------------------------------

    def create_clone_prompt(
        self,
        ref_audio_path: str | Path,
        ref_text: str,
    ) -> tuple[str, str]:
        """No-op placeholder retained for API compatibility.

        MLX-Audio performs voice cloning inline during generation (``ref_audio``
        and ``ref_text`` are passed straight to ``generate_audio``), so there is
        no separate prompt object to build. Returns the resolved inputs.
        """
        return (str(ref_audio_path), self._resolve_ref_text(ref_text))
