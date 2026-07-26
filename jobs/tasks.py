"""Sync job handlers — called via asyncio.to_thread() from JobRunner."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from jobs.models import JobStatus

logger = logging.getLogger(__name__)

# Duration sanity check for run_narrate_document: a chunk whose generated
# audio duration falls far outside the expected speech length for its word
# count is either truncated (hit the MLX token ceiling) or looping/runaway --
# both are the "model falls apart" glitch. Retry up to config.MAX_RETRIES
# times and keep the best-effort last attempt rather than aborting the job.
ASSUMED_WPM = 150
MIN_DURATION_RATIO = 0.4
MAX_DURATION_RATIO = 2.5


def _env(name: str, default: str) -> str:
    """Read RURU_{name} from the environment.

    Mirrors config._env() -- kept local here (rather than imported) so this
    module's env resolution doesn't depend on import order relative to
    config.py.
    """
    return os.environ.get(f"RURU_{name}", default)


WORK_DIR = Path(_env("WORK_DIR", str(Path(__file__).parent.parent.resolve() / "data")))
DATA_DIR = WORK_DIR / "cache"
UPLOAD_DIR = DATA_DIR / "uploads"
CHUNK_DIR = DATA_DIR / "chunks"
OUTPUT_DIR = DATA_DIR / "output"
VOICE_DIR = DATA_DIR / "voices"
DB_PATH = WORK_DIR / "ruru.db"

db_path = DB_PATH
output_dir = OUTPUT_DIR


def _should_clone(payload: dict) -> bool:
    """Decide clone vs design routing based on a usable ref_audio_path.

    Routing by the ``voice_type`` *label* is unreliable: seeded voices are typed
    'ephergent' yet carry a real reference audio file, so the label-based check
    silently funnels every clone-capable voice into the VoiceDesign branch and
    ignores its ref audio. Route on whether a reference audio file actually
    exists on disk instead, falling back to design for anything missing.
    """
    ref_audio = payload.get("ref_audio_path") or ""
    return bool(ref_audio) and Path(ref_audio).is_file()


def run_generate_tts(job_id: int, **payload) -> dict:
    """Handle TTS generation job."""
    from models.db import get_sync_session, Job
    from engine import get_engine_class

    engine = get_engine_class()()

    try:
        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.RUNNING.value
                session.commit()

        text = payload.get("text", "")
        voice_id = payload.get("voice_id")
        voice_type = payload.get("voice_type", "clone")

        logger.info(f"Generating TTS job_id={job_id} voice_type={voice_type}")

        if _should_clone(payload):
            ref_audio = payload.get("ref_audio_path", "")
            ref_text = payload.get("ref_text", "")
            result = engine.generate_clone(text, ref_audio, ref_text)
        else:
            voice_desc = payload.get("voice_description", "")
            result = engine.generate_design(text, voice_desc)

        # Save output
        import os
        os.makedirs(output_dir, exist_ok=True)
        output_path = f"{output_dir}/tts_{job_id}.wav"
        result.write(output_path)

        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.COMPLETED.value
                job.output_path = output_path
                session.commit()

        return {"status": "completed", "output_path": output_path}

    except Exception as e:
        logger.error(f"TTS job {job_id} failed: {e}", exc_info=True)
        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.FAILED.value
                job.error_message = str(e)
                session.commit()
        raise


def run_narrate_document(job_id: int, **payload) -> dict:
    """Handle document narration job."""
    from models.db import get_sync_session, Job
    from engine import get_engine_class
    from engine.chunker import split_into_chunks
    from engine.assembler import assemble_chunks
    from config import MAX_RETRIES

    engine = get_engine_class()()

    try:
        chunks = payload.get("chunks", [])

        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.RUNNING.value
                job.total_chunks = len(chunks)
                session.commit()
        voice_type = payload.get("voice_type", "clone")
        job_title = payload.get("job_title", f"job_{job_id}")

        logger.info(f"Narrating document job_id={job_id} chunks={len(chunks)}")

        # Synthesise each chunk to its own WAV file. assemble_chunks() expects a
        # list of WAV file PATHS (not TTSResult objects) plus an output path, and
        # it writes + post-processes the final file itself.
        import os
        chunk_dir = CHUNK_DIR / f"job_{job_id}"
        os.makedirs(chunk_dir, exist_ok=True)
        audio_format = payload.get("audio_format", "mp3")

        wav_paths = []
        for i, chunk_text in enumerate(chunks):
            # Update progress
            with next(get_sync_session()) as session:
                job = session.get(Job, job_id)
                if job:
                    progress = int((i / len(chunks)) * 100)
                    job.progress = progress
                    job.completed_chunks = i
                    session.commit()

            expected_s = (len(chunk_text.split()) / ASSUMED_WPM) * 60
            audio = None
            for attempt in range(MAX_RETRIES):
                if _should_clone(payload):
                    ref_audio = payload.get("ref_audio_path", "")
                    ref_text = payload.get("ref_text", "")
                    candidate = engine.generate_clone(chunk_text, ref_audio, ref_text)
                else:
                    voice_desc = payload.get("voice_description", "")
                    candidate = engine.generate_design(chunk_text, voice_desc)

                audio = candidate
                actual_s = len(candidate.wav) / candidate.sr
                if (
                    expected_s == 0
                    or MIN_DURATION_RATIO * expected_s <= actual_s <= MAX_DURATION_RATIO * expected_s
                ):
                    break
                logger.warning(
                    "Chunk %d attempt %d/%d looks glitched (expected ~%.1fs, got %.1fs); retrying",
                    i, attempt + 1, MAX_RETRIES, expected_s, actual_s,
                )

            chunk_path = chunk_dir / f"chunk_{i:04d}.wav"
            audio.write(chunk_path)
            wav_paths.append(chunk_path)

        # Assemble final output
        os.makedirs(output_dir, exist_ok=True)
        output_path = f"{output_dir}/narrate_{job_id}.{audio_format}"
        ok = assemble_chunks(wav_paths, Path(output_path), audio_format=audio_format)
        if not ok:
            raise RuntimeError("Assembly produced no audio (all chunks empty/failed)")

        # Mark complete
        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.COMPLETED.value
                job.progress = 100
                job.completed_chunks = len(chunks)
                job.output_path = output_path
                session.commit()

        return {"status": "completed", "output_path": output_path}

    except Exception as e:
        logger.error(f"Narrate job {job_id} failed: {e}", exc_info=True)
        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.FAILED.value
                job.error_message = str(e)
                session.commit()
        raise


def run_regenerate_chapter(job_id: int, **payload) -> dict:
    """Handle chapter regeneration job."""
    from models.db import get_sync_session, Job
    from engine import get_engine_class

    engine = get_engine_class()()

    try:
        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.RUNNING.value
                session.commit()

        chapter_id = payload.get("chapter_id")
        text = payload.get("text", "")
        voice_type = payload.get("voice_type", "clone")

        logger.info(f"Regenerating chapter job_id={job_id} chapter_id={chapter_id}")

        if _should_clone(payload):
            ref_audio = payload.get("ref_audio_path", "")
            ref_text = payload.get("ref_text", "")
            audio = engine.generate_clone(text, ref_audio, ref_text)
        else:
            voice_desc = payload.get("voice_description", "")
            audio = engine.generate_design(text, voice_desc)

        import os
        os.makedirs(output_dir, exist_ok=True)
        output_path = f"{output_dir}/chapter_{chapter_id}_{job_id}.wav"
        audio.write(output_path)

        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.COMPLETED.value
                job.output_path = output_path
                session.commit()

        return {"status": "completed", "output_path": output_path}

    except Exception as e:
        logger.error(f"Regenerate chapter job {job_id} failed: {e}", exc_info=True)
        with next(get_sync_session()) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.FAILED.value
                job.error_message = str(e)
                session.commit()
        raise

