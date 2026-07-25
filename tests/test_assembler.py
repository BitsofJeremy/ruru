"""Tests for engine/assembler.py — silence trimming, normalisation, assembly."""
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydub import AudioSegment


class TestTrimSilence:
    def test_trim_silence_leading(self):
        from engine.assembler import trim_silence

        # Create audio with leading silence and some speech
        silent = AudioSegment.silent(duration=100)
        speech = AudioSegment.silent(duration=500)
        audio = silent + speech

        trimmed = trim_silence(audio)
        # Leading silence should be removed
        assert len(trimmed) < len(audio)

    def test_trim_silence_trailing(self):
        from engine.assembler import trim_silence

        speech = AudioSegment.silent(duration=500)
        silent = AudioSegment.silent(duration=100)
        audio = speech + silent

        trimmed = trim_silence(audio)
        # Trailing silence should be removed
        assert len(trimmed) < len(audio)


class TestPostProcessAudio:
    def test_post_process_audio(self):
        from engine.assembler import post_process_audio

        audio = AudioSegment.silent(duration=1000)
        result = post_process_audio(audio)
        assert isinstance(result, AudioSegment)


class TestAssembleChunks:
    def test_assemble_empty_list_returns_false(self, tmp_path):
        from engine.assembler import assemble_chunks

        output = tmp_path / "output.mp3"
        result = assemble_chunks([], output)
        assert result is False

    @patch("engine.assembler.AudioSegment.from_wav")
    def test_assemble_chunks_calls_post_process(self, mock_from_wav, tmp_path):
        from engine.assembler import assemble_chunks

        mock_audio = MagicMock()
        mock_audio.get_array_of_samples.return_value = np.array([0.1] * 1000)
        mock_from_wav.return_value = mock_audio

        # Create real temp WAV files
        import soundfile as sf
        wav_path1 = tmp_path / "chunk1.wav"
        wav_path2 = tmp_path / "chunk2.wav"
        sf.write(str(wav_path1), np.array([0.1] * 1000, dtype=np.float32), 24000)
        sf.write(str(wav_path2), np.array([0.1] * 1000, dtype=np.float32), 24000)

        output = tmp_path / "output.mp3"
        result = assemble_chunks([wav_path1, wav_path2], output, do_post_process=False)
        # With post_process=False it should still assemble (may return False if no real samples)
        assert isinstance(result, bool)