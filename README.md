# ruru

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-AGPL--3.0-green)
![Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-MLX-black)
![NVIDIA](https://img.shields.io/badge/NVIDIA-CUDA-76b900)

---

Audiobook narration engine powered by [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) running **entirely on your own hardware**. Upload documents, pick a voice, and generate full audiobooks with local text-to-speech. v0.7.0 adds music generation — 48 kHz stereo songs via [YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B) on the same hardware.

Two interchangeable GPU backends, selected with `RURU_TTS_BACKEND`:

| Backend | Hardware | Runtime | Weights |
|---|---|---|---|
| `mlx` (default) | Apple Silicon | [MLX](https://github.com/ml-explore/mlx) via `mlx-audio` | 8-bit `mlx-community/Qwen3-TTS-12Hz-1.7B-*` |
| `cuda` | NVIDIA GPU | PyTorch via `qwen-tts` | bf16 `Qwen/Qwen3-TTS-12Hz-1.7B-*` |

Everything above the engine — jobs, database, routes, templates — is shared. Both backends satisfy one interface (`engine/base.py`), enforced by a contract test suite that runs on either platform.

ruru is a single-user web application built with FastAPI and HTMX. It ships with five example voices — **Gus**, **Mabel**, **Ruth**, **Vera**, and **Wesley**, a cast of callers and archivists from a late-night radio show about the impossible — and supports creating new voices via voice cloning or voice design.

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
| ffmpeg | any recent | Audio format conversion (pydub) |
| Disk | ~16 GB+ | TTS weights ~5 GB; YuE2 music weights ~5 GB more (downloaded on demand) |
| Nginx | optional | Reverse proxy for production |

Plus **one** of:

| Backend | Requirement | Notes |
|---|---|---|
| `mlx` | Apple Silicon (M-series), 16 GB+ unified memory recommended | Install with `uv sync --extra mlx`. MLX runs on the Apple GPU; the 1.7B 8-bit models peak around ~7 GB. MLX music runtime peaks around 23 GB unified memory |
| `cuda` | NVIDIA GPU, ~5 GB free VRAM | Install with `uv sync --extra cuda`. Each bf16 1.7B model is ~4-5 GB; only one is held resident at a time, so a competing GPU workload can still cause an OOM. Music (YuE2) needs more: ~7–8 GB peak with the GGUF runtime, 11–14 GB with PyTorch |

## Quick start

```bash
# Apple Silicon
uv sync --extra mlx
uv run app.py

# NVIDIA
uv sync --extra cuda
RURU_TTS_BACKEND=cuda uv run app.py
```

Music generation (YuE2) works on either backend and downloads its weights on
first use — or pre-fetch them, e.g. the GGUF runtime on Linux:

```bash
uv run python -c "
from huggingface_hub import snapshot_download
snapshot_download('audio-cpp/Yue2-3B-GGUF',
    allow_patterns=['yue2-3b-q4_k_m.gguf','yue2-vae-f16.gguf','sidecars/*'],
    local_dir='data/models/yue2-gguf')"
```

The `gguf` runtime also needs an `audio.cpp` binary (dev branch, built from
source) pointed at by `RURU_YUE2_AUDIOCPP_BIN`. On macOS the MLX runtime needs
no extra binary. Full details, runtimes, VRAM numbers, and the instrumental
LoRA: see [Music](#music-yue2-3b--v070) below.

Open [http://localhost:5151](http://localhost:5151) in your browser. The sidebar shows which backend is active.

> Install the extra that matches your hardware — plain `uv sync` installs neither backend. `mlx` and `cuda` are declared mutually exclusive in `pyproject.toml` (`[tool.uv] conflicts`) so `uv lock` resolves each independently instead of forcing `qwen-tts`'s `transformers==4.57.3` pin onto the Mac's resolution, which used to silently downgrade `mlx-audio` to a version without Qwen3-TTS support at all.

## Production deployment (macOS)

Install as a launchd user agent behind an nginx reverse proxy (idempotent — safe to re-run):

```bash
sudo bash install_macos.sh
```

This installs the LaunchAgent at `~/Library/LaunchAgents/com.jeremy.ruru.web.plist` (listening on port 5151) and an nginx site that proxies port 80 → 5151. After that the app is reachable at [http://localhost](http://localhost).

> As on Linux, run the installer from a **dedicated clone** rather than a development checkout — the installer points a service at whatever directory it lives in, and a synced `.venv` will break it.

Manage the running service:

```bash
# Restart after a code change (no state wiped)
launchctl kickstart -k gui/$(id -u)/com.jeremy.ruru.web

# Restart + clear output/uploads/logs (voices and the database are preserved)
./reset_service.sh restart-only
```

View logs (written to the data dir, not journald):

```bash
tail -f data/logs/ruru.err
```

## Production deployment (Linux / NVIDIA)

Install as a systemd service behind an nginx reverse proxy (idempotent — safe to re-run):

```bash
sudo bash install_linux.sh
```

This installs `ruru-web.service` (running as the invoking user, listening on 5151, with `RURU_TTS_BACKEND=cuda`) and an nginx site proxying port 80. `HF_HOME` is set so the service reuses your existing model cache rather than re-downloading ~11 GB.

> Run the installer from a **dedicated clone**, not a development checkout that is synced between machines. A macOS `.venv` landing in a live service directory leaves a dangling interpreter symlink and the unit fails with `203/EXEC`.

Manage the running service:

```bash
systemctl status ruru-web
journalctl -u ruru-web -f

# Restart + clear output/uploads (voices and the database are preserved)
./reset_service.sh restart-only
```

`reset_service.sh` detects the platform and drives `launchctl` or `systemctl` accordingly.

## Configuration

All settings are controlled via environment variables with the `RURU_` prefix.

| Variable | Default | Description |
|---|---|---|
| `RURU_WORK_DIR` | `<repo>/data/` | Root directory for the DB and the `cache/` data tree |
| `RURU_PORT` | `5151` | Web server port |
| `RURU_HOST` | `0.0.0.0` | Web server bind address |
| `RURU_TTS_BACKEND` | `mlx` | TTS backend: `mlx` (Apple Silicon) or `cuda` (NVIDIA) |
| `RURU_MLX_MODELS_PATH` | `~/.cache/huggingface/hub` | Where the MLX/HF model weights are cached |
| `RURU_MODEL_SIZE` | `auto` | TTS model size: `auto`, `small`, or `large`. On CUDA anything but `small` selects the 1.7B pair |
| `RURU_AUDIO_FORMAT` | `mp3` | Output audio format |
| `RURU_AUDIO_BITRATE` | `128k` | Output audio bitrate |
| `RURU_CHUNK_SIZE_WORDS` | `120` | Words per TTS chunk (lower = less memory per chunk) |
| `RURU_CHUNK_MAX_CHARS` | `700` | Hard character ceiling per chunk |
| `RURU_MAX_RETRIES` | `3` | Retries when a chunk comes back the wrong length |

## Project structure

```
app.py               # FastAPI app entry point + all routes
config.py            # Configuration with env var overrides
pyproject.toml       # Dependencies and uv settings
install_macos.sh     # Production install: launchd agent + nginx (idempotent)
install_linux.sh     # Production install: systemd unit + nginx (idempotent)
reset_service.sh     # Restart / clean / reset-db helper (launchd or systemd)
voice_clones/        # Example voices: audio + transcript + design prompt per voice
jobs/
  models.py          # Job state machine (pending/running/completed/failed)
  queue.py           # In-process asyncio job runner
  tasks.py           # TTS generation tasks (generate_tts, narrate_document)
models/
  db.py              # SQLAlchemy models + schema (voices, jobs, chapters)
  scan_voice_clones.py # Imports every voice_clones/ audio+transcript pair on startup
  import_voices.py   # Migrate a voice library from another install
engine/
  __init__.py        # get_engine_class() — backend factory (RURU_TTS_BACKEND)
  base.py            # TTSEngineBase — the interface both backends implement
  result.py          # TTSResult — tuple-unpackable as (wav, sr), plus .write()
  mlx_engine.py      # MLXEngine — Apple Silicon, via mlx-audio
  cuda_engine.py     # CUDAEngine — NVIDIA, via qwen-tts + torch
  yue2_engine.py     # YuE2Engine — music generation (gguf/pytorch/mlx runtimes)
  yue2_lora.py       # Vendored instrumental-LoRA merge for YuE2
  tts.py             # Back-compat shim re-exporting MLXEngine as TTSEngine
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
                          Qwen3-TTS models
                          (MLX / Apple GPU  or  PyTorch / NVIDIA CUDA)
```

- **FastAPI** serves pages and API endpoints. Templates are rendered server-side with Jinja2. HTMX handles interactivity (form submission, progress polling) without a JavaScript framework.
- **asyncio job runner** manages the job queue in-process. Jobs run sequentially on the GPU (concurrency=1 by design). Progress is written to SQLite so the web UI can poll for status.
- **Backend factory** — application code never imports an engine class directly; `get_engine_class()` resolves `RURU_TTS_BACKEND` and imports the concrete class lazily, so a Mac never imports `torch` and an NVIDIA box never imports `mlx`.
- **Qwen3-TTS** provides two generation modes:
  - **Voice clone** — reproduce a voice from a reference audio clip + transcript
  - **Voice design** — create a voice from a text description alone
- **SQLite** stores voice metadata, job state, and chapter info. Audio files live on the filesystem.

## Bundled example voices

Five character voices ship in `voice_clones/` and are imported into the library on first startup. Each is a `.mp3` reference clip + matching `.txt` transcript (the clone pair) plus a `.prompt.txt` design prompt describing the persona.

| Voice | Persona | Character of the voice |
|---|---|---|
| **Gus** | Warm Ordinary Everyman | Middle-aged American man, mid-register, unpolished — a bewildered but decent neighbor recounting something impossible |
| **Mabel** | Bright Young Adult Caller | Early-20s, quick and hushed, delivering unsettling observations with the calm pride of someone who brought notes |
| **Ruth** | Dry Wry Archivist | Older woman, low and precise, deadpan — a librarian of the impossible who knows more than she'll say |
| **Vera** | Sharp Wry Skeptic | Early-40s, clipped and analytical, allergic to mysticism, composing the footnote that will disprove you in real time |
| **Wesley** | Open Hearted Believer | Late-20s man, warm and rapid, mall-kiosk tech energy, homesick for places he's never been |

Drop your own audio + transcript pair into `voice_clones/` to seed additional voices the same way.

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

## Testing

```bash
uv run pytest
```

The suite runs on both platforms and never touches the GPU — model loading is mocked. Tests that need a runtime that isn't installed skip themselves, so the counts differ by machine (a Mac runs the MLX generation tests; a CUDA box skips them, and vice versa).

**A green suite does not prove the engine works.** Because model loading is mocked, the suite stays green against a backend that cannot load a model at all — which is exactly how a silent `mlx-audio` downgrade once shipped unnoticed. After any dependency change, run one real generation before trusting a green run:

```bash
uv run python -c "
from engine import get_engine_class
e = get_engine_class()()
e.generate_clone('Testing one two three',
                 'voice_clones/vera.mp3',
                 'voice_clones/vera.txt').write('/tmp/check.mp3')"
```

`tests/test_engine_contract.py::TestMLXHasQwen3TTSArchitecture` guards the specific regression above by importing the real architecture module — cheap, no GPU — but it can only cover what a static import can see.

Two files hold the dual-backend design together:

- `tests/test_engine_contract.py` — checks every backend class against the shared interface: method presence and signatures, `is_loaded` genuinely being a property, synchronous methods, and that both generators return something that is **both** tuple-unpackable and `.write()`-able. That last one matters: returning a bare `(wav, sr)` tuple satisfies the unpacking call sites and then fails at `result.write(...)`.
- `tests/test_engine_factory.py` — backend selection, plus assertions that choosing one backend never imports the other's dependencies.

Together they mean a backend developed on one machine can be trusted to drop into the other's call sites.

## Tech stack

- **[FastAPI](https://fastapi.tiangolo.com/)** — async web framework
- **[HTMX](https://htmx.org/)** — server-driven interactivity
- **[Tailwind CSS](https://tailwindcss.com/)** — utility-first styling (CDN)
- **[SQLAlchemy](https://www.sqlalchemy.org/) + [aiosqlite](https://github.com/omnilib/aiosqlite)** — ORM and async SQLite
- **[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)** — text-to-speech models
- **[YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B)** — music generation (v0.7.0)
- **[pydub](https://github.com/jiaaro/pydub)** — audio processing
- **[uv](https://docs.astral.sh/uv/)** — Python package management

## Music (YuE2-3B) — v0.7.0

ruru can generate 48 kHz stereo music with [YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B)
via the **Music** page. Two interchangeable runtimes, picked per request:

| Runtime | Weights | VRAM | LoRA | Notes |
|---|---|---|---|---|
| `gguf` (default on Linux) | Q4_K_M + F16 VAE from `audio-cpp/Yue2-3B-GGUF` (~5 GB on disk) | ~7–8 GB peak | **no** | audio.cpp subprocess (dev branch, build from source) |
| `pytorch` | `m-a-p/YuE2-3B` bf16 via the `yue2_infer` wheel | 11–14 GB peak | **yes** | instrumental LoRA lives here; 90 s song cap by default |
| `mlx` (default on macOS) | `ahmadw/YuE2-3B-MLX` 8-bit | ~23 GB unified | **no** | MLX repo has **no adapter loading** — verified 2026-09-16 |

The LoRA is [`Mothersuperior/YuE2-instrumental-cot-full-loras`](https://huggingface.co/Mothersuperior/YuE2-instrumental-cot-full-loras)
(AR branch, rank 64, `cot="full"`), merged with the vendored
`engine/yue2_lora.py` — adapted from that repo's own `scripts/` (`W += scale*B@A`
fold into the base weights, keeping CUDA graphs working). Base-model instrumental
generation without the LoRA is effectively impossible — that's why the checkbox
exists.

**Setup (Linux, Q4 default):**

```bash
# 1. Weights (~5 GB)
uv run python -c "
from huggingface_hub import snapshot_download
snapshot_download('audio-cpp/Yue2-3B-GGUF',
    allow_patterns=['yue2-3b-q4_k_m.gguf','yue2-vae-f16.gguf','sidecars/*'],
    local_dir='data/models/yue2-gguf')"

# 2. audio.cpp binary (dev branch); then:
export RURU_YUE2_AUDIOCPP_BIN=/path/to/audiocpp/binary
```

**Setup (instrumental LoRA / pytorch runtime):** install
`huggingface-hub==0.36.2` + the `yue2_infer-0.1.5-py3-none-any.whl` from
`m-a-p/YuE2-3B` (watch the `transformers==4.57.3` pin from `qwen-tts` — use
`--no-deps` if uv reports a conflict).

> **⚠️ `uv sync` removes yue2-infer.** Because it's installed `--no-deps`
> (outside the lockfile), every `uv sync --extra cuda` prunes it. After any
> sync, reinstall before music will work:
> ```bash
> uv pip install --python .venv/bin/python --no-deps ./yue2_infer-0.1.5-py3-none-any.whl tiktoken
> ```
> (Download the wheel from the `m-a-p/YuE2-3B` HF repo.) Symptom if forgotten:
> `ModuleNotFoundError: No module named 'yue2'` in the ruru-web journal when a
> music job runs.

Knobs: `RURU_YUE2_RUNTIME` (gguf/pytorch/mlx/"" = auto),
`RURU_YUE2_MAX_SECONDS` (90; drop to 60 if the 12 GB card OOMs),
`RURU_YUE2_INST_LORA_SCALE` (1.0), `RURU_YUE2_COT` (full).
Model + LoRA are CC BY-NC 4.0 (non-commercial). Music on Apple Silicon has no
LoRA and no realistic path to one until the MLX port grows adapter support.

## License

GNU AFFERO GENERAL PUBLIC LICENSE

