"""TTS backend selection.

The single place that knows both backend classes exist. Concrete classes are
imported *inside* :func:`get_engine_class` so a Mac never imports ``torch`` and
a CUDA box never imports ``mlx``.
"""
from __future__ import annotations

BACKENDS = ("mlx", "cuda")


def get_engine_class(backend: str | None = None) -> type:
    """Return the engine class for *backend*.

    Defaults to ``config.TTS_BACKEND`` (``RURU_TTS_BACKEND``, default ``mlx``).
    """
    if backend is None:
        import config

        backend = config.TTS_BACKEND

    normalized = (backend or "").strip().lower()

    if normalized == "mlx":
        from engine.mlx_engine import MLXEngine

        return MLXEngine
    if normalized == "cuda":
        from engine.cuda_engine import CUDAEngine

        return CUDAEngine

    raise ValueError(
        f"Unknown TTS backend {backend!r}. "
        f"Set RURU_TTS_BACKEND to one of: {', '.join(BACKENDS)}."
    )
