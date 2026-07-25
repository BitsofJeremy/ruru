# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ruru is a single-user web application for audiobook narration and voice management, powered by Qwen3-TTS running locally on Apple Silicon via MLX (`mlx-audio`). Single FastAPI process with in-process asyncio job runner handles all TTS work — no Celery, no Redis, no separate worker process. (This MLX/Apple Silicon implementation is distinct from the earlier NVIDIA/CUDA-based implementation in `reference_material/`.)

## Running the App

```bash
# Install dependencies
uv sync

# Run (single process, no worker needed)
uv run ruru
# Or directly
uv run app.py

# Run tests
uv run pytest
uv run pytest tests/test_chunker.py           # a single file
uv run pytest tests/test_chunker.py::test_name # a single test

# Production (macOS): launchd user agent behind nginx — see install_macos.sh
sudo bash install_macos.sh
./reset_service.sh restart-only               # restart the launchd agent
```

Useful env overrides:
```bash
RURU_WORK_DIR=/tmp/ruru_test uv run ruru  # custom data dir
RURU_PORT=5252 uv run ruru                # custom port (default 5151)
```

## Tech Stack

- **Backend:** FastAPI (single process) + in-process asyncio job runner
- **Frontend:** Jinja2 + HTMX 2.0.4 + Tailwind CSS (CDN) — no JavaScript framework
- **Database:** SQLite via aiosqlite (WAL mode). `sqlalchemy[asyncio]` is a declared dependency; ORM integration notes live in `sqlalchemy_orm_integration/` but `models/db.py` still uses raw SQL.
- **TTS:** Qwen3-TTS via `mlx-audio`, 8-bit MLX-community weights — `Qwen3-TTS-12Hz-1.7B-Base-8bit` (voice cloning) and `Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit` (text-described voices). Runs on Apple Silicon GPU, not CUDA.
- **Audio:** pydub + soundfile + mutagen + ffmpeg
- **Package management:** uv + pyproject.toml

## Architecture

```
Browser (HTMX) ──> FastAPI (async) ──> SQLite
                        │
                   asyncio.to_thread() ──> JobRunner (same process, MLX/Apple GPU)
```

**No Redis. No Celery. No separate worker process.**

Jobs persist in SQLite with status (queued/running/completed/failed). A background asyncio task dequeues and processes them sequentially via `asyncio.to_thread()`. Progress updates write back to SQLite so HTMX polling works exactly as before.

## Key Architectural Details

**pyproject.toml.** All dependencies declared in `pyproject.toml`. The package is the repo root itself (`[tool.hatch.build.targets.wheel] packages = ["."]`). Console script `ruru` maps to `app:main` (top-level `app.py`, not a `ruru` package). Use `uv sync` to install.

**StaticFiles directory validation.** `StaticFiles(directory=...)` validates the directory exists at mount time. Directories are created at module level BEFORE `app = FastAPI(...)`. This was a real bug — do not move directory creation into the lifespan handler only.

**uvicorn.run import string.** `main()` calls `uvicorn.run("app:app", ...)` — the double `app:app` is intentional because `app.py` contains `app = FastAPI()` at module level. Changing this breaks the service.

**Two database access patterns.** FastAPI routes use async `aiosqlite`. The sync job handlers (in `jobs/tasks.py`) use synchronous `sqlite3` directly via `asyncio.to_thread()`. Both patterns coexist and must not be mixed.

**JobRunner.** Background thread with its own event loop. Dispatches jobs from an `asyncio.Queue` to sync functions via `asyncio.to_thread()`. Global singleton via `get_runner()`.

## File Layout

```
app.py                  # FastAPI entry point + all API routes.
config.py               # All settings via RURU_ env vars.
jobs/
  __init__.py
  models.py              # JobStatus enum, TTSJob/NarrateJob dataclasses.
  queue.py               # JobRunner class (asyncio queue + background thread).
  tasks.py               # Sync job handlers: run_generate_tts, run_narrate_document,
                        #   run_regenerate_chapter.
models/
  db.py                 # SQLite schema (voices, jobs, chapters), get_db(), init_db().
  seed_voices.py        # Seeds 10 Ephergent characters from characters_data.py at repo root.
engine/
  tts.py                # TTSEngine class: load()/unload(), generate_clone(), generate_design().
  parsers.py            # parse_document() dispatcher. EPUB (ebooklib+BS4, zipfile fallback) and TXT.
  chunker.py            # split_into_chunks() — sentence-boundary-aware, default 200 words.
  assembler.py          # assemble_chunks() — trim silence, normalize, concatenate, export.
templates/              # Jinja2 templates.
  base.html             # Layout with sidebar nav.
  index.html            # Dashboard — stats and job list.
  tts.html              # Quick TTS form.
  voices.html           # Voice library grid.
  voice_add.html        # Voice creation (design + clone).
  voice_edit.html       # Voice edit form.
  narrate.html          # Document upload and narration.
  job_detail.html       # Job progress with live polling.
  admin_storage.html    # Storage administration.
  error.html            # Styled 404/500 error page.
static/                 # Vendored HTMX, custom CSS, toast/redirect JS.
archive/                # Old worker.py, setup.sh, update.sh, uninstall.sh, install_linux.sh (systemd)
docs/
  uninstall.md          # Step-by-step teardown instructions.
  superpowers/
    specs/2026-05-05-weirding-simplification-design.md  # Architecture change doc
```

## Configuration

All env vars use the `RURU_` prefix. Defined in `config.py`. Data lives under `WORK_DIR/cache/` (uploads, chunks, output, voices); the SQLite DB is `WORK_DIR/ruru.db`.

| Variable | Default | Notes |
|---|---|---|
| `RURU_WORK_DIR` | `<repo>/data/` | Runtime data root (cache/, DB) |
| `RURU_PORT` | `5151` | Web server port |
| `RURU_HOST` | `0.0.0.0` | Web server bind address |
| `RURU_MLX_MODELS_PATH` | `~/.cache/huggingface/hub` | Where MLX/HF weights are cached |
| `RURU_MODEL_SIZE` | `auto` | auto/small/large |
| `RURU_CHUNK_SIZE_WORDS` | `200` | Lower = less memory per chunk |
| `RURU_MAX_RETRIES` | `3` | Chunk generation retry count |
| `RURU_CREATOR_TAG` | `Created by ruru` | Audio metadata tag |
| `RURU_EPHERGENT_REPO` | parent of repo root | Directory containing `characters_data.py` |
| `RURU_AUDIO_FORMAT` | `mp3` | Output format |
| `RURU_AUDIO_BITRATE` | `128k` | Output bitrate |

Note: there is no Redis/Celery anywhere — the job runner is fully in-process (`jobs/queue.py`).

## Installation (Production — macOS)

```bash
# Install dependencies + launchd user agent + nginx reverse proxy (idempotent)
sudo bash install_macos.sh
```

`install_macos.sh` is sudo-safe: it derives paths from the script location (never `$HOME`), re-runs user-level steps (`uv sync`, DB init, the launchd *user* agent) as `SUDO_USER`, and only uses root for nginx on port 80. The launchd agent lands in `~/Library/LaunchAgents/com.jeremy.ruru.web.plist`.

Use `reset_service.sh` to manage the running service: `restart-only`, `clean-only`, `reset-db`, `clean-all` (see `reset_service.sh help`). Logs go to `data/logs/ruru.{log,err}`.

The legacy Linux systemd installer has been moved to `archive/install_linux.sh`; the project now targets macOS/Apple Silicon and `install_macos.sh` is the canonical installer.

## Uninstall

See `docs/uninstall.md` for step-by-step teardown instructions.

## Common Pitfalls

- **pydub.silence**: `detect_leading_silence` is a standalone function from `pydub.silence`, NOT a method on `AudioSegment`. Always import as `from pydub.silence import detect_leading_silence`.
- **Voice slug collisions**: Fixed — `_unique_slug()` appends a counter suffix on UNIQUE constraint collision. No longer raises on duplicate names.
- **Quick TTS response**: Fixed — `/api/tts` now returns an `HTMLResponse` with an `<audio>` tag fragment for HTMX swap.
- **Job progress polling**: Job detail uses HTMX polling (`hx-get` every 3s) to `/api/jobs/{id}/progress`.
- **Port conflict**: If the server was previously started manually (`uv run ruru`), the process keeps port 5151 even after restarting the launchd agent. Kill manually-started processes before `reset_service.sh restart-only`.

## UI Conventions

- Dark mode with custom Tailwind colors: `kj-bg`, `kj-card`, `kj-border`, `kj-accent` (purple), `kj-accent2` (teal), `kj-text`, `kj-muted`
- HTMX for all interactivity — no custom JavaScript
- Status badges: running = teal pulse-dot, completed = green, failed = red, queued = yellow
- Voice type badges: ephergent = purple, cloned = blue, designed = green

## Character Data

The 10 Ephergent characters are defined in `characters_data.py` at the repo root (single source of truth). `seed_voices.py` imports from it and populates SQLite on startup. Reference audio lives in `ephergent_voices/*.wav` in the repo root.

## Testing

Pytest suite lives in `tests/` (unit tests for chunker, assembler, parsers, config, db, job models/queue/tasks, and route tests for tts/voices/narrate/jobs). Run with `uv run pytest`. `pytest-asyncio` and `pytest-mock` are dev dependencies; MLX model loading is mocked (`tests/conftest.py` provides `MOCK_WAV` and fixtures) so tests do not touch the GPU.

`test_epub_parser.py` at the repo root is a separate standalone manual script (PEP 723) for verifying EPUB parsing and optionally running real TTS narration against specific chapters. Use it directly with `uv run`.

## Known Issues

See `TODO.md` for the full list. Open items: no CSRF protection (acceptable for single-user local app); the voice edit page's image upload/remove controls call `/api/voices/{id}/image`, which doesn't exist yet (404) — see `PROGRESS.md` "Session: 2026-07-10" for details.

## Project Directories

- `.claude/` — Claude Code session configuration and memory
- `.worktrees/` — Isolated git worktrees created by the `superpowers:using-git-worktrees` skill
- `clone_voices/` — User-created voice clone reference audio (gitignored)
- `ephergent_voices/` — Pre-seeded character reference WAV files (version-controlled)