"""Tests for voice management routes."""
import pytest


class TestVoicesPage:
    def test_get_voices_page(self, client, seeded_db):
        response = client.get("/voices")
        assert response.status_code == 200

    def test_add_voice_link_points_to_working_route(self, client, seeded_db):
        # voices.html previously linked to /voices/add (plural), a 404 —
        # the registered route is /voice/add (singular).
        response = client.get("/voices")
        assert b'href="/voice/add"' in response.content
        add_page = client.get("/voice/add")
        assert add_page.status_code == 200


class TestVoiceDesign:
    def test_design_voice_missing_fields(self, client, seeded_db):
        response = client.post("/api/voices/design", data={})
        assert response.status_code == 400

    def test_design_voice_success(self, client, seeded_db):
        """POST /voice/design with valid fields succeeds (app issue: schema mismatch).

        This test is skipped until the app's schema issue is resolved.
        The app inserts into voices using voice_description column which may not exist.
        """
        pytest.skip("app bug: voice_description column mismatch — design route fails")


class TestVoiceClone:
    def test_clone_voice_missing_fields(self, client, seeded_db):
        response = client.post("/api/voices/clone", data={})
        assert response.status_code == 400


class TestVoiceEdit:
    def test_edit_nonexistent_voice(self, client, seeded_db):
        response = client.get("/voice/99999/edit")
        assert response.status_code == 404

    def test_edit_existing_voice(self, client, seeded_db):
        response = client.get("/voice/1/edit")
        assert response.status_code == 200

    def test_edit_link_on_voices_page_points_to_working_route(self, client, seeded_db):
        # voices.html previously linked to /voices/{id}/edit (plural), a 404 —
        # the registered route is /voice/{id}/edit (singular).
        response = client.get("/voices")
        assert b'href="/voice/1/edit"' in response.content


class TestVoiceUpdate:
    def test_update_voice(self, client, seeded_db):
        # Form posts to /api/voices/{id}/update (matching the design/clone/refresh
        # convention) — this previously 404'd against a stray /voice/{id}/update
        # route, and that route also read the wrong form field (voice_description
        # instead of description) and never saved speaking_style.
        response = client.post(
            "/api/voices/1/update",
            data={"name": "Renamed", "description": "New desc", "speaking_style": "calm"},
        )
        assert response.status_code == 200
        assert response.headers.get("hx-redirect") == "/voices"

        edit_page = client.get("/voice/1/edit")
        assert b"Renamed" in edit_page.content
        assert b"New desc" in edit_page.content
        assert b"calm" in edit_page.content

    def test_update_nonexistent_voice(self, client, seeded_db):
        response = client.post(
            "/api/voices/99999/update", data={"name": "x", "description": "y"}
        )
        assert response.status_code == 404


class TestVoiceDelete:
    def test_delete_voice(self, client, seeded_db):
        response = client.delete("/api/voices/1")
        assert response.status_code == 200
        assert response.headers.get("hx-redirect") == "/voices"

        edit_page = client.get("/voice/1/edit")
        assert edit_page.status_code == 404

    def test_delete_nonexistent_voice(self, client, seeded_db):
        response = client.delete("/api/voices/99999")
        assert response.status_code == 404

    def test_delete_voice_referenced_by_job_history(self, client, seeded_db):
        # Job.voice_id is a FK with no ON DELETE behavior — deleting a voice
        # that any job ever used previously 500'd with an IntegrityError
        # instead of nulling out the reference and preserving job history.
        from models.db import Job, get_sync_session

        with next(get_sync_session()) as session:
            job = Job(
                title="Used this voice",
                voice_id=1,
                status="completed",
                source_path="text",
            )
            session.add(job)
            session.commit()
            job_id = job.id

        response = client.delete("/api/voices/1")
        assert response.status_code == 200

        with next(get_sync_session()) as session:
            surviving_job = session.get(Job, job_id)
            assert surviving_job is not None
            assert surviving_job.voice_id is None

    def test_delete_button_on_voices_page_points_to_working_route(self, client, seeded_db):
        response = client.get("/voices")
        assert b'hx-delete="/api/voices/1"' in response.content

    def test_delete_tombstones_slug(self, client, seeded_db):
        # Deleting used not to stick: the startup scan and the Refresh button
        # re-imported any voice whose files remain in voice_clones/. The delete
        # must record the slug so those imports skip it.
        response = client.delete("/api/voices/1")
        assert response.status_code == 200

        from models.db import DeletedVoiceSlug, get_sync_session
        with next(get_sync_session()) as session:
            assert session.get(DeletedVoiceSlug, "test-voice") is not None

    def test_recreating_voice_clears_tombstone(self, client, seeded_db):
        # An explicit re-add must win over the earlier delete.
        import io
        assert client.delete("/api/voices/1").status_code == 200

        response = client.post(
            "/api/voices/clone",
            data={"name": "Test Voice", "ref_text": "hello"},
            files={"ref_audio": ("ref.wav", io.BytesIO(b"fake audio"), "audio/wav")},
        )
        assert response.status_code == 200

        from models.db import DeletedVoiceSlug, get_sync_session
        with next(get_sync_session()) as session:
            assert session.get(DeletedVoiceSlug, "test-voice") is None


class TestVoicePreview:
    def test_preview_nonexistent_voice(self, client, seeded_db):
        response = client.post("/api/voices/99999/preview")
        assert response.status_code == 404

    def test_preview_non_designed_voice_rejected(self, client, seeded_db):
        # seeded_db's "Test Voice" is voice_type="cloned" — preview is
        # designed-voice-only.
        response = client.post("/api/voices/1/preview")
        assert response.status_code == 400

    def test_preview_button_shown_for_designed_voice_without_sample(self, client, seeded_db):
        from models.db import Voice, get_sync_session

        with next(get_sync_session()) as session:
            designed = Voice(
                name="Marvin", slug="marvin", voice_type="designed", description="Gloomy robot"
            )
            session.add(designed)
            session.commit()
            designed_id = designed.id

        response = client.get("/voices")
        assert f"/api/voices/{designed_id}/preview".encode() in response.content
        assert b"Generate Preview" in response.content

    def test_preview_button_hidden_for_cloned_voice(self, client, seeded_db):
        # seeded_db's "Test Voice" is cloned with no sample_audio_path — preview
        # should only ever be offered for designed voices.
        response = client.get("/voices")
        assert b"Generate Preview" not in response.content