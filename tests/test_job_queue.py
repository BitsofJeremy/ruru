"""Tests for jobs/queue.py — JobRunner, JobType, enqueue/dequeue."""
import asyncio
import pytest
from unittest.mock import patch, MagicMock

from jobs.queue import JobRunner, JobType, get_runner
from jobs.models import JobStatus


class TestJobType:
    def test_all_job_types_exist(self):
        assert JobType.GENERATE_TTS.value == "generate_tts"
        assert JobType.NARRATE_DOCUMENT.value == "narrate_document"
        assert JobType.REGENERATE_CHAPTER.value == "regenerate_chapter"


class TestJobRunner:
    def test_start_stop(self):
        runner = JobRunner()
        runner.start()
        assert runner._running is True
        runner.stop()
        assert runner._running is False

    def test_enqueue_returns_future(self):
        runner = JobRunner()
        runner.start()
        try:
            future = asyncio.get_event_loop().run_until_complete(
                runner.enqueue(1, JobType.GENERATE_TTS, {"text": "hello"})
            )
            assert isinstance(future, asyncio.Future)
        finally:
            runner.stop()

    def test_double_start_is_noop(self):
        runner = JobRunner()
        runner.start()
        runner.start()  # Should not raise
        runner.stop()


class TestGetRunner:
    def test_singleton(self):
        import jobs.queue as q
        # Reset singleton
        q._runner = None
        runner1 = get_runner()
        runner2 = get_runner()
        assert runner1 is runner2
        runner1.stop()