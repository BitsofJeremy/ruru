"""YuE2Engine tests — GPU never touched: subprocess and pipeline are faked."""
import numpy as np
import pytest
import soundfile as sf

from engine.yue2_engine import YuE2Engine


@pytest.fixture
def fake_wav(tmp_path):
    path = tmp_path / "out.wav"
    sf.write(str(path), np.zeros(48000, dtype=np.float32), 48000, subtype="PCM_16")
    return path


@pytest.fixture
def linux_platform(monkeypatch):
    """Pretend we're on Linux — the gguf auto-default and the mlx rejection
    are platform-gated. A no-op when actually running on Linux."""
    monkeypatch.setattr("engine.yue2_engine.platform.system", lambda: "Linux")


def test_auto_runtime_is_gguf_on_linux(monkeypatch, linux_platform):
    monkeypatch.setattr("config.YUE2_RUNTIME", "")
    eng = YuE2Engine()
    assert eng._resolve_runtime() == "gguf"


def test_auto_runtime_is_mlx_on_mac(monkeypatch):
    monkeypatch.setattr("config.YUE2_RUNTIME", "")
    monkeypatch.setattr(
        "engine.yue2_engine.platform.system", lambda: "Darwin"
    )
    eng = YuE2Engine()
    assert eng._resolve_runtime() == "mlx"


def test_instrumental_forces_pytorch_runtime(monkeypatch):
    """Instrumental LoRA only exists for pytorch — auto-resolves there."""
    import tempfile as _tempfile
    import contextlib

    monkeypatch.setattr("config.YUE2_RUNTIME", "gguf")
    monkeypatch.setattr("config.YUE2_INST_LORA_SCALE", 1.0)
    monkeypatch.setattr("config.YUE2_MAX_SECONDS", 90)
    eng = YuE2Engine()
    merged = {}
    real_mkdtemp = _tempfile.mkdtemp

    def fake_save(out_path):
        # the pipeline "writes" the flac the engine reads back
        import soundfile as _sf
        _sf.write(str(out_path), np.zeros(4800, dtype="float32"), 48000, format="WAV")

    monkeypatch.setattr(
        eng, "_load_pytorch_pipeline",
        lambda with_lora: merged.update(lora=with_lora) or (lambda **kw: type("S", (), {"save": staticmethod(fake_save)})()),
    )
    monkeypatch.setattr(eng, "_read_result_wav", lambda p: (np.zeros(1), 48000))

    @contextlib.contextmanager
    def fake_td(*a, **kw):
        yield real_mkdtemp()

    monkeypatch.setattr("engine.yue2_engine.tempfile.TemporaryDirectory", fake_td)
    monkeypatch.setattr(
        "engine.yue2_engine.YuE2Engine._cap_lyrics",
        staticmethod(lambda lyrics, cap_seconds: lyrics),
    )
    result = eng.generate(style="s", lyrics="[instrumental]", instrumental=True)
    assert result is not None
    assert merged["lora"] is True


def test_gguf_runtime_runs_subprocess(monkeypatch, linux_platform, fake_wav):
    eng = YuE2Engine()
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr("config.YUE2_AUDIOCPP_BIN", "/fake/bin/audiocpp")
    monkeypatch.setattr("config.YUE2_GGUF_DIR", fake_wav.parent)
    # make the "model file exists" check pass
    (fake_wav.parent / "yue2-3b-q4_k_m.gguf").write_bytes(b"x")
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(eng, "_gguf_output_target", lambda d: fake_wav)
    result = eng.generate(style="dark ambient", lyrics="[instrumental]")
    wav, sr = result            # TTSResult must unpack
    assert sr == 48000
    assert len(wav) == 48000
    assert "/fake/bin/audiocpp" in " ".join(str(c) for c in calls["cmd"])


def test_gguf_missing_binary_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr("config.YUE2_AUDIOCPP_BIN", "")
    monkeypatch.setattr("config.YUE2_GGUF_DIR", tmp_path)
    (tmp_path / "yue2-3b-q4_k_m.gguf").write_bytes(b"x")
    # explicit gguf selection: operator asked for it, no fallback
    monkeypatch.setattr("config.YUE2_RUNTIME", "gguf")
    eng = YuE2Engine()
    with pytest.raises(RuntimeError, match="RURU_YUE2_AUDIOCPP_BIN"):
        eng.generate(style="s", lyrics="[instrumental]")


def test_gguf_unready_falls_back_to_pytorch(monkeypatch, mocker, linux_platform):
    """Auto-resolved gguf with no binary/weights silently falls back to pytorch
    (explicit gguf via RURU_YUE2_RUNTIME still raises — that's a deliberate
    operator choice)."""
    import sys
    import types

    monkeypatch.setattr("config.YUE2_RUNTIME", "")   # auto -> gguf on linux
    monkeypatch.setattr("config.YUE2_AUDIOCPP_BIN", "")  # not configured
    eng = YuE2Engine()
    assert eng._resolve_runtime() == "gguf"

    fake_pipe = mocker.MagicMock()
    fake_song = mocker.MagicMock()
    fake_song.save = mocker.MagicMock()
    fake_pipe.side_effect = lambda **kw: fake_song
    fake_yue2 = types.ModuleType("yue2")
    fake_yue2.YuE2Pipeline = mocker.MagicMock()
    fake_yue2.YuE2Pipeline.from_pretrained.return_value = fake_pipe
    monkeypatch.setitem(sys.modules, "yue2", fake_yue2)
    monkeypatch.setattr(eng, "_read_result_wav", lambda p: (np.zeros(1), 48000))

    def fake_save_real(out_path):
        import soundfile as _sf
        _sf.write(str(out_path), np.zeros(4800, dtype="float32"), 48000, format="WAV")

    fake_song.save.side_effect = fake_save_real
    result = eng.generate(style="s", lyrics="[verse]")
    assert result is not None  # reached pytorch, no exception


def test_gguf_missing_model_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr("config.YUE2_AUDIOCPP_BIN", "/fake/bin/audiocpp")
    monkeypatch.setattr("config.YUE2_GGUF_DIR", tmp_path)
    # explicit gguf selection: no fallback even when weights are missing
    monkeypatch.setattr("config.YUE2_RUNTIME", "gguf")
    eng = YuE2Engine()
    with pytest.raises(RuntimeError, match="missing"):
        eng.generate(style="s", lyrics="[instrumental]")


def test_pytorch_lora_flag_selects_merge(monkeypatch, mocker):
    """Instrumental=True drives the real pipeline-load path, which invokes the
    LoRA fold. We mock _apply_instrumental_lora's *internals* (ckpt load +
    merge) rather than the pipeline loader, so we verify the wiring end to end."""
    import sys
    import tempfile as _tempfile
    import contextlib
    import types

    # torch is CUDA-only (absent under the mlx extra) — _apply_instrumental_lora
    # only needs no_grad() as a context manager. hf_hub_download is mocked so
    # the test never touches the network/HF cache on any platform.
    fake_torch = types.ModuleType("torch")
    fake_torch.no_grad = contextlib.nullcontext
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(
        "huggingface_hub.hf_hub_download", lambda *a, **k: "/fake/lora.safetensors"
    )

    monkeypatch.setattr("config.YUE2_RUNTIME", "pytorch")
    monkeypatch.setattr("config.YUE2_INST_LORA_SCALE", 1.0)
    monkeypatch.setattr("config.YUE2_MAX_SECONDS", 90)
    eng = YuE2Engine()
    real_mkdtemp = _tempfile.mkdtemp

    def fake_save(out_path):
        import soundfile as _sf
        _sf.write(str(out_path), np.zeros(4800, dtype="float32"), 48000, format="WAV")

    # Fake the yue2 wheel: from_pretrained -> pipe; pipe(...) -> song.save(path)
    fake_pipe = mocker.MagicMock()
    fake_song = mocker.MagicMock()
    fake_song.save.side_effect = fake_save
    fake_pipe.side_effect = lambda **kw: fake_song
    fake_yue2 = types.ModuleType("yue2")
    fake_yue2.YuE2Pipeline = mocker.MagicMock()
    fake_yue2.YuE2Pipeline.from_pretrained.return_value = fake_pipe
    monkeypatch.setitem(sys.modules, "yue2", fake_yue2)

    # Stub the backbone so the real fold math walks an empty layer list fast,
    # but keep _load_pytorch_pipeline + _apply_instrumental_lora REAL.
    fake_model = mocker.MagicMock()
    fake_model.model.layers = []
    fake_pipe._load_model.return_value = fake_model
    mocker.patch("engine.yue2_lora.load_ckpt", return_value={"lora": [], "rank": 64})
    merge = mocker.patch("engine.yue2_lora.merge_ar_lora", wraps=lambda bb, t, scale=1.0: 0)

    # Wrap tempfile so hf_hub (imported fresh here) is untouched but our engine's
    # module-global `tempfile` reference gets a working context manager that
    # yields a real dir. We patch the attribute on the tempfile MODULE CLASS
    # level? No — hf_hub shares it. Instead: real TemporaryDirectory always.
    # (No patch needed — the engine already uses real tempfile, and the fake
    # pipeline writes real audio into it.)
    monkeypatch.setattr(eng, "_read_result_wav", lambda p: (np.zeros(1), 48000))

    eng.generate(style="s", lyrics="[instrumental]", instrumental=True)
    # from_pretrained saw the LoRA flag -> fold path entered
    assert eng._pipe_lora is True
    assert merge.call_count == 1
    assert merge.call_args.kwargs.get("scale") == 1.0


def test_cap_lyrics_compresses_timed_tags():
    lyrics = "[intro 0:00-0:30]\n[verse 0:30-2:00]\n[chorus 2:00-4:00]"
    out = YuE2Engine._cap_lyrics(lyrics, cap_seconds=90)
    # 240s -> 90s: factor 0.375
    assert "0:00-0:11" in out and "0:45-1:30" in out and "4:00" not in out


def test_cap_lyrics_passthrough_under_cap():
    lyrics = "[intro 0:00-0:30]\n[chorus 0:30-1:20]"
    assert YuE2Engine._cap_lyrics(lyrics, cap_seconds=90) == lyrics


def test_cap_lyrics_passthrough_untagged():
    lyrics = "[instrumental]"
    assert YuE2Engine._cap_lyrics(lyrics, cap_seconds=90) == lyrics


def test_is_loaded_and_unload():
    eng = YuE2Engine()
    assert eng.is_loaded is False
    eng._pipe = object()
    assert eng.is_loaded is True
    eng.unload()
    assert eng.is_loaded is False


def test_mlx_runtime_rejected_on_linux(monkeypatch, linux_platform):
    monkeypatch.setattr("config.YUE2_RUNTIME", "mlx")
    eng = YuE2Engine()
    with pytest.raises(RuntimeError, match="macOS-only"):
        eng.generate(style="s", lyrics="[instrumental]")
