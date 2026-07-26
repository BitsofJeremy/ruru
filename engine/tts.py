"""Backwards-compatible re-export shim.

The MLX engine moved to ``engine/mlx_engine.py`` when the backend abstraction
landed. This module keeps the old ``engine.tts.TTSEngine`` name importable.

New code should use the factory instead::

    from engine import get_engine_class
    engine = get_engine_class()()
"""
from engine.mlx_engine import (  # noqa: F401
    ASSUMED_WPM,
    CLONE_MODEL_ID,
    DESIGN_MODEL_ID,
    FRAME_RATE_HZ,
    TOKEN_SAFETY_FACTOR,
    MLXEngine,
    MLXEngine as TTSEngine,
    _estimate_max_tokens,
)
from engine.result import TTSResult  # noqa: F401

__all__ = ["TTSEngine", "MLXEngine", "TTSResult"]
