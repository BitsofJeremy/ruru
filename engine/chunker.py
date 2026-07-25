"""Sentence-boundary-aware text chunking for ruru.

Splits text into word-count-limited chunks while respecting sentence
boundaries. Oversized sentences are further split on punctuation.
"""
from __future__ import annotations

import re


def _split_on_word_count(text: str, chunk_size: int) -> list[str]:
    """Hard-split *text* into pieces of at most *chunk_size* words each.

    Last-resort fallback for oversized text with no comma/semicolon/colon to
    break on, guaranteeing chunk_size is never silently bypassed.
    """
    words = text.split()
    return [
        " ".join(words[i : i + chunk_size])
        for i in range(0, len(words), chunk_size)
    ]


def split_into_chunks(
    text: str, chunk_size: int = 120, max_chars: int = 700
) -> list[str]:
    """Split *text* into chunks of at most *chunk_size* words and *max_chars*
    characters, whichever limit is hit first.

    The splitter tries to break on sentence-ending punctuation (.!?) first.
    If a single sentence exceeds the word limit it is broken on commas,
    semicolons, or colons as a fallback, and as a last resort on a raw word
    count boundary if no such punctuation exists.

    Args:
        text: The input text to split.
        chunk_size: Maximum number of words per chunk (default 120).
        max_chars: Maximum number of characters per chunk (default 700).

    Returns:
        A list of non-empty text chunks.
    """
    if not text or not text.strip():
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current_chunk = ""
    current_words = 0

    def fits(words_to_add: int, piece: str) -> bool:
        return (
            current_words + words_to_add <= chunk_size
            and len(current_chunk) + len(piece) <= max_chars
        )

    for sentence in sentences:
        sentence_words = len(sentence.split())

        if sentence_words > chunk_size or len(sentence) > max_chars:
            # Flush whatever is buffered first
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
                current_words = 0
            # Break the oversized sentence on punctuation
            parts = re.split(r"[,;:]", sentence)
            if len(parts) == 1:
                # No punctuation to split on -- hard-split on word count so
                # chunk_size/max_chars can never be silently bypassed.
                for hard_part in _split_on_word_count(sentence, chunk_size):
                    chunks.append(hard_part.strip())
                continue
            for part in parts:
                part_words = len(part.split())
                piece = part + " "
                if fits(part_words, piece):
                    current_chunk += piece
                    current_words += part_words
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    if part_words > chunk_size or len(part) > max_chars:
                        for hard_part in _split_on_word_count(part, chunk_size):
                            chunks.append(hard_part.strip())
                        current_chunk = ""
                        current_words = 0
                    else:
                        current_chunk = piece
                        current_words = part_words
        else:
            piece = sentence + " "
            if fits(sentence_words, piece):
                current_chunk += piece
                current_words += sentence_words
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = piece
                current_words = sentence_words

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return [c for c in chunks if c.strip()]
