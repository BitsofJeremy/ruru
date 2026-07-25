"""In-process asyncio job runner — replaces Celery/Redis."""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class JobType(str, Enum):
    GENERATE_TTS = "generate_tts"
    NARRATE_DOCUMENT = "narrate_document"
    REGENERATE_CHAPTER = "regenerate_chapter"


@dataclass
class QueuedJob:
    job_id: int
    job_type: JobType
    payload: dict
    future: asyncio.Future


class JobRunner:
    """In-process job runner using a background thread + asyncio queue.

    Jobs are stored in SQLite by the API layer. This runner dequeues
    them and dispatches to the sync worker thread. Progress writes go
    directly to SQLite (same as before).
    """

    def __init__(self):
        self._queue: asyncio.Queue[QueuedJob] = asyncio.Queue()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        """Start the background thread that runs the sync event loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._sync_worker, daemon=True, name="JobRunner")
        self._thread.start()
        logger.info("JobRunner started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("JobRunner stopped")

    def _sync_worker(self):
        """Runs in a background thread, drives the asyncio event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run_jobs())
        loop.close()

    async def _run_jobs(self):
        """Async job loop — wait for jobs and dispatch to sync thread pool."""
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            logger.info(f"JobRunner: dispatching {job.job_type.value} job_id={job.job_id}")
            try:
                if job.job_type == JobType.GENERATE_TTS:
                    from jobs.tasks import run_generate_tts
                    result = await asyncio.to_thread(run_generate_tts, job.job_id, **job.payload)
                elif job.job_type == JobType.NARRATE_DOCUMENT:
                    from jobs.tasks import run_narrate_document
                    result = await asyncio.to_thread(run_narrate_document, job.job_id, **job.payload)
                elif job.job_type == JobType.REGENERATE_CHAPTER:
                    from jobs.tasks import run_regenerate_chapter
                    result = await asyncio.to_thread(run_regenerate_chapter, job.job_id, **job.payload)
                else:
                    result = {"status": "error", "message": f"Unknown job type: {job.job_type}"}
                job.future.set_result(result)
            except Exception as e:
                logger.error(f"Job {job.job_id} failed: {e}", exc_info=True)
                job.future.set_exception(e)

    async def enqueue(self, job_id: int, job_type: JobType, payload: dict) -> asyncio.Future:
        """Enqueue a job and return a Future that resolves with the result."""
        future: asyncio.Future = asyncio.Future()
        await self._queue.put(QueuedJob(job_id=job_id, job_type=job_type, payload=payload, future=future))
        return future


# Global singleton
_runner: JobRunner | None = None


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner