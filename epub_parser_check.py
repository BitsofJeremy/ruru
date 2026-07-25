#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "ebooklib>=0.18",
#   "beautifulsoup4>=4.12.0",
#   "html2text>=2024.2.0",
#   "pydub>=0.25.0",
#   "audioop-lts>=0.2.1",
# ]
# ///
"""Test EPUB parser output and optionally narrate with TTS.

Usage (text-only, works on Mac):
    uv run epub_parser_check.py

Usage (with TTS narration, requires GPU server):
    uv run epub_parser_check.py --narrate
    uv run epub_parser_check.py --narrate --chapters 1,2,3  # specific chapters
    uv run epub_parser_check.py --narrate --max-chunks 5    # limit chunks for quick test
"""
import argparse
import json
import sys
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

from engine.parsers import parse_epub
from engine.chunker import split_into_chunks

EPUB_PATH = _REPO_DIR / "test_ebooks" / "The Wonderful Wizard of Oz by L. Frank Baum.epub"
VOICE_REF = _REPO_DIR / "ephergent_voices" / "pixel.wav"
OUTPUT_DIR = _REPO_DIR / "test_output"


def test_parser(epub_path: Path) -> list:
    """Parse EPUB and return chapters, printing diagnostics."""
    print(f"Parsing: {epub_path.name}")
    print("=" * 70)

    chapters = parse_epub(epub_path)
    print(f"Extracted {len(chapters)} chapters\n")

    results = []
    for i, ch in enumerate(chapters):
        words = ch.word_count
        # Check for HTML remnants
        has_html = "<" in ch.text and ">" in ch.text
        has_markdown = any(ch.text.lstrip().startswith(m) for m in ("# ", "## ", "- ", "* "))
        has_para_breaks = "\n\n" in ch.text

        status = []
        if has_html:
            status.append("HTML_REMNANTS")
        if has_markdown:
            status.append("MARKDOWN_ARTIFACTS")
        if not has_para_breaks and words > 50:
            status.append("NO_PARAGRAPH_BREAKS")

        status_str = ", ".join(status) if status else "CLEAN"
        print(f"  Ch {i+1:2d}: {ch.title:<40s}  {words:5d} words  [{status_str}]")

        results.append({
            "index": i + 1,
            "title": ch.title,
            "word_count": words,
            "status": status_str,
            "has_paragraph_breaks": has_para_breaks,
            "preview": ch.text[:300],
        })

    return results


def test_chunking(chapters: list, chunk_size: int = 200) -> list:
    """Test chunking on parsed chapters."""
    print(f"\nChunking with chunk_size={chunk_size} words")
    print("-" * 70)

    all_chunks = []
    for i, ch_data in enumerate(chapters):
        from engine.parsers import parse_epub
        # Re-parse to get Chapter objects (we stored previews)
        # Actually just chunk from preview... no, let's re-chunk from full text
        pass

    return all_chunks


def narrate(chapters_data, chapter_indices, max_chunks, chunk_size, api_base="http://localhost:8400", voice_name="pixel"):
    """Narrate selected chapters by calling the running ruru API."""
    import urllib.request
    import urllib.parse
    import re

    from engine.assembler import assemble_chunks

    # Re-parse to get full chapter text
    chapters = parse_epub(EPUB_PATH)

    if chapter_indices:
        selected = [(idx - 1, chapters[idx - 1]) for idx in chapter_indices if 0 < idx <= len(chapters)]
    else:
        selected = list(enumerate(chapters))

    if not selected:
        print("No valid chapters selected.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Find Pixel voice via API
    print(f"\nConnecting to ruru API at {api_base}...")
    try:
        with urllib.request.urlopen(f"{api_base}/api/voices") as resp:
            voices = json.loads(resp.read())
    except Exception as e:
        print(f"ERROR: Could not reach ruru API: {e}")
        print("Is 'uv run app.py' (or the systemd service) running?")
        sys.exit(1)

    voice = next((v for v in voices if voice_name.lower() in v["name"].lower()), None)
    if not voice:
        print(f"Available voices: {[v['name'] for v in voices]}")
        print(f"ERROR: no voice matching '{voice_name}'.")
        sys.exit(1)

    voice_id = voice["id"]
    print(f"Using voice: {voice['name']} (id={voice_id})")

    for ch_idx, chapter in selected:
        print(f"\nNarrating Ch {ch_idx + 1}: {chapter.title} ({chapter.word_count} words)")
        chunks = split_into_chunks(chapter.text, chunk_size)

        if max_chunks and len(chunks) > max_chunks:
            print(f"  Limiting to {max_chunks}/{len(chunks)} chunks")
            chunks = chunks[:max_chunks]

        wav_files = []
        chunk_dir = OUTPUT_DIR / f"chunks_ch{ch_idx + 1:03d}"
        chunk_dir.mkdir(parents=True, exist_ok=True)

        for ci, chunk_text in enumerate(chunks):
            chunk_path = chunk_dir / f"chunk_{ci:05d}.wav"
            print(f"  Chunk {ci + 1}/{len(chunks)}: {len(chunk_text.split())} words...", end=" ", flush=True)

            # POST to /api/tts
            form_data = urllib.parse.urlencode({
                "text": chunk_text,
                "voice_id": voice_id,
                "audio_format": "wav",
            }).encode()
            req = urllib.request.Request(f"{api_base}/api/tts", data=form_data,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    html = resp.read().decode()
            except Exception as e:
                print(f"FAILED: {e}")
                continue

            # Extract audio URL from returned HTML fragment
            m = re.search(r'<source src="([^"]+)"', html)
            if not m:
                print("FAILED: could not parse audio URL from response")
                continue

            audio_url = api_base + m.group(1)
            with urllib.request.urlopen(audio_url) as resp:
                chunk_path.write_bytes(resp.read())

            wav_files.append(chunk_path)
            print("done")

        if not wav_files:
            print("  No chunks generated, skipping assembly.")
            continue

        # Assemble chapter audio
        ch_output = OUTPUT_DIR / f"oz_ch{ch_idx + 1:03d}_{chapter.title[:30].replace(' ', '_')}.mp3"
        ok = assemble_chunks(wav_files, ch_output, "mp3", "128k")
        if ok:
            size_mb = ch_output.stat().st_size / (1024 * 1024)
            print(f"  -> {ch_output.name} ({size_mb:.1f} MB)")
        else:
            print(f"  -> Assembly FAILED")

    print(f"\nAudio files in: {OUTPUT_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Test EPUB parser and optional TTS narration")
    parser.add_argument("--epub", type=Path, default=EPUB_PATH, help="EPUB file to test")
    parser.add_argument("--narrate", action="store_true", help="Generate audio via ruru API")
    parser.add_argument("--chapters", type=str, default="", help="Comma-separated chapter numbers to narrate (e.g. 1,2,3)")
    parser.add_argument("--max-chunks", type=int, default=0, help="Max chunks per chapter (0=all)")
    parser.add_argument("--chunk-size", type=int, default=200, help="Words per chunk")
    parser.add_argument("--api", type=str, default="http://localhost:8400", help="ruru API base URL")
    parser.add_argument("--voice", type=str, default="pixel paradox", help="Voice name to use (default: pixel paradox)")
    args = parser.parse_args()

    if not args.epub.exists():
        print(f"EPUB not found: {args.epub}")
        sys.exit(1)

    # Phase 1: Parse and inspect text
    chapter_results = test_parser(args.epub)

    # Write text results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    text_report = OUTPUT_DIR / "parser_report.json"
    with open(text_report, "w") as f:
        json.dump(chapter_results, f, indent=2)
    print(f"\nParser report: {text_report}")

    # Write full extracted text per chapter
    chapters = parse_epub(args.epub)
    for i, ch in enumerate(chapters):
        ch_file = OUTPUT_DIR / f"ch{i+1:03d}_{ch.title[:40].replace(' ', '_').replace('/', '_')}.txt"
        ch_file.write_text(ch.text, encoding="utf-8")
    print(f"Chapter text files: {OUTPUT_DIR}/ch*.txt")

    # Summary stats
    total_words = sum(r["word_count"] for r in chapter_results)
    clean_count = sum(1 for r in chapter_results if r["status"] == "CLEAN")
    para_count = sum(1 for r in chapter_results if r["has_paragraph_breaks"])
    print(f"\nSummary: {len(chapters)} chapters, {total_words} total words")
    print(f"  Clean (no artifacts): {clean_count}/{len(chapters)}")
    print(f"  Has paragraph breaks: {para_count}/{len(chapters)}")

    # Phase 2: Narrate (optional, calls running ruru API)
    if args.narrate:
        chapter_indices = [int(x) for x in args.chapters.split(",") if x.strip()] if args.chapters else []
        narrate(chapter_results, chapter_indices, args.max_chunks, args.chunk_size, args.api, args.voice)


if __name__ == "__main__":
    main()
