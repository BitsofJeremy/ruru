"""Tests for engine/export.py — multi-format encode_variants."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import soundfile as sf
from pydub.exceptions import CouldntEncodeError

import config
from engine.result import TTSResult
from tests.conftest import MOCK_WAV


def _purge_pydub() -> dict:
    """Drop pydub from sys.modules; return the entries to restore later."""
    saved = {
        k: m
        for k, m in sys.modules.items()
        if k == "pydub" or k.startswith("pydub.")
    }
    for key in saved:
        del sys.modules[key]
    return saved


def test_encode_variants_wav_only_writes_pcm(tmp_path):
    """WAV-only export writes a real PCM_16 file without needing pydub/ffmpeg."""
    saved = _purge_pydub()
    try:
        # Re-import engine.export under the purge so even a top-level
        # `import pydub` in the module would surface here.
        sys.modules.pop("engine.export", None)
        from engine.export import encode_variants

        result = TTSResult(MOCK_WAV, 24000)
        out = encode_variants(result, tmp_path / "song", ["wav"])

        assert set(out) == {"wav"}
        assert out["wav"] == tmp_path / "song.wav"

        info = sf.info(out["wav"])
        assert info.samplerate == 24000
        assert info.subtype == "PCM_16"
        data, sr = sf.read(out["wav"])
        assert sr == 24000
        assert len(data) == len(MOCK_WAV)

        # No lossy sidecars, no stray temp files beside the stem.
        assert sorted(p.name for p in tmp_path.iterdir()) == ["song.wav"]

        # The wav-only path must never import pydub at all.
        assert not [
            k for k in sys.modules if k == "pydub" or k.startswith("pydub.")
        ]
    finally:
        sys.modules.update(saved)


def test_encode_variants_unknown_format_raises(tmp_path):
    from engine.export import encode_variants

    result = TTSResult(MOCK_WAV, 24000)
    with pytest.raises(ValueError, match="flac"):
        encode_variants(result, tmp_path / "song", ["wav", "flac"])

    # Validation happens before any work: destination stays empty.
    assert list(tmp_path.iterdir()) == []


def test_encode_variants_lossy_uses_pydub_mp4_name_for_m4a(tmp_path):
    from engine.export import encode_variants

    calls: list[dict] = []

    def fake_export(path, **kwargs):
        calls.append(kwargs)
        Path(path).write_bytes(b"encoded")

    with patch("pydub.AudioSegment") as mock_cls:
        segment = MagicMock()
        segment.export.side_effect = fake_export
        mock_cls.from_file.return_value = segment

        result = TTSResult(MOCK_WAV, 24000)
        out = encode_variants(result, tmp_path / "song", ["mp3", "m4a"])

    # pydub's format name for m4a is "mp4"; mp3 stays "mp3".
    assert [c["format"] for c in calls] == ["mp3", "mp4"]
    # Default bitrate comes from config.AUDIO_BITRATE.
    assert all(c["bitrate"] == config.AUDIO_BITRATE for c in calls)

    # Files land with the requested extensions.
    assert set(out) == {"mp3", "m4a"}
    assert out["mp3"] == tmp_path / "song.mp3"
    assert out["m4a"] == tmp_path / "song.m4a"
    assert out["mp3"].exists()
    assert out["m4a"].exists()

    # An explicit bitrate override propagates to pydub's export.
    calls.clear()
    with patch("pydub.AudioSegment") as mock_cls:
        segment = MagicMock()
        segment.export.side_effect = fake_export
        mock_cls.from_file.return_value = segment
        encode_variants(result, tmp_path / "song", ["mp3"], bitrate="192k")
    assert calls == [{"format": "mp3", "bitrate": "192k"}]


def test_encode_variants_publishes_only_after_all_encodes(tmp_path):
    """Publish goes through shutil.move, and only once every encode is done."""
    from engine.export import encode_variants

    with patch("shutil.move", wraps=shutil.move) as mock_move:
        with patch("pydub.AudioSegment") as mock_cls:
            segment = MagicMock()

            def fake_export(path, **kwargs):
                # Every encode runs against the staging dir; the publish
                # step must not have started before all of them are done.
                assert not mock_move.called, (
                    "publish started before all encodes finished"
                )
                Path(path).write_bytes(b"encoded")

            segment.export.side_effect = fake_export
            mock_cls.from_file.return_value = segment

            result = TTSResult(MOCK_WAV, 24000)
            out = encode_variants(result, tmp_path / "song", ["mp3", "m4a"])

    # shutil.move (not os.replace) is the publish mechanism — os.replace
    # raises EXDEV when the temp dir and destination are on different
    # filesystems (tmpfs /tmp vs disk-backed WORK_DIR on Linux prod).
    assert mock_move.call_count == 2
    moved = {Path(c.args[1]).name for c in mock_move.call_args_list}
    assert moved == {"song.mp3", "song.m4a"}
    assert set(out) == {"mp3", "m4a"}
    assert out["mp3"].exists()
    assert out["m4a"].exists()


@pytest.mark.parametrize(
    "err_cls", [FileNotFoundError, CouldntEncodeError], ids=lambda c: c.__name__
)
def test_encode_variants_wraps_ffmpeg_errors(tmp_path, err_cls):
    from engine.export import encode_variants

    with patch("pydub.AudioSegment") as mock_cls:
        segment = MagicMock()
        segment.export.side_effect = err_cls("ffmpeg missing")
        mock_cls.from_file.return_value = segment

        result = TTSResult(MOCK_WAV, 24000)
        with pytest.raises(RuntimeError, match="ffmpeg"):
            encode_variants(result, tmp_path / "song", ["wav", "mp3"])

    # Staged files never reach the destination: wav succeeded in the temp
    # dir, mp3 failed — nothing may appear beside the stem.
    assert list(tmp_path.iterdir()) == []
