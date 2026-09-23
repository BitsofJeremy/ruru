"""YuE2Engine tests — GPU never touched: subprocess and pipeline are faked."""
from pathlib import Path

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


@pytest.fixture
def fake_mlx(monkeypatch, fake_wav):
    """Force the MLX runtime with a captured, always-successful subprocess.
    Returns (engine, calls); calls["cmds"] is the list of argv lists."""
    calls = {"cmds": []}

    def fake_run(cmd, **kw):
        calls["cmds"].append(cmd)
        calls["kw"] = kw

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr("config.YUE2_RUNTIME", "mlx")
    monkeypatch.setattr("engine.yue2_engine.platform.system", lambda: "Darwin")
    monkeypatch.setattr("config.MLX_MODELS_PATH", str(fake_wav.parent))
    monkeypatch.setattr("subprocess.run", fake_run)
    eng = YuE2Engine()
    monkeypatch.setattr(eng, "_read_result_wav", lambda p: (np.zeros(1), 48000))
    return eng, calls


@pytest.fixture
def fake_gguf(monkeypatch, fake_wav):
    """Force the explicit gguf runtime with a captured, always-successful
    subprocess. Returns (engine, calls); calls["cmds"] is the argv list list."""
    calls = {"cmds": []}

    def fake_run(cmd, **kw):
        calls["cmds"].append(cmd)

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr("config.YUE2_RUNTIME", "gguf")
    monkeypatch.setattr("config.YUE2_AUDIOCPP_BIN", "/fake/bin/audiocpp")
    monkeypatch.setattr("config.YUE2_GGUF_DIR", fake_wav.parent)
    (fake_wav.parent / "yue2-3b-q4_k_m.gguf").write_bytes(b"x")
    monkeypatch.setattr("subprocess.run", fake_run)
    eng = YuE2Engine()
    monkeypatch.setattr(eng, "_gguf_output_target", lambda d: fake_wav)
    return eng, calls


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
    # the pipeline is faked below, so the real preflight (yue2 wheel + CUDA)
    # must be bypassed too — on a Mac it would refuse before reaching it.
    monkeypatch.setattr(
        YuE2Engine, "_pytorch_ready", staticmethod(lambda: (True, ""))
    )
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


def test_mlx_runtime_runs_subprocess(monkeypatch, fake_wav):
    """MLX subprocess must run the current interpreter (the launchd PATH has
    no bare `python`) with cwd at the MLX dir (generate.py resolves the
    relative --model path against the subprocess cwd)."""
    import sys

    eng = YuE2Engine()
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        calls["kw"] = kw

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr(
        "engine.yue2_engine.platform.system", lambda: "Darwin"
    )
    monkeypatch.setattr("config.MLX_MODELS_PATH", str(fake_wav.parent))
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(eng, "_read_result_wav", lambda p: (np.zeros(1), 48000))
    result = eng.generate(style="s", lyrics="[instrumental]")
    assert result is not None
    cmd0 = str(calls["cmd"][0])
    assert cmd0 == sys.executable
    assert calls["kw"].get("cwd", "").endswith("YuE2-3B-MLX")
    assert any("generate.py" in str(c) for c in calls["cmd"])


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
    monkeypatch.setattr(
        YuE2Engine, "_pytorch_ready", staticmethod(lambda: (True, ""))
    )
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
    monkeypatch.setattr(
        YuE2Engine, "_pytorch_ready", staticmethod(lambda: (True, ""))
    )
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


def test_instrumental_without_pytorch_runtime_raises_clear_error(
    monkeypatch, mocker
):
    """Mac default: auto runtime is mlx, Instrumental forces pytorch, but the
    yue2 wheel isn't installed (mlx/cuda extras conflict). The error must name
    the Instrumental checkbox — not a bare ModuleNotFoundError."""
    monkeypatch.setattr("config.YUE2_RUNTIME", "")
    monkeypatch.setattr(
        "engine.yue2_engine.platform.system", lambda: "Darwin"
    )
    eng = YuE2Engine()
    monkeypatch.setattr(eng, "_pytorch_ready", staticmethod(lambda: (False, "yue2 wheel not installed")))
    with pytest.raises(RuntimeError, match="Instrumental"):
        eng.generate(style="s", lyrics="[instrumental]", instrumental=True)


def test_explicit_pytorch_without_runtime_raises_reinstall_hint(monkeypatch):
    """Linux: uv sync prunes the --no-deps yue2_infer wheel. The error must
    point at the README reinstall step, not ModuleNotFoundError."""
    monkeypatch.setattr("config.YUE2_RUNTIME", "pytorch")
    eng = YuE2Engine()
    monkeypatch.setattr(eng, "_pytorch_ready", staticmethod(lambda: (False, "no CUDA")))
    with pytest.raises(RuntimeError, match="uv sync"):
        eng.generate(style="s", lyrics="[verse]")


# -- advanced kwargs (cfg_scale / steps / max_seconds) ------------------------


def _flag(cmd, name):
    """Value following a CLI flag in an argv list (None when absent)."""
    cmd = [str(c) for c in cmd]
    try:
        return cmd[cmd.index(name) + 1]
    except ValueError:
        return None


def test_mlx_cmd_includes_advanced_flags(fake_mlx):
    """cfg_scale/steps/max_seconds/seed/cot all reach the MLX argv when set..."""
    eng, calls = fake_mlx
    eng.generate(
        style="s",
        lyrics="[instrumental]",
        cot="melody",
        cfg_scale=1.5,
        steps=16,
        max_seconds=240,
        seed=7,
    )
    cmd = calls["cmds"][0]
    assert _flag(cmd, "--cot") == "melody"
    assert _flag(cmd, "--cfg-scale") == "1.5"
    assert _flag(cmd, "--steps") == "16"
    assert _flag(cmd, "--max-semantic-tokens") == "6000"  # 240s * 25 fps
    assert _flag(cmd, "--seed") == "7"

    # ...and are omitted entirely when None (blank-field compatibility).
    eng.generate(style="s", lyrics="[instrumental]")
    cmd2 = [str(c) for c in calls["cmds"][1]]
    for name in ("--seed", "--cfg-scale", "--steps", "--max-semantic-tokens"):
        assert name not in cmd2


def test_gguf_forwards_cot_request_option(fake_gguf):
    """cot rides the existing --request-option cot= channel; the default falls
    back to config.YUE2_COT. gguf gets no cfg/steps/length flags at all."""
    import config

    eng, calls = fake_gguf
    eng.generate(style="s", lyrics="[verse]", cot="melody")
    assert _flag(calls["cmds"][0], "--request-option") == "cot=melody"

    eng.generate(style="s", lyrics="[verse]")
    assert _flag(calls["cmds"][1], "--request-option") == f"cot={config.YUE2_COT}"


def test_max_seconds_overrides_pytorch_cap(monkeypatch):
    """max_seconds wins over the config default for the pytorch runtime;
    blank falls back to config.YUE2_MAX_SECONDS (90)."""
    monkeypatch.setattr("config.YUE2_RUNTIME", "pytorch")
    monkeypatch.setattr("config.YUE2_MAX_SECONDS", 90)
    monkeypatch.setattr(
        YuE2Engine, "_pytorch_ready", staticmethod(lambda: (True, ""))
    )
    eng = YuE2Engine()
    seen = []
    monkeypatch.setattr(
        YuE2Engine,
        "_cap_lyrics",
        staticmethod(
            lambda lyrics, *, cap_seconds: seen.append(cap_seconds) or lyrics
        ),
    )
    monkeypatch.setattr(eng, "_generate_pytorch", lambda *a, **k: (np.zeros(1), 48000))

    eng.generate(style="s", lyrics="[verse]", max_seconds=240)
    assert seen == [240]
    eng.generate(style="s", lyrics="[verse]")
    assert seen == [240, 90]


def test_finish_trims_to_cap(tmp_path):
    """_finish folds stereo to mono and trims in memory to cap_seconds * sr —
    no temp-file rewrite (the old .flac-named PCM write)."""
    sr = 8000
    path = tmp_path / "long.wav"
    sf.write(
        str(path), np.zeros((sr * 5, 2), dtype=np.float32), sr, subtype="PCM_16"
    )
    eng = YuE2Engine()
    wav, out_sr = eng._finish(path, cap_seconds=2)
    assert out_sr == sr
    assert wav.ndim == 1  # stereo folded
    assert len(wav) <= 2 * sr


def test_cap_lyrics_applied_for_mlx_when_max_seconds(fake_mlx, monkeypatch):
    """_cap_lyrics now runs for every runtime when a cap is active — mlx with
    max_seconds=60 records cap_seconds=60; blank mlx has no cap at all."""
    eng, calls = fake_mlx
    seen = []
    monkeypatch.setattr(
        YuE2Engine,
        "_cap_lyrics",
        staticmethod(
            lambda lyrics, *, cap_seconds: seen.append(cap_seconds) or lyrics
        ),
    )
    eng.generate(style="s", lyrics="[intro 0:00-5:00]", max_seconds=60)
    assert seen == [60]
    eng.generate(style="s", lyrics="[intro 0:00-5:00]")
    assert seen == [60]  # blank max_seconds -> no cap on the mlx runtime


# -- non-positive cap = no cap (v0.8.0 review fix) -----------------------------


@pytest.mark.parametrize("cap_seconds", [0, -5])
def test_nonpositive_cap_means_no_cap(tmp_path, cap_seconds):
    """_finish must treat 0 / negative cap_seconds as NO cap: before the fix
    `cap_seconds is not None` trimmed a longer-than-cap wav to zero samples,
    so a cap of 0 completed "successfully" with silent empty audio."""
    sr = 8000
    path = tmp_path / "long.wav"
    sf.write(
        str(path), np.zeros(sr * 5, dtype=np.float32), sr, subtype="PCM_16"
    )
    eng = YuE2Engine()
    wav, out_sr = eng._finish(path, cap_seconds=cap_seconds)
    assert out_sr == sr
    assert len(wav) == sr * 5  # full length kept — nothing trimmed


def test_zero_max_seconds_means_no_cap(fake_mlx, monkeypatch):
    """max_seconds=0 on the MLX path: no _cap_lyrics call and no trim to an
    empty result (0 = 'no cap', never 'zero-length song')."""
    eng, calls = fake_mlx
    monkeypatch.setattr(eng, "_read_result_wav", lambda p: (np.zeros(48000), 48000))
    seen = []
    monkeypatch.setattr(
        YuE2Engine,
        "_cap_lyrics",
        staticmethod(
            lambda lyrics, *, cap_seconds: seen.append(cap_seconds) or lyrics
        ),
    )
    wav, sr = eng.generate(style="s", lyrics="[intro 0:00-5:00]", max_seconds=0)
    assert seen == []  # lyric gate skipped: no cap
    assert len(wav) == 48000  # result untouched, not trimmed to empty


def test_zero_config_cap_means_no_cap_for_pytorch(monkeypatch):
    """RURU_YUE2_MAX_SECONDS=0 (a plausible operator misread of '0 = no cap')
    must mean no cap for pytorch songs: no _cap_lyrics call and _finish gets
    None, so the song is never trimmed to zero samples."""
    import contextlib
    import tempfile as _tempfile

    monkeypatch.setattr("config.YUE2_RUNTIME", "pytorch")
    monkeypatch.setattr("config.YUE2_MAX_SECONDS", 0)
    monkeypatch.setattr(
        YuE2Engine, "_pytorch_ready", staticmethod(lambda: (True, ""))
    )
    eng = YuE2Engine()
    seen = []
    monkeypatch.setattr(
        YuE2Engine,
        "_cap_lyrics",
        staticmethod(
            lambda lyrics, *, cap_seconds: seen.append(cap_seconds) or lyrics
        ),
    )

    def fake_save(out_path):
        import soundfile as _sf

        # real FLAC content in the .flac path — the real _read_result_wav must
        # read it back so _finish (the trim gate under test) runs for real
        _sf.write(
            str(out_path), np.zeros(48000, dtype="float32"), 48000, format="FLAC"
        )

    # real _generate_pytorch + real _finish: the trim gate is under test
    monkeypatch.setattr(
        eng,
        "_load_pytorch_pipeline",
        lambda with_lora: (
            lambda **kw: type("S", (), {"save": staticmethod(fake_save)})()
        ),
    )
    real_mkdtemp = _tempfile.mkdtemp

    @contextlib.contextmanager
    def fake_td(*a, **kw):
        yield real_mkdtemp()

    monkeypatch.setattr("engine.yue2_engine.tempfile.TemporaryDirectory", fake_td)

    wav, sr = eng.generate(style="s", lyrics="[intro 0:00-5:00]")
    assert seen == []  # lyric gate: cap 0 -> skipped (falsy, unchanged)
    assert len(wav) == 48000  # full song — _finish did NOT trim to zero
