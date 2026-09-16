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


def test_auto_runtime_is_gguf_on_linux(monkeypatch):
    monkeypatch.setattr("config.YUE2_RUNTIME", "")
    eng = YuE2Engine()
    assert eng._resolve_runtime() == "gguf"


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


def test_gguf_runtime_runs_subprocess(monkeypatch, fake_wav):
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
    eng = YuE2Engine()
    with pytest.raises(RuntimeError, match="RURU_YUE2_AUDIOCPP_BIN"):
        eng.generate(style="s", lyrics="[instrumental]")


def test_gguf_missing_model_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr("config.YUE2_AUDIOCPP_BIN", "/fake/bin/audiocpp")
    monkeypatch.setattr("config.YUE2_GGUF_DIR", tmp_path)
    eng = YuE2Engine()
    with pytest.raises(RuntimeError, match="missing"):
        eng.generate(style="s", lyrics="[instrumental]")


def test_pytorch_lora_flag_selects_merge(monkeypatch, mocker):
    import tempfile as _tempfile
    import contextlib

    monkeypatch.setattr("config.YUE2_RUNTIME", "pytorch")
    monkeypatch.setattr("config.YUE2_INST_LORA_SCALE", 1.0)
    monkeypatch.setattr("config.YUE2_MAX_SECONDS", 90)
    eng = YuE2Engine()
    fake_pipe = mocker.MagicMock()
    merged = {}
    real_mkdtemp = _tempfile.mkdtemp

    def fake_save(out_path):
        import soundfile as _sf
        _sf.write(str(out_path), np.zeros(4800, dtype="float32"), 48000, format="WAV")

    monkeypatch.setattr(
        eng, "_load_pytorch_pipeline",
        lambda with_lora: merged.update(lora=with_lora) or fake_pipe,
    )
    mocker.patch("engine.yue2_lora.load_ckpt", return_value={"lora": [], "rank": 64})
    merge = mocker.patch("engine.yue2_lora.merge_ar_lora", return_value=392)
    monkeypatch.setattr(eng, "_read_result_wav", lambda p: (np.zeros(1), 48000))
    fake_song = mocker.MagicMock()
    fake_song.save.side_effect = fake_save
    fake_pipe.side_effect = lambda **kw: fake_song

    @contextlib.contextmanager
    def fake_td(*a, **kw):
        yield real_mkdtemp()

    monkeypatch.setattr("engine.yue2_engine.tempfile.TemporaryDirectory", fake_td)
    eng.generate(style="s", lyrics="[instrumental]", instrumental=True)
    assert merged["lora"] is True
    assert merge.call_args.kwargs["scale"] == 1.0


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


def test_mlx_runtime_rejected_on_linux(monkeypatch):
    monkeypatch.setattr("config.YUE2_RUNTIME", "mlx")
    eng = YuE2Engine()
    with pytest.raises(RuntimeError, match="macOS-only"):
        eng.generate(style="s", lyrics="[instrumental]")
