"""The shared backend contract.

This is what makes cross-machine development safe: it proves that a backend
written on the Linux/CUDA box drops cleanly into the call sites the Mac's MLX
production install exercises, and vice versa.

Every backend class is checked here — the module imports on any platform
because both engines keep their heavyweight imports method-scoped. Tests that
need a *live* model are the ones that get skipped.
"""
from __future__ import annotations

import inspect
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from engine import BACKENDS, get_engine_class
from engine.base import TTSEngineBase
from engine.result import TTSResult

# Every backend class, imported without touching its runtime deps.
ALL_BACKENDS = [pytest.param(name, id=name) for name in BACKENDS]

# The de-facto contract from docs/nvidia_feasibility.md §2.
GENERATE_CLONE_PARAMS = ["self", "text", "ref_audio_path", "ref_text", "language"]
GENERATE_DESIGN_PARAMS = ["self", "text", "voice_description", "language"]


@pytest.fixture(params=ALL_BACKENDS)
def engine_cls(request):
    return get_engine_class(request.param)


class TestABCSurface:
    def test_subclasses_the_base(self, engine_cls):
        assert issubclass(engine_cls, TTSEngineBase)

    def test_is_instantiable(self, engine_cls):
        """No abstract methods left unimplemented, and __init__ takes no args."""
        assert engine_cls() is not None

    @pytest.mark.parametrize(
        "name", ["load", "unload", "generate_clone", "generate_design", "generate_to_file"]
    )
    def test_method_present_and_callable(self, engine_cls, name):
        assert callable(getattr(engine_cls, name))

    def test_is_loaded_is_a_property_not_a_method(self, engine_cls):
        """jobs/tasks.py and the routes read `engine.is_loaded` without calling
        it — a method here would silently be truthy forever."""
        assert isinstance(inspect.getattr_static(engine_cls, "is_loaded"), property)

    def test_engine_is_synchronous(self, engine_cls):
        """docs/nvidia_feasibility.md §4.1 — the job runner already wraps these
        in asyncio.to_thread(); an async engine would double the ceremony."""
        for name in ("load", "unload", "generate_clone", "generate_design"):
            assert not inspect.iscoroutinefunction(getattr(engine_cls, name)), name

    def test_does_not_grow_db_concerns(self, engine_cls):
        """docs/nvidia_feasibility.md §4.2 — voice inventory belongs to
        models/db.py, not the engine."""
        assert not hasattr(engine_cls, "list_voices")
        assert not hasattr(engine_cls, "validate_voice")


class TestSignatures:
    def test_load_signature(self, engine_cls):
        sig = inspect.signature(engine_cls.load)
        assert list(sig.parameters) == ["self", "size"]
        assert sig.parameters["size"].default == "auto"

    def test_generate_clone_signature(self, engine_cls):
        sig = inspect.signature(engine_cls.generate_clone)
        assert list(sig.parameters) == GENERATE_CLONE_PARAMS
        assert sig.parameters["language"].default == "English"

    def test_generate_design_signature(self, engine_cls):
        sig = inspect.signature(engine_cls.generate_design)
        assert list(sig.parameters) == GENERATE_DESIGN_PARAMS
        assert sig.parameters["language"].default == "English"

    def test_generate_to_file_keeps_keyword_only_options(self, engine_cls):
        sig = inspect.signature(engine_cls.generate_to_file)
        assert list(sig.parameters)[:3] == ["self", "text", "output_path"]
        for name in ("voice_description", "ref_audio_path", "ref_text", "language"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


class TestResultContract:
    """`TTSResult` is the shared currency — both call styles must keep working.

    `jobs/tasks.py` unpacks it in some places, reads `.wav`/`.sr` in others
    (`:167`), and calls `.write()` in all three handlers.
    """

    @pytest.fixture
    def result(self):
        return TTSResult(np.zeros(2400, dtype=np.float32), 24000)

    def test_tuple_unpackable(self, result):
        wav, sr = result
        assert isinstance(wav, np.ndarray)
        assert sr == 24000

    def test_indexable_and_sized(self, result):
        assert len(result) == 2
        assert result[1] == 24000

    def test_attribute_access(self, result):
        """jobs/tasks.py:167 does len(candidate.wav) / candidate.sr."""
        assert len(result.wav) / result.sr == pytest.approx(0.1)

    def test_writes_wav(self, result, tmp_path):
        out = result.write(tmp_path / "nested" / "out.wav")
        assert out.exists() and out.stat().st_size > 0

    def test_writes_mp3_from_supplied_bytes_without_reencoding(self, tmp_path):
        """The MLX path: mp3 bytes arrive pre-encoded and pass straight through."""
        result = TTSResult(np.zeros(2400, dtype=np.float32), 24000, b"ID3-original")
        out = result.write(tmp_path / "out.mp3")
        assert out.read_bytes() == b"ID3-original"

    def test_encodes_mp3_on_demand_when_backend_supplied_none(self, result, tmp_path):
        """The CUDA path: raw arrays only, so .write('.mp3') must encode."""
        out = result.write(tmp_path / "out.mp3")
        assert out.exists() and out.stat().st_size > 0


class TestGenerationReturnsAContractingResult:
    """Both generators must return something tuple-unpackable *and* writable.

    Returning a bare ``(wav, sr)`` tuple is the single most likely porting
    mistake — it satisfies the unpacking call sites and blows up at
    ``result.write(...)``.
    """

    def _assert_contract(self, value, tmp_path):
        wav, sr = value                     # tuple-unpackable
        assert isinstance(wav, np.ndarray)
        assert isinstance(sr, int) and sr > 0
        assert isinstance(value.wav, np.ndarray)   # attribute access
        assert isinstance(value.sr, int)
        out = value.write(tmp_path / "contract.wav")  # writable
        assert Path(out).exists()

    def test_mlx_generate_clone(self, tmp_path):
        pytest.importorskip("mlx_audio")
        import soundfile as sf

        ref = tmp_path / "ref.wav"
        sf.write(str(ref), np.zeros(2400, dtype=np.float32), 24000)

        def _fake_generate_audio(**kwargs):
            sf.write(
                f"{kwargs['output_path']}/{kwargs['file_prefix']}.mp3",
                np.zeros(2400, dtype=np.float32), 24000, format="WAV",
            )

        with patch("mlx_audio.tts.utils.load_model", return_value=MagicMock()), \
             patch("mlx_audio.tts.generate.generate_audio", side_effect=_fake_generate_audio):
            engine = get_engine_class("mlx")()
            self._assert_contract(engine.generate_clone("hi", ref, "ref"), tmp_path)

    @pytest.mark.skipif(find_spec("qwen_tts") is None, reason="qwen_tts not installed")
    def test_cuda_generate_clone(self, tmp_path):
        import soundfile as sf

        ref = tmp_path / "ref.wav"
        sf.write(str(ref), np.zeros(2400, dtype=np.float32), 24000)

        engine = get_engine_class("cuda")()
        model = MagicMock()
        model.generate_voice_clone.return_value = ([np.zeros(2400, dtype=np.float32)], 24000)
        with patch.object(engine, "_load_clone"):
            engine._clone_model = model
            self._assert_contract(engine.generate_clone("hi", ref, "ref"), tmp_path)

    @pytest.mark.skipif(find_spec("qwen_tts") is None, reason="qwen_tts not installed")
    def test_cuda_generate_design(self, tmp_path):
        engine = get_engine_class("cuda")()
        model = MagicMock()
        model.generate_voice_design.return_value = ([np.zeros(2400, dtype=np.float32)], 24000)
        with patch.object(engine, "_load_design"):
            engine._design_model = model
            self._assert_contract(engine.generate_design("hi", "a warm voice"), tmp_path)


class TestCUDAOfflineModelResolution:
    """A cached model must load with no network.

    qwen_tts wraps `AutoModel.from_pretrained(repo_id)`, which hits
    huggingface.co on every load — so an outage took TTS down even though all
    the weights were on disk. CUDAEngine resolves the repo to its local
    snapshot path first.
    """

    @pytest.mark.skipif(find_spec("qwen_tts") is None, reason="qwen_tts not installed")
    def test_uses_cached_snapshot_path_when_available(self):
        cuda_engine = pytest.importorskip("engine.cuda_engine")
        with patch(
            "huggingface_hub.snapshot_download", return_value="/cache/snap"
        ) as dl:
            assert cuda_engine.CUDAEngine._resolve_model_path("Qwen/whatever") == "/cache/snap"
        # local_files_only is the whole point — without it this still calls out.
        assert dl.call_args.kwargs.get("local_files_only") is True

    @pytest.mark.skipif(find_spec("qwen_tts") is None, reason="qwen_tts not installed")
    def test_falls_back_to_repo_id_when_not_cached(self):
        """A first-time download must still work."""
        cuda_engine = pytest.importorskip("engine.cuda_engine")
        with patch("huggingface_hub.snapshot_download", side_effect=OSError("not cached")):
            assert cuda_engine.CUDAEngine._resolve_model_path("Qwen/whatever") == "Qwen/whatever"
