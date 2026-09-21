"""Tests for engine.parsers — EPUB and TXT parsing."""
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from engine.parsers import (
    Chapter,
    _clean_html,
    _is_non_content,
    _looks_like_heading,
    parse_document,
    parse_epub,
    parse_txt,
)


class TestCleanHtml:
    def test_strips_heading_markers(self):
        html = "<h1>Title</h1><p>Content</p>"
        text = _clean_html(html)
        assert "#" not in text
        assert "Title" in text

    def test_strips_list_markers(self):
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        text = _clean_html(html)
        assert "-" not in text

    def test_collapse_excessive_newlines(self):
        html = "<p>A</p>\n\n\n\n\n<p>B</p>"
        text = _clean_html(html)
        assert text.count("\n\n") <= 1

    def test_empty_input(self):
        assert _clean_html("") == ""
        assert _clean_html(None) == ""

    def test_html_with_emphasis(self):
        html = "<p>This is <strong>bold</strong> and <em>italic</em>.</p>"
        text = _clean_html(html)
        assert "bold" in text
        assert "italic" in text


class TestIsNonContent:
    def test_cover_filename(self):
        assert _is_non_content("", "cover.xhtml") is True

    def test_toc_filename(self):
        assert _is_non_content("", "toc.xhtml") is True

    def test_nav_element(self):
        html = '<nav role="doc-toc"><p>Links...</p></nav>'
        assert _is_non_content(html) is True

    def test_regular_content(self):
        html = "<p>This is a chapter paragraph.</p>"
        assert _is_non_content(html, "chapter1.xhtml") is False

    def test_gutenberg_boilerplate(self):
        html = "project gutenberg license"
        assert _is_non_content(html) is True


class TestParseTxt:
    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        chapters = parse_txt(f)
        assert chapters == []

    def test_single_chapter_no_title(self, tmp_path):
        f = tmp_path / "single.txt"
        f.write_text("This is some text without triple newlines.")
        chapters = parse_txt(f)
        assert len(chapters) == 1

    def test_multiple_chapters_split_by_triple_newline(self, tmp_path):
        f = tmp_path / "multi.txt"
        f.write_text("Chapter one text.\n\n\nChapter two text.")
        chapters = parse_txt(f)
        assert len(chapters) == 2

    def test_first_line_as_title(self, tmp_path):
        f = tmp_path / "titled.txt"
        f.write_text("My Short Title\n\nThis is the body of the chapter.")
        chapters = parse_txt(f)
        assert chapters[0].title == "My Short Title"
        assert "body" in chapters[0].text

    def test_long_first_line_not_used_as_title(self, tmp_path):
        f = tmp_path / "longtitle.txt"
        long_line = "x" * 100
        f.write_text(f"{long_line}\n\nBody text here.")
        chapters = parse_txt(f)
        # Should use default section title since first line > 80 chars
        assert chapters[0].title.startswith("Section")

    # -- Regression: first-line-drop defect (parsers.py first-line
    # title extraction misfiring on scripts/letters) ------------------------

    def test_default_mode_does_not_strip_sentence_first_line(self, tmp_path):
        """A short line ending in sentence punctuation is prose, not a
        heading -- the narrated text must keep it (this was the bug: the
        opening line of narration was silently swallowed as a 'title')."""
        f = tmp_path / "narration.txt"
        f.write_text(
            "Good evening. You're listening to the night desk.\n\n"
            "More narration text follows here."
        )
        chapters = parse_txt(f)
        assert len(chapters) == 1
        assert chapters[0].title.startswith("Section")
        assert chapters[0].text.startswith("Good evening.")

    def test_default_mode_handles_trailing_quote_after_punctuation(self, tmp_path):
        """A first line ending in punctuation followed by a closing quote
        (e.g. dialogue: `She said no."`) still reads as prose."""
        f = tmp_path / "quoted.txt"
        f.write_text(
            'She looked at him and said, "not tonight."\n\n'
            "The rest of the scene continues here."
        )
        chapters = parse_txt(f)
        assert chapters[0].text.startswith("She looked at him")

    def test_default_mode_still_strips_genuine_heading(self, tmp_path):
        """Real heading-style documents must keep working: a short first
        line with no sentence punctuation is still promoted to the title."""
        f = tmp_path / "titled.txt"
        f.write_text("My Short Title\n\nThis is the body of the chapter.")
        chapters = parse_txt(f)
        assert chapters[0].title == "My Short Title"
        assert "body" in chapters[0].text
        assert "My Short Title" not in chapters[0].text

    def test_verbatim_mode_never_strips_first_line(self, tmp_path):
        """verbatim=True must never extract a title, even for a first line
        that would otherwise look like a genuine heading."""
        f = tmp_path / "titled.txt"
        f.write_text("My Short Title\n\nThis is the body of the chapter.")
        chapters = parse_txt(f, verbatim=True)
        assert len(chapters) == 1
        assert chapters[0].title == "Section 1"
        assert chapters[0].text == "My Short Title\n\nThis is the body of the chapter."

    def test_verbatim_mode_preserves_sentence_first_line_too(self, tmp_path):
        f = tmp_path / "narration.txt"
        f.write_text(
            "Good evening. You're listening to the night desk.\n\n"
            "More narration text follows here."
        )
        chapters = parse_txt(f, verbatim=True)
        assert chapters[0].text.startswith("Good evening.")

    def test_verbatim_mode_multiple_segments_all_full(self, tmp_path):
        f = tmp_path / "multi.txt"
        f.write_text("Heading One\n\nBody one.\n\n\nHeading Two\n\nBody two.")
        chapters = parse_txt(f, verbatim=True)
        assert len(chapters) == 2
        assert chapters[0].text == "Heading One\n\nBody one."
        assert chapters[1].text == "Heading Two\n\nBody two."
        assert [c.title for c in chapters] == ["Section 1", "Section 2"]

    def test_looks_like_heading_helper(self):
        assert _looks_like_heading("My Short Title") is True
        assert _looks_like_heading("Good evening.") is False
        assert _looks_like_heading('She said "not tonight."') is False
        assert _looks_like_heading("Wait, really?") is False
        assert _looks_like_heading("A pause, then silence;") is False
        assert _looks_like_heading("x" * 100) is False  # too long either way


class TestParseDocumentVerbatim:
    def test_parse_document_threads_verbatim_for_txt(self, tmp_path):
        f = tmp_path / "titled.txt"
        f.write_text("My Short Title\n\nThis is the body of the chapter.")
        default_chapters = parse_document(f)
        verbatim_chapters = parse_document(f, verbatim=True)
        assert default_chapters[0].title == "My Short Title"
        assert verbatim_chapters[0].title == "Section 1"
        assert "My Short Title" in verbatim_chapters[0].text


class TestParseEpub:
    def test_epub_no_file(self, tmp_path):
        with pytest.raises(RuntimeError):
            parse_epub(tmp_path / "nonexistent.epub")

    def test_epub_invalid_file(self, tmp_path):
        f = tmp_path / "bad.epub"
        f.write_text("not a zip")
        with pytest.raises(RuntimeError):
            parse_epub(f)

    def test_epub_minimal_valid(self, tmp_path):
        """Create a minimal valid EPUB and verify parsing."""
        epub_path = tmp_path / "minimal.epub"

        # Minimal EPUB structure
        container_xml = '''<?xml version="1.0"?>
        <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
          <rootfiles>
            <rootfile full-path="OEPBS/content.opf" media-type="application/oebps-package+xml"/>
          </rootfiles>
        </container>'''

        content_opf = '''<?xml version="1.0"?>
        <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
          <metadata>...</metadata>
          <manifest>
            <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
          </manifest>
          <spine>
            <itemref idref="chapter1"/>
          </spine>
        </package>'''

        chapter_html = '''<?xml version="1.0"?>
        <html xmlns="http://www.w3.org/1999/xhtml">
          <head><title>Test Chapter</title></head>
          <body>
            <h1>Test Chapter</h1>
            <p>This is a test paragraph with enough words to pass the chapter minimum threshold check. More words here to ensure we have at least 20 words in this chapter.</p>
          </body>
        </html>'''

        with zipfile.ZipFile(epub_path, "w") as zf:
            zf.writestr("META-INF/container.xml", container_xml)
            zf.writestr("OEPBS/content.opf", content_opf)
            zf.writestr("OEPBS/chapter1.xhtml", chapter_html)

        chapters = parse_epub(epub_path)
        assert len(chapters) >= 1
        assert any("Test Chapter" in ch.title or "test" in ch.text.lower() for ch in chapters)


class TestChapter:
    def test_word_count(self):
        ch = Chapter(title="Test", text="one two three four five")
        assert ch.word_count == 5
