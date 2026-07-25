"""Tests for narration routes."""
from unittest.mock import AsyncMock

import pytest


class TestNarratePage:
    def test_get_narrate_page(self, client, seeded_db):
        response = client.get("/narrate")
        assert response.status_code == 200


class TestAPINarrate:
    def test_narrate_missing_voice(self, client, seeded_db):
        # No voice_id → 404 (voice not found) since voice_id=0 doesn't exist
        response = client.post("/api/narrate", data={"job_title": "Test"})
        assert response.status_code == 404

    def test_narrate_missing_document(self, client, seeded_db):
        # Valid voice but no uploaded document → 400 (parse_document needs a file).
        # Guards BUG 2: the route must read the uploaded "document" file and save
        # it before parsing, instead of passing the FormData object to parse_document.
        response = client.post(
            "/api/narrate", data={"job_title": "Test", "voice_id": "1"}
        )
        assert response.status_code == 400


class TestAPINarrateParseMode:
    """Regression coverage for the parse_mode=verbatim threading:
    form field -> app.api_narrate -> parse_document(verbatim=...) -> job
    payload handed to the runner. These exercise the real route (not just
    the parser unit) so a future refactor that breaks the wiring fails here.
    """

    # A first line that is short AND has no sentence punctuation -- the
    # default heading heuristic will strip it as a "title" and it will be
    # missing from the narrated chunks unless parse_mode=verbatim is honored.
    DOC_TEXT = "Opening Line\n\nThe rest of the narration body follows."

    def _post_narrate(self, client, monkeypatch, *, parse_mode=None):
        import app as app_module

        mock_runner = app_module.get_runner()
        mock_runner.enqueue = AsyncMock(return_value=None)

        data = {"job_title": "Test", "voice_id": "1"}
        if parse_mode is not None:
            data["parse_mode"] = parse_mode

        files = {"document": ("narration.txt", self.DOC_TEXT, "text/plain")}
        response = client.post("/api/narrate", data=data, files=files)
        assert response.status_code == 200
        assert mock_runner.enqueue.await_count == 1
        _, _, payload = mock_runner.enqueue.await_args.args
        return payload

    def test_parse_mode_verbatim_keeps_opening_line_in_chunks(self, client, seeded_db, monkeypatch):
        payload = self._post_narrate(client, monkeypatch, parse_mode="verbatim")
        assert payload["parse_mode"] == "verbatim"
        assert any("Opening Line" in chunk for chunk in payload["chunks"])

    def test_parse_mode_default_still_strips_heading(self, client, seeded_db, monkeypatch):
        # No parse_mode field at all -- must preserve today's heading-strip
        # behavior for real heading-style documents (no regression).
        payload = self._post_narrate(client, monkeypatch, parse_mode=None)
        assert payload["parse_mode"] == ""
        assert not any("Opening Line" in chunk for chunk in payload["chunks"])

    def test_parse_mode_unrecognized_value_falls_back_to_default(self, client, seeded_db, monkeypatch):
        payload = self._post_narrate(client, monkeypatch, parse_mode="something-else")
        assert payload["parse_mode"] == "something-else"
        assert not any("Opening Line" in chunk for chunk in payload["chunks"])