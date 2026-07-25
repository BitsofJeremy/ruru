"""Document parsers for ruru.

Supports EPUB (via ebooklib + BS4 with zipfile fallback) and plain text files.
Heavy imports (ebooklib, bs4, html2text) are lazy-loaded to keep startup fast.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from html import unescape
from pathlib import Path


@dataclass
class Chapter:
    """A single chapter extracted from a document."""
    title: str
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

_MIN_CHAPTER_WORDS = 20


def _clean_html(html_content: str) -> str:
    """Convert HTML to clean prose text suitable for TTS.

    Uses html2text for structure-aware conversion that preserves paragraph
    breaks, then strips any remaining markdown artifacts. Falls back to
    improved regex if html2text is unavailable.
    """
    if not html_content:
        return ""
    try:
        import html2text

        h = html2text.HTML2Text()
        h.body_width = 0
        h.ignore_links = True
        h.ignore_images = True
        h.ignore_emphasis = True
        h.ignore_tables = True
        text = h.handle(html_content)
        # Strip heading markers (# ## ###) and list markers (- * 1.)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[\-\*]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
        # Collapse 3+ newlines to double-newline (paragraph break)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except ImportError:
        pass
    # Regex fallback — insert newlines before block elements for paragraph breaks
    html_content = re.sub(
        r"<style[^>]*>.*?</style>", "", html_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html_content = re.sub(
        r"<script[^>]*>.*?</script>", "", html_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html_content = re.sub(
        r"<(?:p|div|br|h[1-6]|li|tr|blockquote)[^>]*>",
        "\n", html_content, flags=re.IGNORECASE,
    )
    html_content = re.sub(r"<[^>]+>", " ", html_content)
    html_content = unescape(html_content)
    # Normalise whitespace within lines, preserve newlines
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in html_content.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Non-content page filtering
# ---------------------------------------------------------------------------

_NON_CONTENT_FILENAME_PATTERNS = re.compile(
    r"(?:cover|toc|nav|copyright|colophon|titlepage|dedication|frontmatter"
    r"|halftitle|about|endorsements|also-by|series-page|license|wrap)",
    re.IGNORECASE,
)

_NON_CONTENT_EPUB_TYPES = re.compile(
    r'epub:type\s*=\s*"[^"]*(?:toc|landmarks|lot|loi|cover|copyright'
    r"|titlepage|dedication|colophon|frontmatter)",
    re.IGNORECASE,
)


def _is_non_content(html: str, filename: str = "") -> bool:
    """Check if an HTML page is non-content (cover, TOC, copyright, etc.)."""
    basename = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() if filename else ""
    if basename and _NON_CONTENT_FILENAME_PATTERNS.search(basename):
        return True
    if _NON_CONTENT_EPUB_TYPES.search(html[:2000]):
        return True
    # EPUB3 nav element
    if '<nav ' in html[:2000] and 'role="doc-toc"' in html[:2000]:
        return True
    # Project Gutenberg boilerplate
    if "project gutenberg" in html[:3000].lower() and (
        "license" in html[:3000].lower() or "ebook" in html[:500].lower()
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Title extraction (regex-based, no BS4 needed)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(
    r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.IGNORECASE | re.DOTALL
)


def _extract_title_from_html(html: str) -> str | None:
    """Extract the first h1/h2/h3 text from HTML using regex."""
    m = _HEADING_RE.search(html)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        # Collapse internal whitespace/newlines to single space
        title = re.sub(r"\s+", " ", title)
        if title:
            return title
    return None


# ---------------------------------------------------------------------------
# EPUB parsing — zipfile OPF spine helpers
# ---------------------------------------------------------------------------


def _find_opf_path(zf: zipfile.ZipFile) -> str | None:
    """Read META-INF/container.xml to find the OPF file path."""
    try:
        container = zf.read("META-INF/container.xml").decode("utf-8", errors="ignore")
        m = re.search(r'full-path\s*=\s*"([^"]+\.opf)"', container, re.IGNORECASE)
        return m.group(1) if m else None
    except (KeyError, Exception):
        return None


def _get_spine_ordered_files(zf: zipfile.ZipFile, opf_path: str) -> list[str]:
    """Parse OPF manifest + spine to get content files in reading order."""
    try:
        opf = zf.read(opf_path).decode("utf-8", errors="ignore")
    except (KeyError, Exception):
        return []

    # OPF directory for resolving relative hrefs
    opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

    # Build manifest: id -> href (handles attribute order variation)
    manifest: dict[str, str] = {}
    for item_match in re.finditer(r"<item\s[^>]*?>", opf, re.IGNORECASE | re.DOTALL):
        tag = item_match.group(0)
        id_m = re.search(r'id\s*=\s*"([^"]+)"', tag)
        href_m = re.search(r'href\s*=\s*"([^"]+)"', tag)
        media_m = re.search(r'media-type\s*=\s*"([^"]+)"', tag)
        if id_m and href_m:
            media = media_m.group(1) if media_m else ""
            if "html" in media or "xml" in media:
                manifest[id_m.group(1)] = href_m.group(1)

    # Get spine order
    spine_ids: list[str] = []
    for itemref in re.finditer(r"<itemref\s[^>]*?>", opf, re.IGNORECASE | re.DOTALL):
        tag = itemref.group(0)
        idref_m = re.search(r'idref\s*=\s*"([^"]+)"', tag)
        if idref_m:
            spine_ids.append(idref_m.group(1))

    ordered: list[str] = []
    for sid in spine_ids:
        href = manifest.get(sid)
        if href:
            full_path = opf_dir + href
            if full_path in zf.namelist():
                ordered.append(full_path)
    return ordered


# ---------------------------------------------------------------------------
# EPUB parsing
# ---------------------------------------------------------------------------


def _parse_epub_ebooklib(file_path: Path) -> list[Chapter]:
    """Extract chapters from EPUB using ebooklib (primary method)."""
    import ebooklib  # lazy
    from ebooklib import epub  # lazy
    from bs4 import BeautifulSoup  # lazy

    book = epub.read_epub(str(file_path))
    chapters: list[Chapter] = []
    chapter_num = 0

    for item_id, _linear in book.spine:
        try:
            item = book.get_item_by_id(item_id)
            if item and isinstance(item, ebooklib.ITEM_DOCUMENT):
                content = item.get_body_content()
                if not content:
                    continue
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="ignore")
                content_str = str(content)

                if _is_non_content(content_str, item.get_name()):
                    continue

                # Detect title from heading tags
                title: str | None = None
                try:
                    soup = BeautifulSoup(content_str, "html.parser")
                    for tag_name in ("h1", "h2", "h3"):
                        heading = soup.find(tag_name)
                        if heading:
                            title = re.sub(r"\s+", " ", heading.get_text(strip=True))
                            break
                except Exception:
                    pass

                if not title:
                    title = _extract_title_from_html(content_str)

                clean_text = _clean_html(content_str)
                if len(clean_text.split()) < _MIN_CHAPTER_WORDS:
                    continue

                if not title:
                    chapter_num += 1
                    title = f"Chapter {chapter_num}"

                chapters.append(Chapter(title=title, text=clean_text))
        except Exception:
            continue

    return chapters


def _parse_epub_zipfile(file_path: Path) -> list[Chapter]:
    """Fallback: extract chapters from EPUB zip using OPF spine order."""
    chapters: list[Chapter] = []
    chapter_num = 0
    with zipfile.ZipFile(file_path, "r") as zf:
        # Try OPF spine order first, fall back to namelist
        opf_path = _find_opf_path(zf)
        if opf_path:
            html_files = _get_spine_ordered_files(zf, opf_path)
        else:
            html_files = []

        if not html_files:
            html_files = [
                name for name in zf.namelist()
                if name.lower().endswith((".html", ".xhtml", ".htm"))
            ]

        for name in html_files:
            try:
                raw = zf.read(name).decode("utf-8", errors="ignore")
                if _is_non_content(raw, name):
                    continue
                clean = _clean_html(raw)
                if len(clean.split()) < _MIN_CHAPTER_WORDS:
                    continue

                title = _extract_title_from_html(raw)
                if not title:
                    chapter_num += 1
                    title = f"Chapter {chapter_num}"

                chapters.append(Chapter(title=title, text=clean))
            except Exception:
                continue
    return chapters


def parse_epub(file_path: Path) -> list[Chapter]:
    """Extract chapters from an EPUB file.

    Tries ebooklib first for proper spine-order + title detection,
    then falls back to raw zipfile extraction.

    Args:
        file_path: Path to the EPUB file.

    Returns:
        List of Chapter objects.

    Raises:
        RuntimeError: If no text could be extracted.
    """
    for method in (_parse_epub_ebooklib, _parse_epub_zipfile):
        try:
            chapters = method(file_path)
            if chapters:
                return chapters
        except Exception:
            continue
    raise RuntimeError(f"Failed to extract text from {file_path}")


# ---------------------------------------------------------------------------
# Plain text parsing
# ---------------------------------------------------------------------------


# A line ending in sentence punctuation (optionally followed by closing
# quotes/brackets, e.g. `He said no."`) reads as prose, not a heading — even
# when it's short enough to otherwise pass the heading-length check.
_SENTENCE_END_RE = re.compile(r"[.!?,;][\"'”’)\]]*$")


def _looks_like_heading(first_line: str) -> bool:
    """Heuristic: is `first_line` a heading rather than a spoken sentence?

    Headings are short (<=80 chars) and don't end in terminal/clause
    punctuation. A short line that ends like a sentence (e.g. "Good evening.")
    is prose that happens to be brief, not a title to strip.
    """
    return len(first_line) <= 80 and not _SENTENCE_END_RE.search(first_line)


def parse_txt(file_path: Path, verbatim: bool = False) -> list[Chapter]:
    """Parse a plain text file into chapters.

    Splits on triple-newline boundaries. By default, if a segment starts with
    a short first line (<=80 chars) that doesn't read like a sentence (see
    `_looks_like_heading`), that line is promoted to the chapter title and
    only the remaining text is narrated.

    Args:
        file_path: Path to the text file.
        verbatim: If True, never extract a title — every segment is narrated
            in full, untouched. Use this for scripts/letters/narration text
            where every line must be spoken (heading-detection is meant for
            heading-style documents and otherwise silently drops the first
            spoken line of a segment).

    Returns:
        List of Chapter objects.
    """
    raw = file_path.read_text(encoding="utf-8", errors="ignore")
    segments = re.split(r"\n{3,}", raw)
    chapters: list[Chapter] = []
    chapter_num = 0

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        if verbatim:
            chapter_num += 1
            chapters.append(Chapter(title=f"Section {chapter_num}", text=segment))
            continue

        lines = segment.split("\n", 1)
        first_line = lines[0].strip()

        if len(lines) > 1 and _looks_like_heading(first_line):
            title = first_line
            text = lines[1].strip()
        else:
            chapter_num += 1
            title = f"Section {chapter_num}"
            text = segment

        if text:
            chapters.append(Chapter(title=title, text=text))

    return chapters


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_PARSERS = {
    ".epub": parse_epub,
    ".txt": parse_txt,
    ".text": parse_txt,
}


def parse_document(file_path: Path, verbatim: bool = False) -> list[Chapter]:
    """Parse a document into chapters based on file extension.

    Supported formats: .epub, .txt, .text

    Args:
        file_path: Path to the document.
        verbatim: If True, disable title/heading extraction for formats that
            support it (currently .txt/.text — see `parse_txt`). Ignored for
            formats with no heading heuristic (e.g. .epub, which relies on
            real document structure, not a first-line guess).

    Returns:
        List of Chapter objects.

    Raises:
        ValueError: If the file extension is not supported.
    """
    ext = file_path.suffix.lower()
    parser = _PARSERS.get(ext)
    if parser is None:
        raise ValueError(
            f"Unsupported file format: {ext!r}. "
            f"Supported: {', '.join(sorted(_PARSERS))}"
        )
    if ext in (".txt", ".text"):
        return parse_txt(file_path, verbatim=verbatim)
    return parser(file_path)
