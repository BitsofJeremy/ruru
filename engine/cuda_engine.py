"""Qwen3-TTS engine wrapper for NVIDIA/CUDA (ruru's Linux backend).

Ported from WeirDing's `engine/tts.py`, with three deliberate changes:

1. Returns :class:`~engine.result.TTSResult`, not bare ``(wav, sr)`` tuples,
   so it satisfies the same contract as the MLX engine (both tuple-unpacking
   and ``.write(path)`` call sites in ``jobs/tasks.py``).
2. Holds **one model resident at a time**. Loading the clone model evicts the
   design model and vice versa. Two bf16 1.7B models want ~7 GB, and this box
   shares its 12 GB card with other workloads.
3. Caches the voice-clone prompt. ``jobs/tasks.py`` calls ``generate_clone``
   once per chunk, and building that prompt is the expensive per-voice GPU op.

``torch`` and ``qwen_tts`` imports are method-scoped, so importing this module
on a machine with no CUDA is harmless — which is what lets the shared contract
suite run on the Mac.
"""
from __future__ import annotations

import gc
import logging
from pathlib import Path

import numpy as np

from engine.base import TTSEngineBase
from engine.result import TTSResult

logger = logging.getLogger(__name__)

CLONE_MODEL_ID_LARGE = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
CLONE_MODEL_ID_SMALL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
DESIGN_MODEL_ID_LARGE = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DESIGN_MODEL_ID_SMALL = "Qwen/Qwen3-TTS-12Hz-0.6B-VoiceDesign"

DEVICE = "cuda:0"


class CUDAEngine(TTSEngineBase):
    """Wraps Qwen3-TTS model loading and inference on an NVIDIA GPU.

    Models load **on demand**: the first clone/design call pulls in just the
    model it needs, evicting the other. Call :meth:`load` to pre-warm.

    Typical usage::

        engine = CUDAEngine()
        result = engine.generate_clone("Hello", ref.wav, "transcript")
        result.write("out.mp3")
    """

    def __init__(self) -> None:
        self._clone_model = None
        self._design_model = None
        self._size: str = "auto"
        self._clone_prompt = None
        self._clone_prompt_key: tuple | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self, size: str = "auto") -> None:
        """Pre-warm the clone model.

        Only the clone model is loaded here, on purpose — see the class
        docstring on the single-resident-model policy. The design model loads
        on its first use, evicting this one.
        """
        logger.info("=== CUDAEngine.load() called ===")
        self._size = size
        self._require_cuda()
        self._load_clone()

    @staticmethod
    def _require_cuda():
        """Hard gate. Fail loudly rather than silently producing silence."""
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is not available. CUDAEngine requires an NVIDIA GPU. "
                "Set RURU_TTS_BACKEND=mlx to use the Apple Silicon backend."
            )
        return torch

    def _use_large(self) -> bool:
        """Always the 1.7B pair unless "small" was asked for explicitly.

        Deliberately *not* WeirDing's total-VRAM auto-detect: total VRAM says
        nothing about what is free right now, so it silently picks a model that
        cannot fit. Log what is actually free and let an OOM be a real error.
        """
        return self._size != "small"

    def _log_free_vram(self, torch) -> None:
        try:
            free_b, total_b = torch.cuda.mem_get_info(0)
            logger.info(
                "  VRAM: %.2f GB free of %.2f GB total",
                free_b / 1024 ** 3, total_b / 1024 ** 3,
            )
        except Exception as e:  # pragma: no cover - diagnostics only
            logger.debug(f"  Could not read VRAM info: {e}")

    @staticmethod
    def _resolve_model_path(model_id: str) -> str:
        """Resolve *model_id* to a local snapshot directory when it's cached.

        `qwen_tts.from_pretrained` is a thin wrapper over
        `AutoModel.from_pretrained(repo_id)`, which contacts huggingface.co on
        every load — so a network outage takes TTS down even though the weights
        are already on disk. Passing a filesystem path instead never touches
        the network.

        Falls back to the bare repo id when the model isn't cached yet, so a
        first-time download still works.
        """
        try:
            from huggingface_hub import snapshot_download

            path = snapshot_download(model_id, local_files_only=True)
            logger.info(f"  Using cached snapshot: {path}")
            return path
        except Exception as e:
            logger.info(f"  No local snapshot for {model_id} ({e}); will fetch.")
            return model_id

    def _from_pretrained(self, model_id: str):
        torch = self._require_cuda()
        from qwen_tts import Qwen3TTSModel

        self._log_free_vram(torch)
        logger.info(f"  Loading model: {model_id}")
        model = Qwen3TTSModel.from_pretrained(
            self._resolve_model_path(model_id), device_map=DEVICE, dtype=torch.bfloat16,
        )
        logger.info("  Model loaded successfully")
        return model

    def _evict(self, which: str) -> None:
        """Drop one resident model and return its VRAM to the allocator."""
        attr = f"_{which}_model"
        if getattr(self, attr) is None:
            return
        logger.info(f"  Evicting {which} model to free VRAM")
        setattr(self, attr, None)
        if which == "clone":
            self._clone_prompt = None
            self._clone_prompt_key = None
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _load_clone(self) -> None:
        if self._clone_model is not None:
            return
        self._evict("design")
        self._clone_model = self._from_pretrained(
            CLONE_MODEL_ID_LARGE if self._use_large() else CLONE_MODEL_ID_SMALL
        )

    def _load_design(self) -> None:
        if self._design_model is not None:
            return
        self._evict("clone")
        self._design_model = self._from_pretrained(
            DESIGN_MODEL_ID_LARGE if self._use_large() else DESIGN_MODEL_ID_SMALL
        )

    def unload(self) -> None:
        """Free GPU memory by dropping loaded models."""
        self._evict("clone")
        self._evict("design")
        logger.info("CUDA models unloaded")

    @property
    def is_loaded(self) -> bool:
        """True if any model is currently resident in VRAM."""
        return self._clone_model is not None or self._design_model is not None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_clone_prompt(self, ref_audio: Path, ref_text: str):
        """Build the voice-clone prompt, reusing it across chunks.

        ``jobs/tasks.py`` calls generate_clone once per chunk with the same
        reference voice; rebuilding this every call is the single biggest
        avoidable cost in a narration job.
        """
        try:
            mtime = ref_audio.stat().st_mtime
        except OSError:
            mtime = None
        key = (str(ref_audio), mtime, ref_text)

        if self._clone_prompt is not None and self._clone_prompt_key == key:
            logger.info("  Reusing cached voice clone prompt")
            return self._clone_prompt

        logger.info("  Building voice clone prompt (one-time per voice)")
        self._clone_prompt = self._clone_model.create_voice_clone_prompt(
            ref_audio=str(ref_audio),
            ref_text=ref_text,
        )
        self._clone_prompt_key = key
        return self._clone_prompt

    @staticmethod
    def _as_result(wavs, sr) -> TTSResult:
        """Unwrap qwen_tts's ``(list[ndarray], sr)`` into a TTSResult.

        Returning the raw tuple here is the porting mistake the contract suite
        exists to catch: it satisfies the unpacking call sites and then blows
        up at ``result.write(...)``.
        """
        wav = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
        return TTSResult(wav=np.asarray(wav), sr=int(sr))

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
            language: Language tag.

        Returns:
            A :class:`TTSResult` (tuple-unpackable as ``(wav, sr)``).
        """
        logger.info("=== CUDAEngine.generate_clone() called ===")
        logger.info(f"  text: {text[:80]!r}")
        logger.info(f"  ref_audio: {ref_audio_path}")

        ref_audio = Path(ref_audio_path)
        if not ref_audio.exists():
            raise FileNotFoundError(f"Reference audio not found: {ref_audio}")

        try:
            self._load_clone()
            prompt = self._get_clone_prompt(
                ref_audio, self._resolve_ref_text(ref_text)
            )
            wavs, sr = self._clone_model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=prompt,
            )
            result = self._as_result(wavs, sr)
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
            voice_description: Natural-language description of the desired
                voice, forwarded as ``instruct``.
            language: Language tag.

        Returns:
            A :class:`TTSResult` (tuple-unpackable as ``(wav, sr)``).
        """
        logger.info("=== CUDAEngine.generate_design() called ===")
        logger.info(f"  text: {text[:80]!r}")
        logger.info(f"  design: {voice_description[:80]!r}")

        try:
            self._load_design()
            wavs, sr = self._design_model.generate_voice_design(
                text=text,
                instruct=voice_description,
                language=language,
            )
            result = self._as_result(wavs, sr)
            logger.info(
                f"  Speech generated: {len(result.wav)} samples @ {result.sr} Hz"
            )
            return result
        except Exception as e:
            logger.error(f"  Design generation failed: {e}")
            raise RuntimeError(f"Failed to generate designed speech: {e}") from e

    # ------------------------------------------------------------------
    # CUDA-only extras (not part of the shared contract)
    # ------------------------------------------------------------------

    def create_clone_prompt(self, ref_audio_path: str | Path, ref_text: str):
        """Build a voice clone prompt eagerly.

        ``generate_clone`` already caches this internally, so callers don't
        need it — kept for parity with WeirDing's API.
        """
        self._load_clone()
        return self._get_clone_prompt(
            Path(ref_audio_path), self._resolve_ref_text(ref_text)
        )

    def generate_clone_with_prompt(
        self,
        text: str,
        voice_clone_prompt,
        language: str = "English",
    ) -> TTSResult:
        """Generate using a pre-built prompt, skipping prompt construction."""
        if self._clone_model is None:
            raise RuntimeError("Clone model not loaded. Call load() first.")
        wavs, sr = self._clone_model.generate_voice_clone(
            text=text,
            language=language,
            voice_clone_prompt=voice_clone_prompt,
        )
        return self._as_result(wavs, sr)
