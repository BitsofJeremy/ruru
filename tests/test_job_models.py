"""Tests for jobs/models.py — JobStatus enum and dataclasses."""
from jobs.models import JobStatus, TTSJob, NarrateJob


class TestJobStatus:
    def test_all_statuses_exist(self):
        assert JobStatus.QUEUED.value == "queued"
        assert JobStatus.RUNNING.value == "running"
        assert JobStatus.COMPLETED.value == "completed"
        assert JobStatus.FAILED.value == "failed"
        assert JobStatus.CANCELLED.value == "cancelled"

    def test_status_is_string_enum(self):
        assert isinstance(JobStatus.QUEUED, str)


class TestTTSJob:
    def test_tts_job_creation(self):
        job = TTSJob(
            job_id=1,
            text="Hello world",
            voice_id=5,
            voice_type="clone",
            voice_description="",
            ref_audio_path="/path/to/audio.wav",
            ref_text="Hello",
            audio_format="wav",
        )
        assert job.job_id == 1
        assert job.text == "Hello world"
        assert job.voice_type == "clone"


class TestNarrateJob:
    def test_narrate_job_creation(self):
        job = NarrateJob(
            job_id=2,
            chunks=["chunk1", "chunk2"],
            voice_type="designed",
            voice_description="A calm voice",
            ref_audio_path="",
            ref_text="",
            audio_format="mp3",
            job_title="Test Book",
            chapter_boundaries=[],
            author="Test Author",
            cover_path="",
        )
        assert job.job_id == 2
        assert len(job.chunks) == 2
        assert job.job_title == "Test Book"
