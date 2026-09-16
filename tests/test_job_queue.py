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
        assert JobType.GENERATE_MUSIC.value == "generate_music"


class TestGenerateMusicDispatch:
    def test_generate_music_routes_to_handler(self):
        """GENERATE_MUSIC dispatches to jobs.tasks.run_generate_music."""
        runner = JobRunner()
        runner.start()
        try:
            with patch(
                "jobs.tasks.run_generate_music",
                MagicMock(return_value={"status": "completed", "output_path": "/tmp/x.wav"}),
            ):
                async def scenario():
                    future = await runner.enqueue(1, JobType.GENERATE_MUSIC, {"style": "lofi"})
                    return await asyncio.wait_for(future, timeout=5)

                result = asyncio.run(scenario())
            assert result == {"status": "completed", "output_path": "/tmp/x.wav"}
        finally:
            runner.stop()


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
        # The background thread may dequeue this before stop(). Stub the handler
        # so it never reaches the real one, which needs a configured DB path and
        # would otherwise make this test depend on run order.
        try:
            with patch("jobs.tasks.run_generate_tts", MagicMock(return_value={})):
                future = asyncio.run(
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