"""Tests for engine.get_engine_class() — the backend selection factory."""
from importlib.util import find_spec

import pytest

from engine import BACKENDS, get_engine_class
from engine.base import TTSEngineBase


class TestBackendSelection:
    def test_defaults_to_mlx(self, monkeypatch):
        """Unset RURU_TTS_BACKEND -> MLX, so the Mac's behaviour is unchanged."""
        import config

        monkeypatch.setattr(config, "TTS_BACKEND", "mlx")
        from engine.mlx_engine import MLXEngine

        assert get_engine_class() is MLXEngine

    def test_explicit_mlx(self):
        from engine.mlx_engine import MLXEngine

        assert get_engine_class("mlx") is MLXEngine

    def test_explicit_cuda(self):
        from engine.cuda_engine import CUDAEngine

        assert get_engine_class("cuda") is CUDAEngine

    def test_config_drives_default(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "TTS_BACKEND", "cuda")
        from engine.cuda_engine import CUDAEngine

        assert get_engine_class() is CUDAEngine

    @pytest.mark.parametrize("value", ["", "  ", "rocm", "MLXX", "gpu"])
    def test_unknown_backend_raises_with_a_useful_message(self, value):
        with pytest.raises(ValueError) as exc:
            get_engine_class(value)
        message = str(exc.value)
        assert "RURU_TTS_BACKEND" in message
        for name in BACKENDS:
            assert name in message

    @pytest.mark.parametrize("value", ["MLX", " mlx ", "Cuda"])
    def test_case_and_whitespace_insensitive(self, value):
        assert issubclass(get_engine_class(value), TTSEngineBase)


class TestImportIsolation:
    def test_selecting_mlx_does_not_import_torch(self, monkeypatch):
        """A Mac must never pull in torch just by asking for its own backend."""
        import sys

        monkeypatch.delitem(sys.modules, "torch", raising=False)
        get_engine_class("mlx")
        assert "torch" not in sys.modules

    @pytest.mark.skipif(
        find_spec("mlx_audio") is not None,
        reason="only meaningful on a machine without MLX installed",
    )
    def test_selecting_cuda_does_not_import_mlx(self):
        """A CUDA box must never pull in mlx just by asking for its backend."""
        import sys

        get_engine_class("cuda")
        assert "mlx" not in sys.modules
        assert "mlx_audio" not in sys.modules
