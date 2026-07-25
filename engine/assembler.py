"""Audio post-processing and assembly for ruru.

Handles silence trimming, normalisation, and concatenation of WAV chunks
into a single output file (MP3, M4A, or WAV).
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from pydub import AudioSegment
from pydub.effects import normalize
from pydub.silence import detect_leading_silence

logger = logging.getLogger(__name__)


def trim_silence(
    audio: AudioSegment,
    threshold: int = -45,
    chunk_size: int = 1000,
) -> AudioSegment:
    """Trim leading and trailing silence from *audio*.

    Args:
        audio: The AudioSegment to trim.
        threshold: Silence threshold in dBFS (default -45).
        chunk_size: Detection granularity in ms (default 1000).

    Returns:
        A new AudioSegment with silence removed from both ends.
    """
    leading = detect_leading_silence(audio, silence_threshold=threshold, chunk_size=chunk_size)
    trimmed = audio[leading:]
    trailing = detect_leading_silence(trimmed.reverse(), silence_threshold=threshold, chunk_size=chunk_size)
    if trailing:
        trimmed = trimmed[:-trailing]
    return trimmed


def post_process_audio(audio: AudioSegment) -> AudioSegment:
    """Trim silence and normalise *audio* to -3 dBFS.

    Args:
        audio: The AudioSegment to process.

    Returns:
        Processed AudioSegment.
    """
    audio = trim_silence(audio)
    audio = normalize(audio, headroom=3.0)
    return audio


def assemble_chunks(
    wav_files: list[Path],
    output_path: Path,
    audio_format: str = "mp3",
    bitrate: str = "128k",
    do_post_process: bool = True,
) -> bool:
    """Concatenate WAV chunk files into a single output file.

    Each chunk is individually post-processed (if enabled) before
    concatenation, and the final combined audio receives a second
    post-processing pass.

    Args:
        wav_files: Ordered list of WAV file paths to concatenate.
        output_path: Destination path for the assembled file.
        audio_format: Output format -- ``mp3``, ``m4a``, or ``wav``.
        bitrate: Audio bitrate for lossy formats (default ``128k``).
        do_post_process: Whether to apply silence trimming + normalisation.

    Returns:
        ``True`` if assembly succeeded, ``False`` if no audio was produced.
    """
    if not wav_files:
        return False

    combined = AudioSegment.empty()
    for wf in wav_files:
        try:
            chunk_audio = AudioSegment.from_wav(str(wf))
            if do_post_process:
                raw_samples = np.array(chunk_audio.get_array_of_samples(), dtype=np.float32)
                if len(raw_samples) > 0:
                    raw_peak = np.max(np.abs(raw_samples)) / (2 ** (chunk_audio.sample_width * 8 - 1))
                    logger.debug("Assembler: chunk %s raw peak: %.4f", wf.name, raw_peak)
                chunk_audio = post_process_audio(chunk_audio)
            combined += chunk_audio
        except Exception:
            continue

    if len(combined) == 0:
        return False

    if do_post_process:
        combined = post_process_audio(combined)

    # pydub uses "mp4" as the format name for m4a containers
    fmt = "mp4" if audio_format == "m4a" else audio_format
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(output_path), format=fmt, bitrate=bitrate)
    return True


def tag_audio_file(
    path: Path,
    title: str = "",
    creator_tag: str = "Created by ruru",
    author: str = "",
    cover_path: "str | Path | None" = None,
    album: str = "",
    track_number: "int | None" = None,
    genre: str = "Audiobook",
) -> None:
    """Write metadata tags to an MP3 or M4A file.

    Silently skips if the format is unsupported or mutagen is unavailable.

    Args:
        path: Path to the audio file.
        title: Title tag (track/chapter title).
        creator_tag: Comment tag identifying the creator application.
        author: Author name written to Artist tag (falls back to creator_tag if empty).
        cover_path: Optional path to a JPEG or PNG cover image to embed.
        album: Album tag — should be the book title.
        track_number: Track number as an integer; formatted as 3-digit zero-padded
            string in ID3 (e.g. 1 → "001"). Pass None to omit the tag.
        genre: Genre tag (default "Audiobook").
    """
    import logging
    _log = logging.getLogger(__name__)
    artist_tag = author if author else creator_tag
    try:
        fmt = path.suffix.lower()
        if fmt == ".mp3":
            from mutagen.id3 import ID3, TIT2, TPE1, COMM, TALB, TRCK, TCON
            try:
                tags = ID3(str(path))
            except Exception:
                tags = ID3()
            if title:
                tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artist_tag))
            tags.add(COMM(encoding=3, lang="eng", desc="", text=creator_tag))
            if album:
                tags.add(TALB(encoding=3, text=album))
            if track_number is not None:
                tags.add(TRCK(encoding=3, text=f"{track_number:03d}"))
            if genre:
                tags.add(TCON(encoding=3, text=genre))
            if cover_path and Path(cover_path).exists():
                try:
                    from mutagen.id3 import APIC
                    cover_suffix = Path(cover_path).suffix.lower()
                    mime_type = "image/jpeg" if cover_suffix in (".jpg", ".jpeg") else "image/png"
                    with open(cover_path, "rb") as f:
                        cover_bytes = f.read()
                    tags.add(APIC(encoding=3, mime=mime_type, type=3, desc="Cover", data=cover_bytes))
                except Exception as ce:
                    _log.warning("Failed to embed cover art in MP3 %s: %s", path, ce)
            tags.save(str(path))
        elif fmt in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            tags = MP4(str(path))
            if title:
                tags["\xa9nam"] = [title]
            tags["\xa9ART"] = [artist_tag]
            tags["\xa9cmt"] = [creator_tag]
            if album:
                tags["\xa9alb"] = [album]
            if track_number is not None:
                tags["trkn"] = [(track_number, 0)]
            if genre:
                tags["\xa9gen"] = [genre]
            if cover_path and Path(cover_path).exists():
                try:
                    from mutagen.mp4 import MP4Cover
                    with open(cover_path, "rb") as f:
                        cover_bytes = f.read()
                    fmt_img = MP4Cover.FORMAT_JPEG if Path(cover_path).suffix.lower() in (".jpg", ".jpeg") else MP4Cover.FORMAT_PNG
                    tags["covr"] = [MP4Cover(cover_bytes, imageformat=fmt_img)]
                except Exception as ce:
                    _log.warning("Failed to embed cover art in M4A %s: %s", path, ce)
            tags.save()
    except Exception as e:
        _log.warning("Failed to write audio tags to %s: %s", path, e)
