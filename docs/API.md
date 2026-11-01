# ruru — API Reference

Base URL (direct): `http://localhost:5151`
Base URL (via nginx in production): `http://localhost`

ruru is a single-user app. There is **no authentication** and no Redis/Celery —
jobs run in-process on an asyncio `JobRunner` (see `jobs/queue.py`) and TTS runs
locally via MLX (Apple Silicon) or PyTorch/CUDA (NVIDIA), selected with
`RURU_TTS_BACKEND`.

Two kinds of endpoints coexist:

- **HTMX endpoints** — consumed by the web UI. They return **HTML fragments**
  (or redirects via the `HX-Redirect` header), not JSON.
- **JSON endpoints** — a small programmatic surface for scripts/agents
  (see [`SKILL.md`](../SKILL.md)).

Version: don't hardcode it — ask `GET /health` (hosts update independently).

---

## JSON endpoints (programmatic)

### `GET /health`

```json
{ "status": "ok", "version": "0.9.0" }
```

### `GET /api/voices`

List all voices.

```json
[
  { "id": 1,  "name": "A1",     "slug": "a1",     "voice_type": "ephergent" },
  { "id": 12, "name": "Lilith", "slug": "lilith", "voice_type": "cloned" }
]
```

`voice_type` is one of `ephergent`, `cloned`, or `designed`. Ephergent voices carry
a real reference WAV and narrate as clones.

### `POST /api/tts/generate`

Synthesize a **short** clip. Enqueues the job and blocks until the single clip is
done (cheap on MLX). The first call after a cold start loads the model (~30–60s).

Request (JSON):

```json
{ "text": "Hello there.", "voice_id": 12, "audio_format": "mp3" }
```

`audio_format` is optional (default `mp3`). Response `200`:

```json
{ "job_id": 5, "status": "completed", "audio_url": "/audio/output/tts_5.wav" }
```

Errors: `400` (`text` missing), `404` (voice not found), `500`
(`{ "job_id", "status": "failed", "message" }`).

> Do **not** send long documents here — it re-introduces request blocking/timeout.
> Use `POST /api/narrate` (below) for whole documents.

### `GET /api/jobs/{id}`

Programmatic job status.

```json
{ "id": 5, "status": "completed", "progress": 100, "audio_url": "/audio/output/tts_5.wav" }
```

`audio_url` is `null` until the job completes. `status` is one of
`queued`, `running`, `completed`, `failed`. `404` if the job does not exist.

### `GET /api/jobs/{id}/download`

`302` redirect to the finished audio under `/audio/output/<file>`. `404` if the
job is missing or has no output yet.

### Live TTS (Phonon, optional)

Instant synthesis with one of 102 built-in Gradium canned voices — no voice
library, no job row, **nothing persisted server-side**. Runs on CPU via the
optional `phonon` extra, roughly 6× realtime.

#### `GET /api/tts/live/voices`

The voice catalog. Returns `200` even when live TTS is disabled, so clients can
probe cheaply:

```json
{
  "available": true,
  "voices": [
    { "name": "Hugh", "voice_id": "YyKcxPG7iIgS3jsK", "language": "en",
      "description": "Soft and close, airy in texture, with lively pitch movement and an attentive, sincere delivery." }
  ]
}
```

`available` is `false` (with an empty list) when the `phonon` extra is not
installed, `RURU_PHONON=0`, or the catalog failed to load.

#### `POST /api/tts/live`

Request (JSON):

```json
{ "text": "Hello from ruru.", "voice": "Hugh", "format": "wav" }
```

`format` is optional and currently only accepts `wav`. Response `200`: binary
24 kHz mono WAV (`Content-Type: audio/wav`).

Errors: `400` (empty `text` or unsupported `format`), `404` (unknown voice),
`413` (`text` over `RURU_PHONON_MAX_CHARS`, default 2000), `503` (live TTS
disabled or the extra is missing).

```bash
curl -s -X POST http://localhost:5151/api/tts/live \
  -H 'Content-Type: application/json' \
  -d '{"text": "Hello from ruru.", "voice": "Hugh", "format": "wav"}' \
  -o /tmp/hello.wav && afplay /tmp/hello.wav   # Linux: aplay or ffplay
```

---

## HTMX endpoints (used by the web UI)

These return HTML fragments and are documented for completeness; a browser driving
the UI is the intended client.

### TTS / narration

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/tts` | form: `text`, `voice_id`, `audio_format?` | HTML `<audio>` fragment (blocks; short clips); emits `HX-Trigger: refreshHistory` |
| GET | `/api/tts/clips` | — | HTML fragment listing the 10 most recent Quick TTS clips (Recent Clips panel on `/tts`) |
| POST | `/api/narrate` | form: `document` (upload), `voice_id`, `job_title?`, `author?`, `cover_art?` | `HX-Redirect: /jobs/{id}` — **non-blocking**, job runs in background |
| GET | `/api/jobs/{id}/progress` | — | HTML progress fragment; emits `HX-Trigger: job:complete` when terminal so polling stops |
| DELETE | `/api/jobs/{id}` | — | Deletes a job, its chapters, and its output file(s) — for music jobs this also sweeps every `music_<id>` format sibling. `400` if the job is `queued`/`running`; `404` if not found. `HX-Redirect: /` |

### Voices

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/voices/design` | form: `name`, `description`, `speaking_style?` | `HX-Redirect: /voices` |
| POST | `/api/voices/clone` | form: `name`, `ref_audio` (upload), `ref_text`, `description?` | `HX-Redirect: /voices` |
| POST | `/api/voices/refresh` | — | Rescans `voice_clones/`; toast with count of new voices |
| POST | `/api/voices/{id}/update` | form: `name`, `description`, `speaking_style?` | Updates a voice; `HX-Redirect: /voices` |
| DELETE | `/api/voices/{id}` | — | Deletes a voice. Any job that referenced it has `voice_id` nulled instead of blocking the delete. `HX-Redirect: /voices` |
| POST | `/api/voices/{id}/preview` | — | Designed voices only. Synthesizes a random Hitchhiker's Guide quote and returns an `<audio>` fragment; saves it as the voice's sample. `400` if not a designed voice |

Design/clone return `400` when required fields are missing.

### Music

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/music` | form: `style`, `lyrics`, `instrumental?`, `seed?`, `max_seconds?`, `formats` (repeatable), `cot?`, `cfg_scale?`, `steps?` | **Fire-and-forget** — enqueues the job and responds with an HTML polling fragment pointing at `/api/music/status/{job_id}` (`hx-trigger="load, every 3s until job:complete"`); the job runs in the background |
| GET | `/api/music/status/{job_id}` | — | HTML fragment: progress while `queued`/`running`; player + seed + per-format download links when `completed` (emits `HX-Trigger: {"job:complete": true, "refreshHistory": true}` — stops polling and reloads Recent Songs); failure message when `failed` (emits `HX-Trigger: job:complete`, stopping polling). `404` if not found |
| GET | `/api/music/clips` | — | HTML fragment listing the 12 most recent completed songs (Recent Songs panel on `/music`) |
| DELETE | `/api/music/clips/{job_id}` | — | Deletes the song row plus every format sibling. Empty `200` + `HX-Trigger: refreshHistory` so HTMX drops the card. `400` if `queued`/`running`; `404` if not found or not a music job |

`POST /api/music` validation — each failure is a `400` HTML fragment:

- `style` and `lyrics` required ("Style and lyrics required.").
- `seed` blank → random `0..2147483647`, recorded on the job and shown with the result (reproducible only within the MLX runtime); otherwise an integer in `0..2147483647`.
- `max_seconds` blank → auto (lyric tags decide; pytorch falls back to `RURU_YUE2_MAX_SECONDS`, default 90 s); otherwise an integer `1..300`.
- `formats` filtered to `wav`/`mp3`/`m4a`; at least one required ("Pick at least one export format."). WAV needs no ffmpeg; MP3/M4A do — missing ffmpeg fails the job with "MP3/M4A export requires ffmpeg — install ffmpeg or export WAV only".
- `cot` blank → engine default (`RURU_YUE2_COT`); otherwise `off`, `melody`, or `full`. Applies on all runtimes.
- `cfg_scale` blank → none; otherwise a number. `steps` blank → none; otherwise a positive integer. Both are MLX-only — other runtimes log and ignore them.

Outputs are `music_<id>.{wav,mp3,m4a}` in the output cache dir; the primary file (job player/download) follows mp3 > m4a > wav.

### Storage admin

| Method | Path | Returns |
|---|---|---|
| POST | `/api/storage/cleanup-old` | Deletes output/upload files older than 30 days; toast |
| POST | `/api/storage/delete-selected` | Deletes selected files; toast |
| POST | `/api/storage/delete-all` | Deletes all cache files; toast |
| DELETE | `/api/storage/files/{name}` | Deletes one file; toast |
| POST | `/api/storage/clear-jobs` | Deletes completed/failed/cancelled job rows (and their chapters). Queued/running jobs are always kept; toast |

Storage endpoints signal the UI via an `HX-Trigger` header carrying a `showToast`
object: `{"showToast": {"message": "...", "type": "success"}}`.

### Pages (HTML)

`GET /` (dashboard), `/voices`, `/voice/add`, `/voice/{id}/edit`, `/tts`,
`/narrate`, `/music`, `/jobs/{id}`, `/admin/storage`.

### Static audio

Generated audio is served directly at `/audio/output/<filename>` (mounted from the
output cache dir). The inline players and the download redirect both point here.
