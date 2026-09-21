# TODO

## Known issues

- **Scanner/UI slug mismatch can duplicate voices on restart.** `scan_voice_clones()`
  slugs from the audio filename stem (`gus.mp3` → `gus`), while the web-UI clone
  flow slugs from the persona name (`Gus Guy` → `gus-guy`). A UI-cloned voice
  whose stem ≠ persona name gets re-imported as a duplicate on startup. Proper
  fix: match existing voices by something stable (ref-audio content/path or
  name), or have voice_add adopt the scanner's stem-based slug.
- Voice edit page's image upload/remove controls call `/api/voices/{id}/image`,
  which doesn't exist yet (404).
- No CSRF protection (acceptable for a single-user local app).
- Each job constructs a fresh engine, so the TTS model reloads per job
  (~15-20 s before the first chunk). Worth revisiting if narration
  throughput ever matters.

## Someday / maybe

- CI: a GitHub Actions job running `uv sync --extra cuda && uv run pytest` on
  `ubuntu-latest`. Catches every "this only imports on a Mac" mistake at push
  time; won't catch MLX runtime regressions.
- Two jobs uploading the same filename share one `uploads/<name>` path;
  deleting either job unlinks the file while the other job's row still
  references it.
