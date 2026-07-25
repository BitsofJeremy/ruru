# ruru — MLX Version API Reference

Base URL (direct): `http://localhost:5151`
Base URL (via nginx in production): `http://localhost`

ruru is a single-user app. There is **no authentication** and no Redis/Celery —
jobs run in-process on an asyncio `JobRunner` (see `jobs/queue.py`) and TTS runs
locally on Apple Silicon via MLX.

Two kinds of endpoints coexist:

- **HTMX endpoints** — consumed by the web UI. They return **HTML fragments**
  (or redirects via the `HX-Redirect` header), not JSON.
- **JSON endpoints** — a small programmatic surface for scripts/agents
  (see [`slow-tts-weirding`](../.claude/skills/slow-tts-weirding/SKILL.md)).

Version: `0.0.3`.

---

## JSON endpoints (programmatic)

### `GET /health`

```json
{ "status": "ok", "version": "0.0.3" }
```

### `GET /api/voices`

List all voices.

```json
[
  { "id": 1,  "name": "A1",     "slug": "a1",     "voice_type": "" },
  { "id": 12, "name": "", "slug": "", "voice_type": "cloned" }
]
```

`voice_type` is one of ``, `cloned`, or `designed`.  voices carry
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
| DELETE | `/api/jobs/{id}` | — | Deletes a job, its chapters, and its output file. `400` if the job is `queued`/`running`; `404` if not found. `HX-Redirect: /` |

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
`/narrate`, `/jobs/{id}`, `/admin/storage`.

### Static audio

Generated audio is served directly at `/audio/output/<filename>` (mounted from the
output cache dir). The inline players and the download redirect both point here.
