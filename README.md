# ruru — MLX Version

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-AGPL--3.0-green)
![Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-MLX-black)

---

Audiobook narration engine powered by [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) running locally on Apple Silicon via [MLX](https://github.com/ml-explore/mlx). Upload documents, pick a voice, and generate full audiobooks with on-device text-to-speech.

ruru is a single-user web application built with FastAPI and HTMX. It ships with one pre-seeded default voice (), and supports creating new voices via voice cloning or voice design. (-style character-voice bulk seeding is an optional, local-only feature for developers who supply their own `characters_data.py` — it is not part of the public release.)

The name is a nod to ruru, the morepork owl — known for its distinctive two-note night call, fitting for a tool that runs overnight turning books into audio.

## Documentation

- [API Reference](docs/API.md) — Complete REST API documentation for all endpoints

## Features

- **Document narration** — Upload EPUB or TXT files, split into chapters and chunks, narrate with a single voice
- **Quick TTS** — Type text, pick a voice, get audio instantly; a Recent Clips history panel keeps your last 10 generated clips on the page
- **Voice library** — Browse, audition, and manage voices
- **Voice creation** — Clone a voice from a reference audio clip, or design one from a text description
- **Voice preview** — Generate an on-demand sample for any designed voice, narrated from a random line
- **Full CRUD** — Create, edit, and delete voices and jobs via the UI or JSON API — deleting a voice preserves its job history instead of failing or erasing it
- **Storage admin** — Clean up generated audio files and clear completed/failed job history from one page
- **Background jobs** — Long-running narration jobs processed in-process with real-time progress
- **Health check** — `/health` endpoint reports database connectivity
- **Dark mode UI** — Gumroad-inspired dark theme with HTMX-driven interactivity (no SPA framework)

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | >= 3.10 | Tested on 3.12 |
| [uv](https://docs.astral.sh/uv/) | latest | Package runner — no pip needed |
| Apple Silicon (M-series) | 16 GB+ unified memory recommended | MLX runs on the Apple GPU; the 1.7B 8-bit models peak around ~7 GB |
| ffmpeg | any recent | Audio format conversion (pydub) — `brew install ffmpeg` |
| Nginx | optional | Reverse proxy for production — `brew install nginx` |

## Quick start

```bash
uv sync
uv run app.py
```

Open [http://localhost:5151](http://localhost:5151) in your browser.

## Production deployment (macOS)

Install as a launchd user agent behind an nginx reverse proxy (idempotent — safe to re-run):

```bash
sudo bash install_macos.sh
```

This installs the LaunchAgent at `~/Library/LaunchAgents/com.jeremy.ruru.web.plist` (listening on port 5151) and an nginx site that proxies port 80 → 5151. After that the app is reachable at [http://localhost](http://localhost).

Manage the running service:

```bash
# Restart after a code change (no state wiped)
launchctl kickstart -k gui/$(id -u)/com.jeremy.ruru.web

# Restart + clear output/uploads/logs (voices are re-seeded on start)
./reset_service.sh restart-only
```

View logs (written to the data dir, not journald):

```bash
tail -f data/logs/ruru.err
```

## Configuration

All settings are controlled via environment variables with the `RURU_` prefix.

| Variable | Default | Description |
|---|---|---|
| `RURU_WORK_DIR` | `<repo>/data/` | Root directory for the DB and the `cache/` data tree |
| `RURU_PORT` | `5151` | Web server port |
| `RURU_HOST` | `0.0.0.0` | Web server bind address |
| `RURU_MLX_MODELS_PATH` | `~/.cache/huggingface/hub` | Where the MLX/HF model weights are cached |
| `RURU_MODEL_SIZE` | `auto` | TTS model size: `auto`, `small`, or `large` |
| `RURU_AUDIO_FORMAT` | `mp3` | Output audio format |
| `RURU_AUDIO_BITRATE` | `128k` | Output audio bitrate |
| `RURU_CHUNK_SIZE_WORDS` | `200` | Words per TTS chunk (lower = less memory per chunk) |

## Project structure

```
app.py               # FastAPI app entry point + all routes
config.py            # Configuration with env var overrides
pyproject.toml       # Dependencies and uv settings
install_macos.sh     # Production install: launchd agent + nginx (idempotent)
reset_service.sh     # Restart / clean / reset-db helper for the launchd service
voice_clones/        # Default voice reference audio (.mp3 + .txt)
jobs/
  models.py          # Job state machine (pending/running/completed/failed)
  queue.py           # In-process asyncio job runner
  tasks.py           # TTS generation tasks (generate_tts, narrate_document)
models/
  db.py              # SQLite schema (voices, jobs, chapters)
  seed_voices.py     # Optional  character voice seeding (local-only)
  scan_voice_clones.py # Seeds the default  voice from voice_clones/ on startup
engine/
  tts.py             # TTSEngine — model lifecycle and generation
  parsers.py         # EPUB and TXT document parsing
  chunker.py         # Sentence-boundary-aware text splitting
  assembler.py       # Audio concatenation and silence trimming
templates/
  base.html          # Layout with sidebar nav
  index.html         # Dashboard — stats and job list
  tts.html           # Quick TTS form
  voices.html        # Voice library grid
  voice_add.html     # Voice creation (design + clone)
  voice_edit.html    # Voice edit form
  narrate.html       # Document upload and narration
  job_detail.html    # Job progress with live polling
  admin_storage.html # Storage administration
  error.html         # Styled 404/500 error page
static/
  css/style.css      # Custom styles and animations
  css/toast.css      # Toast notification styles
  js/htmx.min.js     # HTMX 2.0.4 (vendored)
  js/toast.js        # Toast notification listener
  js/redirect.js     # HTMX redirect listener
  favicon.svg        # App favicon
```

## Architecture

```
Browser (HTMX) ──> FastAPI (port 5151) ──> SQLite (jobs, voices)
                        │
                        ├── asyncio job runner (in-process, single-threaded)
                        │
                        └── Static files (voices, output audio)
                               │
                          Qwen3-TTS models (MLX, Apple Silicon GPU)
```

- **FastAPI** serves pages and API endpoints. Templates are rendered server-side with Jinja2. HTMX handles interactivity (form submission, progress polling) without a JavaScript framework.
- **asyncio job runner** manages the job queue in-process. Jobs run sequentially on the Apple GPU (concurrency=1 by design). Progress is written to SQLite so the web UI can poll for status.
- **Qwen3-TTS** provides two generation modes:
  - **Voice clone** — reproduce a voice from a reference audio clip + transcript
  - **Voice design** — create a voice from a text description alone
- **SQLite** stores voice metadata, job state, and chapter info. Audio files live on the filesystem.

## Voice creation

### Voice design (no audio needed)

Describe the voice you want in plain text:

> "A warm, authoritative male voice in his 40s with a slight rasp and measured cadence"

The TTS model generates a matching voice using the `VoiceDesign` model variant.

### Voice cloning (reference audio required)

Upload a short audio clip (5-30 seconds) of clear speech along with its transcript. The `Base` model variant reproduces the voice characteristics.

## Supported document formats

| Format | Parser | Notes |
|---|---|---|
| EPUB | ebooklib + BeautifulSoup4 | Chapter detection from spine order |
| TXT | Built-in | Chapters split on blank-line-separated headings |

## Tech stack

- **[FastAPI](https://fastapi.tiangolo.com/)** — async web framework
- **[HTMX](https://htmx.org/)** — server-driven interactivity
- **[Tailwind CSS](https://tailwindcss.com/)** — utility-first styling (CDN)
- **[aiosqlite](https://github.com/omnilib/aiosqlite)** — async SQLite
- **[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)** — text-to-speech models
- **[pydub](https://github.com/jiaaro/pydub)** — audio processing
- **[uv](https://docs.astral.sh/uv/)** — Python package management

## License

GNU AFFERO GENERAL PUBLIC LICENSE