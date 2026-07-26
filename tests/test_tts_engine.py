"""Tests for engine/tts.py's max_tokens sizing.

mlx_audio.tts.generate.generate_audio() defaults to max_tokens=1200 (~96s at
the Qwen3-TTS 12Hz codec's frame_rate=12.5), which a long chunk can hit
mid-utterance and come back truncated/looping. TTSEngine must pass an
explicit, per-chunk max_tokens instead of relying on that fixed default.
"""
from importlib.util import find_spec
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import soundfile as sf

requires_mlx = pytest.mark.skipif(
    find_spec("mlx_audio") is None,
    reason="mlx_audio is Apple-Silicon only; the cross-platform engine surface "
           "is covered by tests/test_engine_contract.py",
)


class TestEstimateMaxTokens:
    def test_scales_with_word_count(self):
        from engine.tts import _estimate_max_tokens

        short_text = "A few words here."
        long_text = " ".join(["word"] * 500)
        assert _estimate_max_tokens(long_text) > _estimate_max_tokens(short_text)

    def test_floor_for_very_short_text(self):
        from engine.tts import _estimate_max_tokens

        assert _estimate_max_tokens("Hi.") == 200

    def test_matches_frame_rate_and_safety_factor(self):
        from engine.tts import _estimate_max_tokens, FRAME_RATE_HZ, ASSUMED_WPM, TOKEN_SAFETY_FACTOR

        text = " ".join(["word"] * 300)
        expected_seconds = (300 / ASSUMED_WPM) * 60
        expected_tokens = int(expected_seconds * FRAME_RATE_HZ * TOKEN_SAFETY_FACTOR)
        assert _estimate_max_tokens(text) == expected_tokens


@requires_mlx
class TestGenerateClonePassesMaxTokens:
    def _write_fake_output(self, **kwargs):
        out_dir = kwargs["output_path"]
        prefix = kwargs["file_prefix"]
        wav = np.zeros(2400, dtype=np.float32)
        sf.write(f"{out_dir}/{prefix}.mp3", wav, 24000, format="WAV")

    def test_generate_clone_passes_estimated_max_tokens(self, tmp_path):
        from engine.tts import TTSEngine, _estimate_max_tokens

        ref = tmp_path / "ref.wav"
        sf.write(str(ref), np.zeros(2400, dtype=np.float32), 24000)

        text = " ".join(["word"] * 300)
        captured = {}

        def _fake_generate_audio(**kwargs):
            captured.update(kwargs)
            self._write_fake_output(**kwargs)

        with patch("mlx_audio.tts.utils.load_model", return_value=MagicMock()), \
             patch("mlx_audio.tts.generate.generate_audio", side_effect=_fake_generate_audio):
            engine = TTSEngine()
            engine.generate_clone(text, ref, "ref transcript")

        assert captured["max_tokens"] == _estimate_max_tokens(text)

    def test_generate_design_passes_estimated_max_tokens(self):
        from engine.tts import TTSEngine, _estimate_max_tokens

        text = " ".join(["word"] * 50)
        captured = {}

        def _fake_generate_audio(**kwargs):
            captured.update(kwargs)
            self._write_fake_output(**kwargs)

        with patch("mlx_audio.tts.utils.load_model", return_value=MagicMock()), \
             patch("mlx_audio.tts.generate.generate_audio", side_effect=_fake_generate_audio):
            engine = TTSEngine()
            engine.generate_design(text, "A calm narrator voice")

        assert captured["max_tokens"] == _estimate_max_tokens(text)
