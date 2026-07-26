# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ruru is a single-user web application for audiobook narration and voice management, powered by Qwen3-TTS running locally on Apple Silicon via MLX (`mlx-audio`). Single FastAPI process with in-process asyncio job runner handles all TTS work — no Celery, no Redis, no separate worker process. (This MLX/Apple Silicon implementation is distinct from the earlier NVIDIA/CUDA-based implementation in `reference_material/`.)

## Development vs Production — four separate checkouts

**You are almost certainly in a dev checkout. Production runs somewhere else.**
Each machine keeps development and production in *different directories*, with
their own `.venv` and their own `RURU_WORK_DIR`. Nothing you do in a dev
checkout reaches a running service until you deliberately update the
production clone.

| | Development | Production |
|---|---|---|
| **macOS (MLX)** | `~/Documents/current_projects/ai_speech/ruru` — Syncthing-synced | `~/bin/ruru` — launchd agent `com.jeremy.ruru.web`, port 5151 |
| **Linux (CUDA)** | a plain `git clone` — Syncthing-synced | `/8TB/bin/ruru` — `ruru-web.service` + nginx at `ruru.nexus.home.test` |

Why it's arranged this way:

- **The dev checkouts are Syncthing-synced between the Mac and the Linux box.**
  A macOS `.venv` landing in a live service directory leaves a dangling
  interpreter symlink — that is exactly how `sprecher-web.service` ended up
  crash-looping with `203/EXEC`. Production clones are deliberately outside the
  synced tree so this cannot happen.
- **The production clones lag on purpose.** They only move when someone pulls
  and verifies. A production clone sitting several commits behind `main` is a
  normal state, not a mistake to "fix" on sight.

### The working rule

Verify in dev, then update production, and restart the service **last**:

1. Make and verify the change in the dev checkout — `uv run pytest`, plus a
   **real** model load and narrate job. The suite mocks the engine, so a green
   run alone proves nothing about TTS actually working.
2. Commit and push from dev.
3. In the production clone: `git pull`, then `uv sync --extra mlx` (macOS) or
   `uv sync --extra cuda` (Linux), then re-run the suite and one real
   generation **before** touching the service.
4. Only then `./reset_service.sh restart-only`. Confirm the old PID is gone, a
   new process holds 5151, and the site answers.

Both platforms must stay green — a Mac-only or Linux-only change is a
regression for the other. See "Testing" for the two files that enforce this.

### Don't collide with the running service

A dev checkout defaults to the same port and a sibling data dir as production.
When running the app from dev on a machine where production is live, override
both:

```bash
RURU_PORT=5252 RURU_WORK_DIR=/tmp/ruru_test uv run ruru
```

Never run `install_macos.sh` / `install_linux.sh` from a dev checkout — they
install a service pointing at whatever directory they live in.

## Running the App

```bash
# Install dependencies (pick the extra matching your hardware)
uv sync --extra mlx     # Apple Silicon
uv sync --extra cuda    # NVIDIA/Linux

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
# Production (Linux/CUDA): systemd unit behind nginx — see install_linux.sh
sudo bash install_linux.sh
./reset_service.sh restart-only               # restart the service (either platform)
```

Useful env overrides:
```bash
RURU_WORK_DIR=/tmp/ruru_test uv run ruru  # custom data dir
RURU_PORT=5252 uv run ruru                # custom port (default 5151)
RURU_TTS_BACKEND=cuda uv run ruru         # NVIDIA backend (default: mlx)
```

## Tech Stack

- **Backend:** FastAPI (single process) + in-process asyncio job runner
- **Frontend:** Jinja2 + HTMX 2.0.4 + Tailwind CSS (CDN) — no JavaScript framework
- **Database:** SQLite (WAL mode). `models/db.py` uses SQLAlchemy ORM — `set_db_path()`, async `init_db()`, and the sync `get_sync_session()` that job handlers use.
- **TTS:** Qwen3-TTS, two interchangeable backends selected by `RURU_TTS_BACKEND`:
  - `mlx` (default, Apple Silicon) — `mlx-audio` with 8-bit MLX-community weights, `Qwen3-TTS-12Hz-1.7B-Base-8bit` (cloning) and `-VoiceDesign-8bit` (text-described).
  - `cuda` (NVIDIA/Linux) — `qwen-tts` + torch with bf16 weights, `Qwen/Qwen3-TTS-12Hz-1.7B-Base` and `-VoiceDesign`. Installed via the `cuda` extra.
- **Audio:** pydub + soundfile + mutagen + ffmpeg
- **Package management:** uv + pyproject.toml

## Architecture

```
Browser (HTMX) ──> FastAPI (async) ──> SQLite
                        │
                   asyncio.to_thread() ──> JobRunner (same process, MLX or CUDA GPU)
```

**No Redis. No Celery. No separate worker process.**

Jobs persist in SQLite with status (queued/running/completed/failed). A background asyncio task dequeues and processes them sequentially via `asyncio.to_thread()`. Progress updates write back to SQLite so HTMX polling works exactly as before.

## Key Architectural Details

**pyproject.toml.** All dependencies declared in `pyproject.toml`. The package is the repo root itself (`[tool.hatch.build.targets.wheel] packages = ["."]`). Console script `ruru` maps to `app:main` (top-level `app.py`, not a `ruru` package). Use `uv sync --extra mlx` (Apple Silicon) or `uv sync --extra cuda` (NVIDIA) to install — plain `uv sync` installs neither backend. The `mlx` and `cuda` extras are declared mutually exclusive via `[tool.uv] conflicts` so `uv lock` forks the resolution per backend instead of intersecting it: `qwen-tts==0.1.1` (pulled in by `cuda`) hard-pins `transformers==4.57.3`, and without the conflict declaration a single universal lock would force that pin onto the Mac too, silently backtracking `mlx-audio` to 0.2.10 — the last release before `mlx_audio/tts/models/qwen3_tts/` existed, which breaks Qwen3-TTS loading with no error at install time. Do not "simplify" the extras back into unconditional dependencies.

**StaticFiles directory validation.** `StaticFiles(directory=...)` validates the directory exists at mount time. Directories are created at module level BEFORE `app = FastAPI(...)`. This was a real bug — do not move directory creation into the lifespan handler only.

**uvicorn.run import string.** `main()` calls `uvicorn.run("app:app", ...)` — the double `app:app` is intentional because `app.py` contains `app = FastAPI()` at module level. Changing this breaks the service.

**Two database access patterns.** FastAPI routes use the async session; the sync job handlers (in `jobs/tasks.py`) use `get_sync_session()` via `asyncio.to_thread()`. Both patterns coexist and must not be mixed.

**Backend selection.** Never import a concrete engine class directly in application code — call `get_engine_class()` from `engine/__init__.py`. It reads `config.TTS_BACKEND` and imports the concrete class *lazily inside the function*, which is what keeps a Mac from importing `torch` and a CUDA box from importing `mlx`. Both engines also keep their heavyweight imports method-scoped, so `engine/mlx_engine.py` and `engine/cuda_engine.py` both import cleanly on either platform — that is what lets `tests/test_engine_contract.py` check both backends everywhere.

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
  db.py                 # SQLAlchemy models + set_db_path(), init_db(), get_sync_session().
  seed_voices.py        # Optional Ephergent seeding; a no-op unless characters_data.py exists.
  scan_voice_clones.py  # Imports voice_clones/*.{mp3,wav} + matching .txt into the library.
engine/
  __init__.py           # get_engine_class() — the backend factory (RURU_TTS_BACKEND).
  base.py               # TTSEngineBase ABC + shared generate_to_file()/_resolve_ref_text().
  result.py             # TTSResult — tuple-unpackable as (wav, sr), plus .wav/.sr/.write().
  mlx_engine.py         # MLXEngine — Apple Silicon, via mlx-audio.
  cuda_engine.py        # CUDAEngine — NVIDIA, via qwen-tts + torch.
  tts.py                # Back-compat shim: re-exports MLXEngine as TTSEngine.
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
install_macos.sh        # Production install: launchd user agent + nginx.
install_linux.sh        # Production install: systemd unit + nginx, CUDA backend.
reset_service.sh        # Service control (restart-only/clean-only/reset-db/clean-all).
docs/
  nvidia_feasibility.md # Dual-backend design notes (§5 implemented).
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
| `RURU_TTS_BACKEND` | `mlx` | `mlx` (Apple Silicon) or `cuda` (NVIDIA) |
| `RURU_MODEL_SIZE` | `auto` | auto/small/large. On CUDA, anything but `small` means the 1.7B pair |
| `RURU_CHUNK_SIZE_WORDS` | `200` | Lower = less memory per chunk |
| `RURU_MAX_RETRIES` | `3` | Chunk generation retry count |
| `RURU_CREATOR_TAG` | `Created by ruru` | Audio metadata tag |
| `RURU_EPHERGENT_REPO` | parent of repo root | Optional dir containing `characters_data.py`; seeding is skipped if absent |
| `RURU_AUDIO_FORMAT` | `mp3` | Output format |
| `RURU_AUDIO_BITRATE` | `128k` | Output bitrate |

Note: there is no Redis/Celery anywhere — the job runner is fully in-process (`jobs/queue.py`).

## Installation (Production — macOS)

```bash
# In the DEDICATED production clone at ~/bin/ruru, not the Syncthing-synced
# dev checkout under ~/Documents:
cd ~/bin/ruru
# Install dependencies + launchd user agent + nginx reverse proxy (idempotent)
sudo bash install_macos.sh
```

`install_macos.sh` is sudo-safe: it derives paths from the script location (never `$HOME`), re-runs user-level steps (`uv sync --extra mlx`, DB init, the launchd *user* agent) as `SUDO_USER`, and only uses root for nginx on port 80. The launchd agent lands in `~/Library/LaunchAgents/com.jeremy.ruru.web.plist`.

Use `reset_service.sh` to manage the running service: `restart-only`, `clean-only`, `reset-db`, `clean-all` (see `reset_service.sh help`). It detects the platform and drives `launchctl` or `systemctl` accordingly. On macOS, logs go to `data/logs/ruru.{log,err}`.

## Installation (Production — Linux / NVIDIA)

```bash
# In a DEDICATED production clone, not a Syncthing-synced dev checkout:
git clone git@github.com:BitsofJeremy/ruru.git /8TB/bin/ruru
cd /8TB/bin/ruru && sudo bash install_linux.sh
```

`install_linux.sh` is sudo-safe in the same way: paths come from the script location, `uv sync --extra cuda` and DB init run as `SUDO_USER`, and root is only used for the systemd unit and nginx. It installs `ruru-web.service` (env: `RURU_TTS_BACKEND=cuda`, `RURU_MODEL_SIZE=large`, `HF_HOME` pointed at the existing weight cache) and an nginx vhost for `ruru.nexus.home.test` → `127.0.0.1:5151`. It also retires the old `sprecher-web` service and vhost — ruru is its successor. Logs go to the journal: `journalctl -u ruru-web -f`.

**Never run the installer from the Syncthing-synced dev checkout.** A macOS `.venv` syncing into a live service directory leaves a dangling interpreter symlink, which is exactly how `sprecher-web.service` ended up crash-looping with `203/EXEC`.

The two 1.7B bf16 models need ~3.5 GB of VRAM each. The engine keeps only one resident at a time, but a competing GPU workload (ComfyUI holds ~8 GB when running) will still OOM a narration job.

## Uninstall

See `docs/uninstall.md` for step-by-step teardown instructions.

## Common Pitfalls

- **pydub.silence**: `detect_leading_silence` is a standalone function from `pydub.silence`, NOT a method on `AudioSegment`. Always import as `from pydub.silence import detect_leading_silence`.
- **Voice slug collisions**: Fixed — `_unique_slug()` appends a counter suffix on UNIQUE constraint collision. No longer raises on duplicate names.
- **Quick TTS response**: Fixed — `/api/tts` now returns an `HTMLResponse` with an `<audio>` tag fragment for HTMX swap.
- **Job progress polling**: Job detail uses HTMX polling (`hx-get` every 3s) to `/api/jobs/{id}/progress`.
- **Port conflict**: If the server was previously started manually (`uv run ruru`), the process keeps port 5151 even after restarting the service. Kill manually-started processes before `reset_service.sh restart-only`.
- **Backend imports**: don't `from engine.tts import TTSEngine` in new code — that shim only ever yields the MLX engine. Use `get_engine_class()`. Keep every `mlx_audio`/`torch`/`qwen_tts` import method-scoped; a module-level one breaks the other platform's test run.
- **CUDA returns raw arrays**: `qwen_tts` hands back `(list[ndarray], sr)`. `CUDAEngine` wraps it in a `TTSResult`; returning the bare tuple satisfies the unpacking call sites and then fails at `result.write(...)`. `tests/test_engine_contract.py` guards this.

## UI Conventions

- Dark mode with custom Tailwind colors: `kj-bg`, `kj-card`, `kj-border`, `kj-accent` (purple), `kj-accent2` (teal), `kj-text`, `kj-muted`
- HTMX for all interactivity — no custom JavaScript
- Status badges: running = teal pulse-dot, completed = green, failed = red, queued = yellow
- Voice type badges: ephergent = purple, cloned = blue, designed = green

## Character Data

Voices come from `voice_clones/` — an audio file plus a same-stem `.txt` transcript (e.g. `lilith.mp3` + `lilith.txt`), imported into the library on startup by `models/scan_voice_clones.py`.

The Ephergent characters are optional: `seed_voices.py` imports `characters_data.py` from `RURU_EPHERGENT_REPO` if it's there, and is a **no-op when it isn't** (neither `characters_data.py` nor `ephergent_voices/` ship with this repo).

## Testing

Pytest suite lives in `tests/` (unit tests for chunker, assembler, parsers, config, db, job models/queue/tasks, and route tests for tts/voices/narrate/jobs). Run with `uv run pytest`. `pytest-asyncio` and `pytest-mock` are dev dependencies; model loading is mocked (`tests/conftest.py` provides `MOCK_WAV`, `fake_audio()`, and the `mock_tts_engine` fixture that patches the backend factory) so tests never touch the GPU.

The whole suite must pass on **both** platforms. Two files carry that guarantee:
- `tests/test_engine_contract.py` — checks every backend class against the ABC: signatures, `is_loaded` really being a property, synchronous methods, and that both generators return something tuple-unpackable *and* `.write()`-able. Tests needing a live runtime skip themselves.
- `tests/test_engine_factory.py` — backend selection, plus assertions that picking one backend never imports the other's dependencies.

Platform-specific tests skip rather than fail: `tests/test_tts_engine.py`'s generation tests are gated behind a `find_spec("mlx_audio")` check.

`epub_parser_check.py` at the repo root is a separate standalone manual script (PEP 723) for verifying EPUB parsing and optionally running real TTS narration against specific chapters. Named to avoid pytest's `test_*.py` collection (its plain functions aren't pytest tests). Use it directly with `uv run`.

## Known Issues

See `TODO.md` for the full list. Open items: no CSRF protection (acceptable for single-user local app); the voice edit page's image upload/remove controls call `/api/voices/{id}/image`, which doesn't exist yet (404) — see `PROGRESS.md` "Session: 2026-07-10" for details.

## Project Directories

- `.claude/` — Claude Code session configuration and memory
- `.worktrees/` — Isolated git worktrees created by the `superpowers:using-git-worktrees` skill
- `voice_clones/` — Voice clone reference audio + matching `.txt` transcripts (version-controlled)