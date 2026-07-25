"""Tests for engine.chunker.split_into_chunks()."""
from engine.chunker import split_into_chunks


class TestSplitIntoChunks:
    def test_empty_string(self):
        assert split_into_chunks("") == []

    def test_whitespace_only(self):
        assert split_into_chunks("   \n  ") == []

    def test_single_sentence_under_limit(self):
        text = "Hello world. This is a test."
        result = split_into_chunks(text, chunk_size=200)
        assert len(result) == 1
        assert result[0] == text

    def test_multiple_sentences(self):
        text = "First sentence. Second sentence. Third sentence."
        result = split_into_chunks(text, chunk_size=5)
        assert len(result) >= 1

    def test_oversized_sentence_split_on_punctuation(self):
        # A sentence with 300 words should be split
        words = " ".join([f"word{i}" for i in range(300)])
        text = f"One sentence. {words}."
        result = split_into_chunks(text, chunk_size=50)
        assert len(result) > 1

    def test_sentence_boundary_respected(self):
        text = "This is sentence one. This is sentence two."
        result = split_into_chunks(text, chunk_size=10)
        # Should not split mid-sentence
        for chunk in result:
            # Each chunk should be complete sentences (not split mid-sentence)
            assert chunk.endswith((" one.", " two."))

    def test_default_chunk_size_matches_docstring(self):
        """The default chunk_size is 120 words, matching the docstring."""
        import inspect
        sig = inspect.signature(split_into_chunks)
        assert sig.parameters["chunk_size"].default == 120

    def test_unpunctuated_oversized_sentence_still_splits(self):
        """Regression test: a long run of text with no comma/semicolon/colon
        must still be hard-split on word count, not emitted as one giant
        chunk (the old fallback silently bypassed chunk_size in this case)."""
        text = " ".join([f"word{i}" for i in range(2000)])
        result = split_into_chunks(text, chunk_size=120)
        assert len(result) > 1
        assert all(len(c.split()) <= 120 for c in result)

    def test_max_chars_cap(self):
        """A chunk built from few but very long words should split on the
        character cap even though it stays under the word-count cap."""
        long_word = "x" * 100
        text = ". ".join([long_word] * 10) + "."
        result = split_into_chunks(text, chunk_size=120, max_chars=250)
        assert len(result) > 1
        assert all(len(c) <= 250 for c in result)

    def test_trailing_whitespace_stripped(self):
        text = "Good sentence.   "
        result = split_into_chunks(text, chunk_size=200)
        assert result[0].strip() == result[0]
