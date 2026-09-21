"""ruru — Audiobook narration engine powered by Qwen3-TTS."""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite
import sqlalchemy
from sqlalchemy import func
import uvicorn
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydub import AudioSegment

# Bootstrap: add repo root to sys.path so ruru's local modules import correctly
_REPO_ROOT = Path(__file__).parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import (
    WORK_DIR,
    DB_PATH,
    UPLOAD_DIR,
    CHUNK_DIR,
    OUTPUT_DIR,
    VOICE_DIR,
    HOST,
    PORT,
    APP_VERSION,
)
from jobs.queue import get_runner, JobType
from models.db import init_db, get_async_session
from models.scan_voice_clones import scan_voice_clones

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# =============================================================================
# Paths & Config
# =============================================================================

# Ensure required directories exist at startup (before StaticFiles mount)
for _dir in (UPLOAD_DIR, CHUNK_DIR, OUTPUT_DIR, VOICE_DIR, WORK_DIR / "cache"):
    _dir.mkdir(parents=True, exist_ok=True)

# Ensure required directories exist at startup (before StaticFiles mount)
for _dir in (UPLOAD_DIR, CHUNK_DIR, OUTPUT_DIR, VOICE_DIR, WORK_DIR / "cache"):
    _dir.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Database helpers
# =============================================================================

async def get_db():
    async for session in get_async_session():
        yield session

@contextmanager
def get_db_sync():
    from models.db import get_sync_session
    yield next(get_sync_session())


# =============================================================================
# Template setup
# =============================================================================

template_dir = _REPO_ROOT / "templates"
env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
env.filters['basename'] = lambda p: Path(p).name

# Globals, so every template gets these without each route passing them. base.html
# renders both in the sidebar; app_version used to come out blank because no route
# supplied it.
from engine import get_backend_label

env.globals['app_version'] = APP_VERSION
env.globals['backend_label'] = get_backend_label()

def render(template_name, **context):
    return env.get_template(template_name).render(**context)

# =============================================================================
# FastAPI setup
# =============================================================================

# =============================================================================
# Lifespan: start/stop job runner
# =============================================================================

import asyncio


async def app_lifespan(app):
    """FastAPI lifespan handler — replaces deprecated on_event."""
    await init_db(DB_PATH)
    try:
        scan_voice_clones(DB_PATH, _REPO_ROOT / "voice_clones", VOICE_DIR)
    except Exception:
        logger.warning("voice_clones scan failed", exc_info=True)
    get_runner().start()
    logger.info("ruru started")

    yield  # server is running here

    get_runner().stop()
    logger.info("ruru stopped")


# Single FastAPI app: lifespan + static mounts on the SAME object. (A second
# `FastAPI()` reassignment used to discard the mounts, breaking /static + /audio/*.)
app = FastAPI(lifespan=app_lifespan)
app.mount("/static", StaticFiles(directory=_REPO_ROOT / "static"), name="static")
app.mount("/audio/voices", StaticFiles(directory=str(VOICE_DIR)), name="voices")
app.mount("/audio/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

# =============================================================================
# Middleware
# =============================================================================

@app.middleware("http")
async def handle_htmx(request: Request, call_next):
    response = await call_next(request)
    response.headers["HX-Push-Url"] = "false"
    return response

# =============================================================================
# Helper: generate voice combo options for templates
# =============================================================================

def voice_combos(voice_rows):
    combos = []
    for v in voice_rows:
        combos.append({
            "id": v["id"],
            "name": v["name"],
            "type": v["voice_type"],
            "description": v["voice_description"] or "",
            "ref_audio": v["ref_audio_path"] or "",
            "ref_text": v["ref_text"] or "",
            "audio_format": v["audio_format"] or "wav",
        })
    return combos

async def _unique_slug(name: str, session) -> str:
    """Slugify ``name`` and resolve UNIQUE collisions by appending -2, -3, ..."""
    from models.db import Voice
    base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or "voice"
    candidate = base
    n = 1
    while True:
        existing = await session.execute(
            sqlalchemy.select(Voice.id).where(Voice.slug == candidate)
        )
        if existing.scalars().first() is None:
            return candidate
        n += 1
        candidate = f"{base}-{n}"

async def _clear_slug_tombstone(slug: str, session) -> None:
    """Creating a voice via the UI overrides an earlier delete of that slug."""
    from models.db import DeletedVoiceSlug
    await session.execute(
        sqlalchemy.delete(DeletedVoiceSlug).where(DeletedVoiceSlug.slug == slug)
    )

# =============================================================================
# Routes: Root / Admin
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    async with get_async_session() as session:
        from models.db import Voice, Job
        voices = await session.execute(
            sqlalchemy.select(Voice).order_by(Voice.name)
        )
        voices = voices.scalars().all()

        jobs_query = sqlalchemy.select(Job).order_by(Job.created_at.desc()).limit(20)
        jobs_result = await session.execute(jobs_query)
        jobs = jobs_result.scalars().all()

    return HTMLResponse(render("index.html", voices=list(voices), jobs=list(jobs)))

def _safe_output_file(name: str) -> Path | None:
    """Resolve ``name`` against OUTPUT_DIR, rejecting path traversal.

    Returns the resolved Path only when it is an existing file that lives
    directly inside OUTPUT_DIR; otherwise None so callers can 404/ignore.
    """
    base = OUTPUT_DIR.resolve()
    try:
        target = (OUTPUT_DIR / name).resolve()
    except (ValueError, OSError):
        return None
    if target.parent != base:
        return None
    return target if target.is_file() else None


def _storage_toast(message: str, type: str = "success") -> dict:
    """HX-Trigger header payload — object form toast.js expects ({message, type}).

    toast.js does `const { message, type } = event.detail;`, so the value must be
    an object, not a bare string (the bare-string form rendered literal "undefined").
    """
    import json
    return {"HX-Trigger": json.dumps({"showToast": {"message": message, "type": type}})}


@app.get("/admin/storage", response_class=HTMLResponse)
async def admin_storage():
    async with get_async_session() as session:
        from models.db import Voice, Job
        voices_result = await session.execute(
            sqlalchemy.select(Voice).order_by(Voice.name)
        )
        voices = voices_result.scalars().all()

        job_count = (
            await session.execute(sqlalchemy.select(func.count()).select_from(Job))
        ).scalar_one()
        clearable_job_count = (
            await session.execute(
                sqlalchemy.select(func.count())
                .select_from(Job)
                .where(Job.status.in_(("completed", "failed", "cancelled")))
            )
        ).scalar_one()

    # Scan the output directory for generated audio files so the template's
    # {{ file_count }}, {{ total_size_mb }}, {{ output_dir }} and {% for file %}
    # bindings resolve. Previously only `voices` was passed, so the page showed
    # "No files" even when OUTPUT_DIR held generated audio.
    files = []
    total_bytes = 0
    if OUTPUT_DIR.is_dir():
        for entry in sorted(
            OUTPUT_DIR.iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            if entry.is_file():
                st = entry.stat()
                total_bytes += st.st_size
                files.append(
                    {
                        "name": entry.name,
                        "size_mb": round(st.st_size / 1_000_000, 2),
                        "created": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    }
                )
    file_count = len(files)
    total_size_mb = round(total_bytes / 1_000_000, 2)

    return HTMLResponse(
        render(
            "admin_storage.html",
            voices=list(voices),
            files=files,
            file_count=file_count,
            total_size_mb=total_size_mb,
            output_dir=str(OUTPUT_DIR),
            job_count=job_count,
            clearable_job_count=clearable_job_count,
        )
    )


@app.post("/api/storage/cleanup-old")
async def storage_cleanup_old(days: int = 30):
    """Delete output files older than ``days`` (query param, default 30)."""
    cutoff = datetime.now().timestamp() - days * 86_400
    removed = 0
    if OUTPUT_DIR.is_dir():
        for entry in OUTPUT_DIR.iterdir():
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
    return Response(
        status_code=200, headers=_storage_toast(f"Deleted {removed} old file(s)")
    )


@app.post("/api/storage/delete-selected")
async def storage_delete_selected(request: Request):
    """Delete a JSON-selected list of output filenames: {"filenames": [...]}."""
    body = await request.json()
    filenames = body.get("filenames", []) if isinstance(body, dict) else []
    removed = 0
    for name in filenames:
        target = _safe_output_file(name)
        if target:
            target.unlink(missing_ok=True)
            removed += 1
    return Response(
        status_code=200,
        headers=_storage_toast(f"Deleted {removed} selected file(s)"),
    )


@app.post("/api/storage/delete-all")
async def storage_delete_all():
    """Delete every file in the output directory."""
    removed = 0
    if OUTPUT_DIR.is_dir():
        for entry in OUTPUT_DIR.iterdir():
            if entry.is_file():
                entry.unlink(missing_ok=True)
                removed += 1
    return Response(
        status_code=200, headers=_storage_toast(f"Deleted {removed} file(s)")
    )


@app.post("/api/storage/clear-jobs")
async def storage_clear_jobs():
    """Delete completed/failed/cancelled job rows (and their chapters) from the DB.

    Never touches queued/running jobs. Chapter.job_id has ON DELETE CASCADE, but
    PRAGMA foreign_keys=ON isn't guaranteed on every pooled connection, so chapters
    are deleted explicitly rather than relying on cascade.
    """
    from models.db import Job, Chapter

    async with get_async_session() as session:
        clearable_ids = (
            await session.execute(
                sqlalchemy.select(Job.id).where(
                    Job.status.in_(("completed", "failed", "cancelled"))
                )
            )
        ).scalars().all()
        if clearable_ids:
            await session.execute(
                sqlalchemy.delete(Chapter).where(Chapter.job_id.in_(clearable_ids))
            )
            await session.execute(sqlalchemy.delete(Job).where(Job.id.in_(clearable_ids)))
            await session.commit()
    return Response(
        status_code=200,
        headers=_storage_toast(f"Cleared {len(clearable_ids)} job(s) from history"),
    )


@app.delete("/api/storage/files/{name}")
async def storage_delete_file(name: str):
    """Delete a single output file by name (path-traversal safe)."""
    target = _safe_output_file(name)
    if not target:
        return Response(
            status_code=404, headers=_storage_toast("File not found", "error")
        )
    target.unlink(missing_ok=True)
    return Response(
        status_code=200, headers=_storage_toast(f"Deleted {target.name}")
    )

# =============================================================================
# Routes: Voice Management
# =============================================================================

@app.get("/voices", response_class=HTMLResponse)
async def voices():
    async with get_async_session() as session:
        from models.db import Voice
        voices_result = await session.execute(
            sqlalchemy.select(Voice).order_by(Voice.name)
        )
        voices = voices_result.scalars().all()
    return HTMLResponse(render("voices.html", voices=list(voices)))

@app.get("/voice/add", response_class=HTMLResponse)
async def voice_add():
    async with get_async_session() as session:
        from models.db import Voice
        voices_result = await session.execute(
            sqlalchemy.select(Voice).order_by(Voice.name)
        )
        voices = voices_result.scalars().all()
    return HTMLResponse(render("voice_add.html", voices=list(voices)))

@app.post("/api/voices/design")
async def voice_design(request: Request):
    form = await request.form()
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()

    if not name or not description:
        return HTMLResponse("<p class='text-red-500'>Name and description required.</p>", status_code=400)

    async with get_async_session() as session:
        from models.db import Voice
        slug = await _unique_slug(name, session)
        await _clear_slug_tombstone(slug, session)
        new_voice = Voice(
            name=name,
            voice_type="designed",
            description=description,
            slug=slug,
        )
        session.add(new_voice)
        await session.commit()

    return HTMLResponse("", headers={"HX-Redirect": "/voices"})

@app.post("/api/voices/clone")
async def voice_clone(request: Request):
    form = await request.form()
    name = form.get("name", "").strip()
    ref_audio: UploadFile = form.get("ref_audio")
    ref_text = form.get("ref_text", "").strip()
    description = form.get("description", "").strip()

    if not name or not ref_audio or not getattr(ref_audio, "filename", None) or not ref_text:
        return HTMLResponse("<p class='text-red-500'>Name, reference audio file, and reference text required.</p>", status_code=400)

    async with get_async_session() as session:
        from models.db import Voice
        slug = await _unique_slug(name, session)
        await _clear_slug_tombstone(slug, session)
        suffix = Path(ref_audio.filename).suffix or ".wav"
        saved_path = VOICE_DIR / f"{slug}_ref{suffix}"
        saved_path.write_bytes(await ref_audio.read())
        new_voice = Voice(
            name=name,
            voice_type="cloned",
            ref_audio_path=str(saved_path),
            sample_audio_path=str(saved_path),
            ref_text=ref_text,
            description=description,
            slug=slug,
        )
        session.add(new_voice)
        await session.commit()

    return HTMLResponse("", headers={"HX-Redirect": "/voices"})

@app.post("/api/voices/refresh")
async def voices_refresh():
    """Re-scan voice_clones/ for new voices; returns a toast."""
    try:
        n = scan_voice_clones(DB_PATH, _REPO_ROOT / "voice_clones", VOICE_DIR)
    except Exception:
        logger.warning("voice_clones refresh failed", exc_info=True)
        return Response(status_code=200, headers=_storage_toast("Refresh failed", "error"))
    return Response(status_code=200, headers=_storage_toast(f"Scanned; {n} new voice(s)"))

@app.get("/voice/{voice_id}/edit", response_class=HTMLResponse)
async def voice_edit(voice_id: int):
    async with get_async_session() as session:
        from models.db import Voice
        voice = await session.get(Voice, voice_id)
        voices_result = await session.execute(
            sqlalchemy.select(Voice).order_by(Voice.name)
        )
        voices = voices_result.scalars().all()
    if not voice:
        return HTMLResponse("Voice not found", status_code=404)
    return HTMLResponse(render("voice_edit.html", voice=voice, voices=list(voices)))

@app.post("/api/voices/{voice_id}/update")
async def voice_update(voice_id: int, request: Request):
    form = await request.form()
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()
    speaking_style = form.get("speaking_style", "").strip()

    async with get_async_session() as session:
        from models.db import Voice
        voice = await session.get(Voice, voice_id)
        if not voice:
            return HTMLResponse("Voice not found", status_code=404)
        voice.name = name
        voice.description = description
        voice.speaking_style = speaking_style
        await session.commit()

    return HTMLResponse("", headers={"HX-Redirect": "/voices"})

@app.delete("/api/voices/{voice_id}")
async def voice_delete(voice_id: int):
    async with get_async_session() as session:
        from models.db import Voice, Job, DeletedVoiceSlug
        voice = await session.get(Voice, voice_id)
        if not voice:
            return HTMLResponse("Voice not found", status_code=404)
        # Job.voice_id is a FK with no ON DELETE behavior, so any job that ever
        # used this voice would otherwise block deletion with an IntegrityError.
        # Null it out to preserve job history instead of losing it.
        await session.execute(
            sqlalchemy.update(Job).where(Job.voice_id == voice_id).values(voice_id=None)
        )
        # Tombstone the slug: the startup scan re-imports anything
        # from voice_clones/ whose row is missing — without this the voice
        # resurrects on restart.
        session.add(DeletedVoiceSlug(slug=voice.slug))
        await session.delete(voice)
        await session.commit()
    return HTMLResponse("", headers={"HX-Redirect": "/voices"})

# Short, iconic lines used as sample text when previewing a designed voice —
# quick to synthesize and instantly recognizable.
HITCHHIKERS_QUOTES = [
    "Don't panic.",
    "Time is an illusion. Lunchtime doubly so.",
    "So long, and thanks for all the fish.",
    "Space is big. You just won't believe how vastly, hugely, mind-bogglingly big it is.",
    "The ships hung in the sky in much the same way that bricks don't.",
    "It is a mistake to think you can solve any major problems just with potatoes.",
    "The trouble with most forms of transport is that, basically, they don't actually work.",
    "A common mistake people make when trying to design something completely foolproof is to underestimate the ingenuity of complete fools.",
    "Human beings, who are almost unique in having the ability to learn from the experience of others, are also remarkable for their apparent disinclination to do so.",
    "Forty-two.",
]


@app.post("/api/voices/{voice_id}/preview")
async def voice_preview(voice_id: int):
    """Generate a short sample clip for a designed voice using a random HHGTTG quote."""
    import random

    async with get_async_session() as session:
        from models.db import Voice, Job

        voice = await session.get(Voice, voice_id)
        if not voice:
            return HTMLResponse("<p class='text-red-500 text-sm'>Voice not found.</p>", status_code=404)
        if voice.voice_type != "designed":
            return HTMLResponse(
                "<p class='text-red-500 text-sm'>Preview is only available for designed voices.</p>",
                status_code=400,
            )

        text = random.choice(HITCHHIKERS_QUOTES)
        new_job = Job(
            title=text[:100],
            voice_id=voice_id,
            status="queued",
            source_path=text,
            scope="quick",
        )
        session.add(new_job)
        await session.commit()
        job_id = new_job.id

    payload = {
        "text": text,
        "voice_id": voice_id,
        "voice_type": voice.voice_type,
        "voice_description": voice.description or "",
        "ref_audio_path": voice.ref_audio_path or "",
        "ref_text": voice.ref_text or "",
        "audio_format": "wav",
    }

    runner = get_runner()
    future = await runner.enqueue(job_id, JobType.GENERATE_TTS, payload)
    result = await future

    if result.get("status") != "completed":
        return HTMLResponse(
            "<p class='text-red-500 text-sm'>Preview generation failed.</p>", status_code=500
        )

    # Copy the generated clip into VOICE_DIR so it's served the same way as a
    # cloned voice's reference sample (/audio/voices/...), not /audio/output/...
    src = Path(result["output_path"])
    dest = VOICE_DIR / f"{voice.slug}_preview{src.suffix}"
    shutil.copy(src, dest)

    async with get_async_session() as session:
        from models.db import Voice

        voice = await session.get(Voice, voice_id)
        voice.sample_audio_path = str(dest)
        await session.commit()

    return HTMLResponse(
        f'<audio controls preload="none" class="mt-2 w-full">'
        f'<source src="/audio/voices/{dest.name}" type="audio/wav">'
        f"</audio>"
    )

# =============================================================================
# Routes: TTS
# =============================================================================

@app.get("/tts", response_class=HTMLResponse)
async def tts_page():
    async with get_async_session() as session:
        from models.db import Voice, Job
        voices_result = await session.execute(
            sqlalchemy.select(Voice).order_by(Voice.name)
        )
        voices = voices_result.scalars().all()

        active_jobs_count = await session.scalar(
            sqlalchemy.select(func.count()).select_from(Job).where(Job.status.in_(('queued', 'running')))
        )
    return HTMLResponse(render("tts.html", voices=list(voices), active_jobs_count=active_jobs_count))

@app.post("/api/tts", response_class=HTMLResponse)
async def api_tts(request: Request):
    form = await request.form()
    text = form.get("text", "").strip()
    voice_id = int(form.get("voice_id", 0))
    audio_format = form.get("audio_format", "wav")

    if not text:
        return HTMLResponse("<p class='text-red-500'>Text required.</p>", status_code=400)

    async with get_async_session() as session:
        from models.db import Voice, Job
        voice = await session.get(Voice, voice_id)
        if not voice:
            return HTMLResponse("<p class='text-red-500'>Voice not found.</p>", status_code=404)

        new_job = Job(
            title=text[:100],
            voice_id=voice_id,
            status="queued",
            source_path=text,
            scope="quick",
        )
        session.add(new_job)
        await session.commit()
        job_id = new_job.id

    payload = {
        "text": text,
        "voice_id": voice_id,
        "voice_type": voice.voice_type,
        "voice_description": voice.description or "",
        "ref_audio_path": voice.ref_audio_path or "",
        "ref_text": voice.ref_text or "",
        "audio_format": audio_format,
    }

    runner = get_runner()
    future = await runner.enqueue(job_id, JobType.GENERATE_TTS, payload)
    result = await future

    if result.get("status") == "completed":
        audio_path = result["output_path"]
        audio_name = Path(audio_path).name
        return HTMLResponse(
            f'<div hx-swap="innerHTML"><audio controls src="/audio/output/{audio_name}" '
            f'class="w-full mt-2"></audio></div>',
            headers={"HX-Trigger": "refreshHistory"},
        )
    else:
        return HTMLResponse(f"<p class='text-red-500'>Job failed: {result.get('message', 'unknown')}</p>", status_code=500)


@app.get("/api/tts/clips", response_class=HTMLResponse)
async def api_tts_clips():
    """Recent Quick TTS clips for the /tts page history panel."""
    async with get_async_session() as session:
        from models.db import Job, Voice

        rows = (
            await session.execute(
                sqlalchemy.select(Job, Voice.name)
                .join(Voice, Job.voice_id == Voice.id, isouter=True)
                .where(Job.scope == "quick", Job.status == "completed")
                .order_by(Job.created_at.desc())
                .limit(10)
            )
        ).all()

    clips = [
        {
            "text": job.title,
            "voice_name": voice_name or "Unknown",
            "audio_name": Path(job.output_path).name,
            "created_at": job.created_at,
        }
        for job, voice_name in rows
        if job.output_path
    ]
    return HTMLResponse(render("partials/tts_clips.html", clips=clips))

# =============================================================================
# Routes: Music (YuE2-3B) — v0.7.0

@app.get("/music", response_class=HTMLResponse)
async def music_page():
    async with get_async_session() as session:
        from models.db import Job
        active_jobs_count = await session.scalar(
            sqlalchemy.select(func.count()).select_from(Job).where(Job.status.in_(('queued', 'running')))
        )
    return HTMLResponse(render("music.html", active_jobs_count=active_jobs_count))

@app.post("/api/music", response_class=HTMLResponse)
async def api_music(request: Request):
    form = await request.form()
    style = form.get("style", "").strip()
    lyrics = form.get("lyrics", "").strip()
    instrumental = form.get("instrumental") == "on"
    seed_raw = (form.get("seed") or "").strip()
    audio_format = form.get("audio_format", "wav")

    if not style or not lyrics:
        return HTMLResponse("<p class='text-red-500'>Style and lyrics required.</p>", status_code=400)
    seed = int(seed_raw) if seed_raw else None

    async with get_async_session() as session:
        from models.db import Job
        new_job = Job(
            title=f"Music: {style[:80]}",
            voice_id=None,
            status="queued",
            source_path=f"{style}\n---\n{lyrics}",
            scope="music",
            audio_format=audio_format,
        )
        session.add(new_job)
        await session.commit()
        job_id = new_job.id

    payload = {
        "style": style,
        "lyrics": lyrics,
        "instrumental": instrumental,
        "seed": seed,
        "audio_format": audio_format,
    }
    runner = get_runner()
    future = await runner.enqueue(job_id, JobType.GENERATE_MUSIC, payload)
    result = await future

    if result.get("status") == "completed":
        audio_name = Path(result["output_path"]).name
        return HTMLResponse(
            f'<div hx-swap="innerHTML"><audio controls src="/audio/output/{audio_name}" '
            f'class="w-full mt-2"></audio></div>',
            headers={"HX-Trigger": "refreshHistory"},
        )
    return HTMLResponse(
        f"<p class='text-red-500'>Job failed: {result.get('message', 'unknown')}</p>",
        status_code=500,
    )

# =============================================================================
# Routes: Narration
# =============================================================================

@app.get("/narrate", response_class=HTMLResponse)
async def narrate_page():
    async with get_async_session() as session:
        from models.db import Voice
        voices_result = await session.execute(
            sqlalchemy.select(Voice).order_by(Voice.name)
        )
        voices = voices_result.scalars().all()
    return HTMLResponse(render("narrate.html", voices=list(voices)))

@app.post("/api/narrate", response_class=HTMLResponse)
async def api_narrate(request: Request):
    form = await request.form()
    job_title = form.get("job_title", "").strip() or "Untitled"
    voice_id = int(form.get("voice_id", 0))
    author = form.get("author", "").strip()
    cover_path = form.get("cover_path", "").strip()
    # parse_mode=verbatim disables title/heading extraction in parse_document()
    # (see engine/parsers.py) — every segment is narrated in full, untouched.
    # Anything else (including unset) keeps the current heading-detection
    # default so existing document-narration workflows don't change.
    parse_mode = form.get("parse_mode", "").strip().lower()
    verbatim = parse_mode == "verbatim"

    # Validate the voice first so a bad voice_id yields 404 before we consume the
    # upload. Snapshot the voice config while the session is still open.
    async with get_async_session() as session:
        from models.db import Voice
        voice = await session.get(Voice, voice_id)
        if not voice:
            return HTMLResponse("<p class='text-red-500'>Voice not found.</p>", status_code=404)
        voice_type = voice.voice_type
        voice_description = voice.description or ""
        ref_audio_path = voice.ref_audio_path or ""
        ref_text = voice.ref_text or ""

    # Save the uploaded document and parse it into text chunks. parse_document()
    # expects a saved file PATH — passing the Starlette FormData object (the old
    # behaviour) raised AttributeError -> 500.
    upload = form.get("document")
    if upload is None or not getattr(upload, "filename", None):
        return HTMLResponse("<p class='text-red-500'>No document uploaded.</p>", status_code=400)

    upload_path = UPLOAD_DIR / upload.filename
    upload_path.write_bytes(await upload.read())

    from engine.parsers import parse_document
    from engine.chunker import split_into_chunks
    from config import CHUNK_SIZE_WORDS, CHUNK_MAX_CHARS

    # Each chapter must be re-split into word-limited chunks: Qwen3-TTS silently
    # truncates long inputs, so feeding a whole chapter as one chunk produces a
    # partial MP3. chapter_boundaries records where each chapter starts.
    chunks: list[str] = []
    chapter_boundaries: list[int] = []
    for chapter in parse_document(upload_path, verbatim=verbatim):
        chapter_boundaries.append(len(chunks))
        chunks.extend(split_into_chunks(chapter.text, CHUNK_SIZE_WORDS, CHUNK_MAX_CHARS))

    if not chunks:
        return HTMLResponse("<p class='text-red-500'>No readable text found in document.</p>", status_code=400)

    # Create the narration job
    async with get_async_session() as session:
        from models.db import Job
        new_job = Job(
            title=job_title,
            voice_id=voice_id,
            status="queued",
            source_path=str(upload_path),
            author=author,
            cover_path=cover_path,
        )
        session.add(new_job)
        await session.commit()
        job_id = new_job.id

    payload = {
        "chunks": chunks,
        "voice_type": voice_type,
        "voice_description": voice_description,
        "ref_audio_path": ref_audio_path,
        "ref_text": ref_text,
        "audio_format": "mp3",
        "job_title": job_title,
        "chapter_boundaries": chapter_boundaries,
        "author": author,
        "cover_path": cover_path,
        "parse_mode": parse_mode,
    }

    runner = get_runner()
    await runner.enqueue(job_id, JobType.NARRATE_DOCUMENT, payload)
    # Do NOT await the future inline — long narrations block the HTTP request and
    # time out under nginx. The job runs in the background; /jobs/{id} polls.
    return HTMLResponse("", headers={"HX-Redirect": f"/jobs/{job_id}"})

# =============================================================================
# Routes: Jobs
# =============================================================================

@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(job_id: int):
    async with get_async_session() as session:
        from models.db import Job
        job = await session.get(Job, job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    return HTMLResponse(render("job_detail.html", job=job))

@app.get("/api/jobs/{job_id}/progress")
async def job_progress(job_id: int):
    """HTML fragment for job_detail.html's polling div. Emits HX-Trigger:
    job:complete when terminal so the page stops polling and reloads."""
    async with get_async_session() as session:
        from models.db import Job
        job = await session.get(Job, job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    html = render("_job_progress.html", job=job)
    headers = {}
    if job.status in ("completed", "failed", "cancelled"):
        headers["HX-Trigger"] = "job:complete"
    return HTMLResponse(html, headers=headers)

@app.get("/api/jobs/{job_id}/download")
async def job_download(job_id: int):
    """Download the completed audiobook (redirects to the static audio mount)."""
    async with get_async_session() as session:
        from models.db import Job
        job = await session.get(Job, job_id)
    if not job or not job.output_path:
        return HTMLResponse("Job or output not found", status_code=404)
    return RedirectResponse(f"/audio/output/{Path(job.output_path).name}", status_code=302)

@app.delete("/api/jobs/{job_id}")
async def job_delete(job_id: int):
    """Delete a job and its output file. Never touches queued/running jobs."""
    async with get_async_session() as session:
        from models.db import Job, Chapter
        job = await session.get(Job, job_id)
        if not job:
            return HTMLResponse("Job not found", status_code=404)
        if job.status in ("queued", "running"):
            return HTMLResponse(
                "<p class='text-red-500 text-sm'>Cannot delete a queued or running job.</p>",
                status_code=400,
            )
        # --- on-disk artifacts (run BEFORE the row goes, while we still
        # --- have job.output_path / job.source_path in hand) ---
        if job.output_path:
            Path(job.output_path).unlink(missing_ok=True)
        # Per-chunk WAVs: narrate jobs write chunks/job_<id>/chunk_*.wav.
        # Never raises — a missing dir is the common case (short TTS jobs
        # don't chunk).
        chunk_dir = CHUNK_DIR / f"job_{job_id}"
        if chunk_dir.is_dir():
            shutil.rmtree(chunk_dir, ignore_errors=True)
        # Uploaded source document. CAREFUL: for /api/tts/generate jobs,
        # source_path is the raw TEXT, not a filesystem path — resolve()
        # against the upload dir and is_file() both guard that (raw text
        # won't resolve under UPLOAD_DIR).
        src = Path(job.source_path)
        if src.is_absolute():
            upload_file = src.resolve() if src.is_file() else None
        else:
            upload_file = None
        if upload_file is not None and upload_file.is_relative_to(UPLOAD_DIR.resolve()):
            upload_file.unlink(missing_ok=True)

        await session.execute(sqlalchemy.delete(Chapter).where(Chapter.job_id == job_id))
        await session.delete(job)
        await session.commit()
    return HTMLResponse("", headers={"HX-Redirect": "/"})


# =============================================================================
# Routes: Programmatic JSON API (for agents / curl)
# =============================================================================

@app.get("/api/voices")
async def api_voices_json():
    """JSON voice list — agents resolve voices by slug here."""
    async with get_async_session() as session:
        from models.db import Voice
        voices = await session.execute(sqlalchemy.select(Voice).order_by(Voice.name))
        return JSONResponse([
            {"id": v.id, "name": v.name, "slug": v.slug, "voice_type": v.voice_type}
            for v in voices.scalars().all()
        ])

@app.post("/api/tts/generate")
async def api_tts_generate(request: Request):
    """JSON TTS — short clips only. Enqueue + await inline (single clip is cheap)."""
    body = await request.json()
    text = (body.get("text") or "").strip()
    voice_id = int(body.get("voice_id", 0))
    audio_format = body.get("audio_format") or "mp3"
    if not text:
        return JSONResponse({"status": "failed", "message": "text required"}, status_code=400)
    async with get_async_session() as session:
        from models.db import Voice, Job
        voice = await session.get(Voice, voice_id)
        if not voice:
            return JSONResponse({"status": "failed", "message": "Voice not found."}, status_code=404)
        new_job = Job(title=text[:100], voice_id=voice_id, status="queued", source_path=text)
        session.add(new_job)
        await session.commit()
        job_id = new_job.id
        payload = {
            "text": text,
            "voice_id": voice_id,
            "voice_type": voice.voice_type,
            "voice_description": voice.description or "",
            "ref_audio_path": voice.ref_audio_path or "",
            "ref_text": voice.ref_text or "",
            "audio_format": audio_format,
        }
    runner = get_runner()
    future = await runner.enqueue(job_id, JobType.GENERATE_TTS, payload)
    result = await future
    if result.get("status") == "completed":
        return JSONResponse({"job_id": job_id, "status": "completed",
                             "audio_url": f"/audio/output/{Path(result['output_path']).name}"})
    return JSONResponse({"job_id": job_id, "status": "failed",
                         "message": result.get("message", "unknown")}, status_code=500)

@app.get("/api/jobs/{job_id}")
async def api_job_json(job_id: int):
    """JSON job status — distinct from the HTML .../progress fragment."""
    async with get_async_session() as session:
        from models.db import Job
        job = await session.get(Job, job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    audio_url = (f"/audio/output/{Path(job.output_path).name}"
                 if job.status == "completed" and job.output_path else None)
    return JSONResponse({"id": job.id, "status": job.status, "progress": job.progress,
                         "audio_url": audio_url})

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "version": APP_VERSION})

# =============================================================================
# Error handlers
# =============================================================================

@app.exception_handler(404)
async def not_found(request: Request, exc):
    return HTMLResponse(render("error.html", code=404, message="Page not found"), status_code=404)

@app.exception_handler(500)
async def server_error(request: Request, exc):
    return HTMLResponse(render("error.html", code=500, message="Server error"), status_code=500)

# =============================================================================
# Entry point
# =============================================================================

def main():
    uvicorn.run("app:app", host=HOST, port=PORT, reload=False)

if __name__ == "__main__":
    main()
