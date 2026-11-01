---
name: slow-tts-ruru
description: Generate speech audio from short text via the ruru Qwen3-TTS (MLX) HTTP API. Use to synthesize short clips, list available voices, check job status, and download the resulting MP3 from a running ruru instance. Defaults to the Lilith voice, and to the RTX 3060 desktop with automatic fallback to the Mac. For whole-document narration, use the ruru web UI (/narrate) instead — do not loop long text through this API.
---

# slow-tts-ruru

Drive a running ruru instance to synthesize short speech clips over HTTP.

## When to use

- Turn a short line of text (a sentence or two) into an MP3 in a specific voice.
- List the voices ruru knows about.
- Check the status of, or download, a TTS job.

**Not for long-form narration.** Whole documents block the request and time out. For an audiobook, tell the user to upload the file on the ruru web UI at `/narrate`.

## Prerequisites

ruru defaults to the RTX 3060 desktop at `http://ruru.nexus.home.test`, falling back to the
Mac-local instance at `http://localhost:5151` when the desktop is unreachable (e.g. off the home
network). **Resolve the host once and reuse `$BASE` for every call below — never hardcode one:**

```bash
BASE=$(~/.claude/scripts/ruru-base.sh) || { echo "ruru unreachable on any configured host"; exit 1; }
curl -s "$BASE/health"   # -> {"status":"ok","version":"0.9.0"}
```

The chain lives in `~/.claude/scripts/ruru-hosts.conf`; set `RURU_URL` to pin one host and skip
probing. Resolution is cached for 5 minutes, so repeated calls stay on the same instance.

If nothing answers, restart whichever host you meant to use:
- **Mac:** `cd /Users/jeremy/bin/ruru && ./reset_service.sh restart-only` (launchd) or `uv run ruru` (dev)
- **Linux desktop (RTX 3060):** restart ruru on `ruru.nexus.home.test` manually on that machine —
  there is no SSH automation for it from here.

## Resolve the voice (default: Lilith)

Never hardcode a voice id. The DB is re-seeded and ids change — and **ids differ per host**
(Lilith is `1` on the desktop, `12` on the Mac), so a hardcoded id would narrate in the wrong
voice depending on which instance answered. Always resolve by slug at call time:

```bash
LILITH_ID=$(curl -s "$BASE/api/voices" \
  | python3 -c "import sys,json;print(next(v['id'] for v in json.load(sys.stdin) if v['slug']=='lilith'))")
```

List all voices to pick a different one:

```bash
curl -s "$BASE/api/voices" | python3 -m json.tool
# each entry: {"id", "name", "slug", "voice_type"}
```

## Synthesize a short clip

`POST /api/tts/generate` enqueues the job, waits for the single clip (cheap on Apple Silicon MLX), and returns JSON with the audio URL. First call after a cold start loads the MLX model and may take ~30–60s.

```bash
RESP=$(curl -s -X POST "$BASE/api/tts/generate" \
  -H 'Content-Type: application/json' \
  -d "{\"text\":\"Hello, this is a test.\",\"voice_id\":$LILITH_ID}")
echo "$RESP"   # -> {"job_id":N,"status":"completed","audio_url":"/audio/output/tts_N.wav"}

AUDIO_URL=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['audio_url'])")
curl -s -o out.mp3 "$BASE$AUDIO_URL"
```

On failure the endpoint returns HTTP 500 with `{"job_id","status":"failed","message":...}`.

## Check a job / download later

```bash
curl -s "$BASE/api/jobs/JOB_ID" | python3 -m json.tool
# -> {"id","status","progress","audio_url"}   (audio_url is null until completed)

curl -s -o out.mp3 "$BASE/api/jobs/JOB_ID/download"
```

## API contract (real routes)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | `{status, version}` |
| GET  | `/api/voices` | list `[{id, name, slug, voice_type}]` |
| POST | `/api/tts/generate` | body `{text, voice_id, audio_format?}` → `{job_id, status, audio_url}` |
| GET  | `/api/jobs/{id}` | `{id, status, progress, audio_url}` |
| GET  | `/api/jobs/{id}/download` | redirect to the audio file |

## Constraints

- Short text only. Long text re-introduces request blocking/timeout — steer it to the web `/narrate` UI.
- ruru 0.0.3+ (the JSON API surface). Hosts update independently — `curl $BASE/health` for the version rather than assuming.
