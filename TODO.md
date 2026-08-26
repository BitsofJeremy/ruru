# TODO

## Dual-backend (MLX + CUDA) support and Linux production install

Implements the recommendation in `docs/nvidia_feasibility.md`: one repo, an
abstract `engine/base.py`, `MLXEngine`/`CUDAEngine` subclasses, backend chosen by
`RURU_TTS_BACKEND` — plus the Linux production install that makes ruru the
successor to the retired WeirDing and sprecher services.

**Development setup.** CUDA work happens on the NVIDIA Linux desktop (a plain
`git clone` of this repo). That clone is **dev only** — it is Syncthing-synced
with the MacBook and must never be a service directory. The Mac is the MLX
production install (launchd + nginx) and must never break, so every Linux branch
gets pulled and verified there before merging to `main`. Linux production is a
*separate* clone at `/8TB/bin/ruru` (Phase 3).

---

## STATUS as of 2026-07-26 (later same day) — Mac done, read this first

The Mac work below ("What's left — pick up here on the Mac") is now **done**.
Summary, evidence inline further down:

- Fixed the real bug found this session: `qwen-tts`'s `transformers==4.57.3`
  pin was silently backtracking `mlx-audio` to 0.2.10 on the Mac via uv's
  universal lock (see the "PLAIN uv sync" correction below). Fixed with
  conflicting `mlx`/`cuda` extras in `pyproject.toml` (commit `a8ea1cf`,
  pushed to `main`).
- Dev checkout (`~/Documents/current_projects/ai_speech/ruru`): `uv lock &&
  uv sync --extra mlx` → mlx-audio 0.4.6. `uv run pytest` → 175 passed, 6
  skipped. Real (non-mocked) `generate_clone` produced a valid MP3.
  `HF_HUB_OFFLINE=1` load succeeded — MLX does not have the CUDA offline
  bug (see §5 below, answered).
- Production clone (`/Users/jeremy/bin/ruru`): pulled to `a8ea1cf`, `uv sync
  --extra mlx` (resolved mlx-audio 0.4.4, clears the >=0.4.4 floor), `uv run
  pytest` → 175/6/0 (see the `test_voice_seed.py` note below — this clone has
  a local `characters_data.py` that made two tests fail until temporarily
  moved aside to prove root cause, then restored). Real generation check
  passed. `./reset_service.sh restart-only`: old PID 97249 → new PID, port
  5151 re-bound, HTTP 200, 13 voices / all reference audio intact (one
  disposable `*_preview.*` file cleared by design, not a data loss).
- New regression test in `tests/test_engine_contract.py` —
  `TestMLXHasQwen3TTSArchitecture` — guards against this exact silent
  downgrade recurring; proven to fail against a real 0.2.10 install.
- Not done: §6 CI (optional, not attempted this session).

---

## DONE — Linux rollout of 0.6.0, 2026-08-26

Both Linux clones at `b53a219` (v0.6.0). Prod verified live; one landmine
defused on the way (history rewrite) and one **upstream bug found** (see
"Scanner/UI slug mismatch" below).

- **History was rewritten** since 0.5.1 (old `eeea4e4…` → new `482ffad…`
  hashes; `cuda-backend` force-pushed too). Prod clone showed ahead-26/
  behind-34 — a plain pull would have merged. Correct move was
  `git reset --hard origin/main` (tree verified clean, `data/` ignored, the
  26 "local" commits were just the pre-rewrite ancestors). Dev had already
  pulled normally.
- **`reset --hard` deleted `voice_clones/lilith.*`** — `ae57512` untracked
  them via a tree delete, not `git rm --cached`. Restored by copying from the
  dev checkout (Syncthing). Lilith stays local-only on both boxes, as
  intended.
- **Suites**: dev and prod both **187 passed / 5 skipped** (new baseline —
  the 0.6.0 voice/prompt commits added 7). `torch 2.13.0+cu130` intact after
  `uv sync --extra cuda`.
- **Real generation** (service stopped): 12.7 s wall → 14.9 s clip, 24 kHz
  mono, no runaway (a 180 s `timeout` + duration assert is now part of the
  check).
- **Verified live through nginx**: 200, sidebar **CUDA · v0.6.0**,
  end-to-end narrate job (11.8 s MP3), `DELETE /api/jobs/{id}` sweeps chunk
  dir + upload + row. Journal clean.

### Scanner/UI slug mismatch — duplicates voices on restart (OPEN BUG)

`scan_voice_clones()` slugs from the **audio filename stem** (`gus.mp3` →
`gus`, ref copied as `gus_clone.mp3`), but the web-UI clone flow slugs from
the **persona name** (`Gus Guy` → `gus-guy`, ref `gus-guy_ref.mp3`). The five
example voices had been UI-cloned on Linux before 0.6.0, so the first 0.6.0
startup re-imported all five as duplicates (13 → 23 voices, every example
voice ×2).

**Interim fix applied on Linux prod (2026-08-26):** deleted the five
scanner-format rows (no jobs referenced them; DB backed up to
`data/ruru.db.bak-0.6.0-dedupe` first) and re-slugged the UI rows to the
scanner's stems (`gus-guy` → `gus`, …) so the scanner now sees its slug in
`seen` and skips. Restart-stable: 18 voices, no dupes. Job references and
`.prompt.txt` descriptions intact.

**Proper fix (not done):** the scanner should match an existing voice by
something stable (ref-audio content/path or name) rather than assuming its
own slug format is the only one — or `voice_add` should adopt the scanner's
stem-based slug. Any UI-cloned voice whose stem ≠ persona name will hit this
on the Mac too.

---

## DONE — Linux rollout of 0.5.1, 2026-08-23

Both Linux clones are now at `eeea4e4` (v0.5.1 — the `job_delete` disk-sweep
release). Nothing outstanding on this box.

- **Dev clone**: `uv sync --extra cuda` → `uv run pytest` → **180 passed,
  5 skipped** (the new baseline; `f3dfcbf` added the job-delete route tests).
- **Production clone** (`/8TB/bin/ruru`): `0346859` → `git pull` → `eeea4e4`.
  `uv sync --extra cuda` reinstalled only `ruru` (0.5.0 → 0.5.1); `torch
  2.13.0+cu130` / CUDA verified intact afterwards. Suite → **180/5**, matching
  dev.
- **Real generation quirk**: the first clone-side `generate_clone` (service
  stopped, pre-reboot) produced a valid but runaway 655 s MP3 — the model never
  emitted EOS. `engine/cuda_engine.py` is unchanged since the verified-good
  July run, so a sampling/state fluke, not a code regression. After the user
  rebooted the box, an end-to-end narrate job through nginx produced a normal
  23.2 s MP3 for a 3-paragraph doc. If runaways recur, look at a max-length
  cap in the CUDA engine.
- **Service**: rebooted mid-rollout (deliberate, to clear GPU/memory state);
  `ruru-web` auto-started on the new code — new PID, port 5151, journal clean.
  Site → 200 through nginx, sidebar **CUDA · v0.5.1**, 13 voices.
- **The actual point of the release, verified live**: `POST /api/narrate`
  (field `document`) → completed → `DELETE /api/jobs/{id}` removed the DB row,
  `cache/output/narrate_<id>.mp3`, `cache/chunks/job_<id>/`, **and** the
  uploaded source doc. Swept the pre-fix orphans by hand afterwards (chunk
  dirs `job_4/5/6/10`, six stale uploads) — `chunks/` and `uploads/` are now
  empty.
- One noted edge case (harmless, not fixed): two jobs uploading the *same
  filename* share one `uploads/<name>` path; deleting either job unlinks the
  file while the other job's row still references it.

---

## DONE — the Linux catch-up landed, 2026-07-26 (evening)

Both Linux clones are now at `0346859`. Nothing outstanding on this box; the
section below is kept as the record of what was run.

- **Dev clone** (`~/Documents/current_projects/ai_speech/ruru`): already at
  `0346859` via Syncthing. `uv sync --extra cuda` → `uv run pytest` →
  **176 passed, 5 skipped**.
- **Production clone** (`/8TB/bin/ruru`): `213f5af` → `git pull` → `0346859`.
  `uv sync --extra cuda` reinstalled only `ruru` itself (0.0.3 → 0.5.0) and
  left `torch` / `qwen_tts` in place — verified explicitly afterwards
  (`torch 2.13.0+cu130`, `cuda True`, `qwen_tts` imports). `uv run pytest` →
  **176 passed, 5 skipped** — the new Linux baseline, replacing 167/4. No
  `test_voice_seed.py` failures: this clone carries no local
  `characters_data.py`, exactly as predicted.
- **Real (non-mocked) CUDA generation** from the production clone, with the
  service stopped so the 1.7B model had the VRAM: `generate_clone` off
  `voice_clones/lilith.mp3` → a valid 3.52 s MP3 (24 kHz mono, 128 kbps).
- **Restart**: `systemctl stop` → verify → `systemctl start` (plain, not
  `reset_service.sh restart-only` — the service was already down and there was
  no reason to invoke the state-clearing path). New PID 263797, active.
- **Verified after restart**: `http://ruru.nexus.home.test/` → 200; `/api/voices`
  → still **13 voices**; sidebar reads **CUDA** and **v0.5.0**; no errors in the
  journal on the first generation.
- **Full narrate job end to end through nginx**: a 3-paragraph document →
  1 chunk → a 17.04 s MP3 normalized to max −3.43 dBFS, downloadable through
  the `/audio/output/` alias (273,836 bytes, `audio/mpeg`).

Two small things worth knowing next time:

- `POST /api/narrate`'s file field is **`document`**, not `file` (`app.py:733`).
  Posting `file=@…` returns a 400 `No document uploaded.`
- The `jobs` table has **`error_message`**, not `error`. A polling loop selecting
  `error` fails with `no such column` on every iteration while the job quietly
  succeeds.

The GPU had 5.5 GB in use before this: 4.5 GB held by the running `ruru`
service (a model left resident from an earlier job — freed by the restart) and
784 MB by an unrelated container.

Still not done: §6 CI (optional, never attempted).

---

## The original Linux catch-up instructions — completed, kept for reference

The Mac is done and `main` was at `596f0a0`. Linux had **not** pulled this yet:
`http://ruru.nexus.home.test/` was serving **v0.5.0 on the CUDA
backend**, healthy, from whatever commit it installed at (`213f5af` or
earlier). Nothing was broken — a routine catch-up, with **one hazard**.

### ⚠️ The one thing that will break the service

**`uv sync` on its own no longer installs a backend.** After the extras change
in `a8ea1cf`, `torch` and `qwen-tts` live *only* behind `--extra cuda`. A plain
`uv sync` in the production clone will happily succeed while **uninstalling
torch and qwen-tts**, and `ruru-web.service` will then fail on the first
generation with an import error.

Always: `uv sync --extra cuda`

Never pass both extras (`--extra mlx --extra cuda`) — uv will refuse, and that
refusal is the whole point of the fix. On Linux the `mlx` extra resolves to
nothing anyway (its two deps carry `sys_platform == 'darwin'` markers), so
there is no risk of pulling MLX onto the box.

`install_linux.sh` already passes `--extra cuda`, so re-running the installer
is safe and is the lowest-risk path if you'd rather not do it by hand.

### Steps

Two clones to update. The **dev** clone (Syncthing-synced) and the
**production** clone at `/8TB/bin/ruru`. Do dev first.

```bash
# 1. Dev clone (Syncthing-synced — dev only, never a service dir)
cd <ruru dev clone>
git pull                      # -> 596f0a0
uv sync --extra cuda          # NOT plain `uv sync`
uv run pytest
```

```bash
# 2. Production clone — only after dev is green
cd /8TB/bin/ruru
git status --short            # expect clean
git pull                      # -> 596f0a0
uv sync --extra cuda
uv run pytest
```

Then a real generation **before** restarting anything — the suite mocks the
engine, so green proves nothing about CUDA actually working:

```bash
# ComfyUI must be stopped — it holds ~8 GB and a 1.7B model needs ~4-5 GB
uv run python -c "
from engine import get_engine_class
e = get_engine_class('cuda')()
e.generate_clone('Testing one two three',
                 'voice_clones/lilith.mp3',
                 'voice_clones/lilith.txt').write('/tmp/cuda_check.mp3')"
file /tmp/cuda_check.mp3      # must say 'MPEG ... Audio'
```

Restart last:

```bash
./reset_service.sh restart-only     # or: sudo systemctl restart ruru-web
```

### What to expect

- **`uv.lock` regenerates.** It is gitignored, so this clone resolves its own.
  The CUDA side still pins `transformers==4.57.3` via `qwen-tts` — unchanged,
  and now isolated from the Mac's resolution rather than dictating it.
- **Test counts will differ from the old 167 passed / 4 skipped baseline** —
  tests have been added since (the Mac now sees 175/6). The new
  `TestMLXHasQwen3TTSArchitecture` **skips** on Linux (`mlx_audio` not
  installed), which is correct. Record whatever number you actually get as the
  new baseline rather than matching it to the Mac's.
- **Two `test_voice_seed.py` failures are possible but not expected here.**
  They appear only in a checkout carrying a local, gitignored
  `characters_data.py` — the Mac production clone has one, Linux probably
  doesn't. See the entry under "Other open items" for the root cause; it is a
  test-isolation bug, not a code regression, and does not block the restart.

### Verify

- `systemctl status ruru-web` → active
- `curl -s -o /dev/null -w '%{http_code}' http://ruru.nexus.home.test/` → 200
- `curl -s http://ruru.nexus.home.test/api/voices` → still **13 voices**
- Sidebar still reads **CUDA** and the version
- One real narrate job end to end through nginx
- `journalctl -u ruru-web -f` → no import errors on the first generation

### If it goes wrong

Last known-good for the Linux production clone is **`213f5af`** (what it is
running now):

```bash
cd /8TB/bin/ruru && git reset --hard 213f5af && uv sync --extra cuda
sudo systemctl restart ruru-web
```

The Mac is independent and already verified at `596f0a0`; nothing you do on
Linux affects it.

---

## STATUS as of 2026-07-26 — read this first

Merged to `main`. Phases 0 through 3 are **done and running in
production on Linux**. `http://ruru.nexus.home.test/` serves ruru on the CUDA
backend from `/8TB/bin/ruru`, via `ruru-web.service` behind nginx.

---

## What's left — pick up here on the Mac

This work is **already merged to `main`** at Jeremy's request, so he could pull
it on the MacBook in a fresh session. That inverts the usual rule in Phase 4
("merge only after the Mac is green"), which means:

> ⚠️ **`main` currently contains code that has never run on Apple Silicon.**
> The Mac's launchd production service is still running whatever it had before.
> Verify *before* you restart that agent, not after.

### 1. Sync and install

```bash
cd <ruru repo on the Mac>
git checkout main && git pull
uv sync --extra mlx        # NOT plain `uv sync` — that installs no backend.
                           # Never `--extra cuda` on the Mac; it would try to
                           # install torch + qwen-tts.
```

**Update, 2026-07-26 — the plain-`uv sync` guidance above was itself a live
bug.** With `mlx`/`mlx-audio` as unconditional (if platform-marked) base
deps and `qwen-tts` pinning `transformers==4.57.3` under the `cuda` extra,
`uv`'s single universal lock forced that pin onto the Mac's resolution too,
silently backtracking `mlx-audio` to **0.2.10** — the last release before
`mlx_audio/tts/models/qwen3_tts/` existed. A plain `uv sync` on the Mac
dev checkout reproduced this: real `generate_clone` died with
`TypeError: ModelConfig.__init__() missing 11 required positional
arguments`, because without the `qwen3_tts` architecture module,
`mlx_audio.tts.utils.get_model_and_args` resolves `model_type: qwen3_tts`
down to the unrelated `qwen3` (Orpheus/SNAC) architecture. The mocked test
suite stayed green throughout (174 passed, 6 skipped) — it mocks
`load_model` and cannot catch this.

**Fixed**: `mlx`/`mlx-audio` moved into their own `mlx` extra (floor
`mlx-audio>=0.4.4`), and `[tool.uv] conflicts = [[{extra="mlx"},
{extra="cuda"}]]` added so `uv lock` forks the resolution instead of
intersecting it. Verified: `mlx-audio` now resolves to 0.4.x with
`qwen3_tts` present, and `transformers` forks to 5.12.1 (MLX) / 4.57.3
(CUDA) independently. Install commands and CLAUDE.md/README.md updated to
`uv sync --extra mlx` / `--extra cuda` — plain `uv sync` installs neither
backend now.

### 2. Run the suite

```bash
uv run pytest
```

Expect **all Linux-green tests to pass, plus more**: the Mac has `mlx_audio`,
so the two `tests/test_tts_engine.py` generation tests and the MLX branch of
`tests/test_engine_contract.py` will *run* there instead of skipping. Linux
sees 167 passed / 4 skipped; the Mac should see roughly 170 passed / 1 skipped
(the pre-existing `test_routes_voices` skip).

If something fails, the likely culprits, in order:
- `engine/tts.py` is now a **re-export shim**; anything still doing
  `from engine.tts import TTSEngine` gets `MLXEngine` and should be fine, but
  `patch("engine.tts.TTSEngine")` in any *untouched* test would now be patching
  the wrong target — the factory is what handlers call.
- `engine/result.py`'s `TTSResult` gained an optional `mp3_bytes`. MLX still
  passes real mp3 bytes, so `.write("x.mp3")` should remain a byte copy with no
  re-encode. Confirm you're not silently round-tripping through pydub.

### 3. Real MLX narrate job

Run one through the UI — not just the suite. This is the only thing that proves
the refactor didn't change MLX behaviour, since every test mocks the model.
Check the output MP3 actually plays and is the right length.

### 4. Restart the launchd agent — last

```bash
./reset_service.sh restart-only
```

**Pay attention to this one.** `reset_service.sh` was deleted from the repo
months ago and has just been recreated; the Mac has never run this version. Its
`launchctl` path carries a bug fix that matters (commit `44de990`): the old
`launchctl list | grep -q` idiom silently failed under `set -o pipefail`, which
would have made every restart skip unloading the agent and leave the old
process holding port 5151 — the exact "Port conflict" pitfall in CLAUDE.md.
Confirm the agent really does stop and restart, and that the site comes back.

Kill any manually started `uv run ruru` first.

### 5. Check whether MLX has the same offline bug — likely, untested

On Linux, `qwen_tts` wraps `AutoModel.from_pretrained(repo_id)`, which contacts
huggingface.co on **every** model load. A network outage therefore took TTS down
even though all ~11 GB of weights were cached on disk. Fixed for CUDA in commit
`c22cb74` by resolving the repo id to its local snapshot directory first — a
filesystem path never touches the network — with a fallback to the bare repo id
so first-time downloads still work. See `CUDAEngine._resolve_model_path`.

**The MLX engine very likely has the same problem and has not been tested.**
`MLXEngine._load_clone`/`_load_design` call
`mlx_audio.tts.utils.load_model(CLONE_MODEL_ID)` with a bare
`mlx-community/...` repo id (`engine/mlx_engine.py`), which is the same shape of
call. It was left alone deliberately: it can't be tested off Apple Silicon, and
blind-patching the Mac's production load path is exactly the kind of change this
whole branch structure exists to prevent.

To check it on the Mac:

```bash
HF_HUB_OFFLINE=1 uv run python -c "
from engine import get_engine_class
e = get_engine_class('mlx')()
e._load_clone()
print('offline load OK')"
```

- **If it raises** (expect `OfflineModeIsEnabled`), mirror the CUDA fix: add the
  same `_resolve_model_path` helper — ideally lifted to `engine/base.py` so both
  backends share one implementation — and pass the resolved path to
  `load_model`. Then re-run the command above to confirm, and add a test beside
  `TestCUDAOfflineModelResolution` in `tests/test_engine_contract.py`.
- **If it loads fine**, mlx-audio already falls back to cache. Note that here so
  nobody re-investigates.

**Answered, 2026-07-26 — MLX does NOT have the offline bug. No fix needed.**
On the dev checkout, post `mlx-audio` 0.4.6 fix:
```
HF_HUB_OFFLINE=1 uv run python -c "
from engine import get_engine_class
e = get_engine_class('mlx')()
e._load_clone()
print('offline load OK')"
```
→ `offline load OK`, no exception, no network contacted. `mlx_audio`'s model
loading already resolves fully from the local HF cache under
`HF_HUB_OFFLINE=1`. Unlike CUDA's `qwen_tts` (which wraps a bare
`AutoModel.from_pretrained(repo_id)` that always tries the network first),
MLX's path doesn't need `CUDAEngine._resolve_model_path` mirrored — this is
asymmetric between the two backends and that's fine. Nothing to change here.

Either way this is worth doing: ruru is a local-first app, and "no internet"
should never mean "no speech".

### 6. Optional, cheap, worth it

A GitHub Actions job running `uv sync --extra cuda && uv run pytest` on
`ubuntu-latest`. It won't catch MLX runtime regressions, but it catches every
"this only imports on a Mac" mistake at push time — which is exactly the class
of bug this branch was built to prevent. (Plain `uv sync` would be faster —
no torch download — but since `a8ea1cf` it installs no backend, so the
CUDA-gated tests would all skip and the run would prove much less.)

### If the Mac goes wrong

`main` is ~10 commits ahead of where it was. Last known-good commit before this
work: **`60be0c8`**. The Linux production install at `/8TB/bin/ruru` is
independent — it tracks its own checkout and won't be affected by anything you
do to the Mac or to `main`.

### Also landed after the merge — the Mac will pull these too

Three fixes and a tool, all found while running the Linux install. None are
CUDA-specific, so they affect the Mac as well:

- **`c22cb74`** — cached weights load without contacting huggingface.co
  (CUDA only so far; see step 5 above).
- **`ad10040`** — **`reset_service.sh` was deleting the voice library.**
  `clear_state()` removed `*_ref.wav` and `*_sample.wav`, but `app.py:424`
  saves uploaded clone reference audio as `{slug}_ref{suffix}` — so every
  `restart-only` destroyed uploaded voices and left DB rows pointing at missing
  files. Only `{slug}_preview.*` (`app.py:567`) is disposable. **If the Mac's
  voice library has been losing entries, this is why.**
- **`a686573`** — `reset_service.sh` checks for sudo before doing anything
  destructive (Linux path only; harmless on macOS).
- **`models/import_voices.py`** — migrates a voice library between installs,
  rewriting the per-machine paths and writing transcripts to disk. Used to move
  the 10 Ephergent characters + Ray Porter + Neil Hellegers onto the Linux box;
  the same command works in reverse if the Mac needs them.

### Linux voice library, as of 2026-07-26

13 voices live at `http://ruru.nexus.home.test/`: the 10 Ephergent characters
(`ephergent`) plus Lilith, Ray_Porter, and Neil_Hellegers (`cloned`). Every
`ref_audio_path` and `ref_text` resolves to a local file. Generation confirmed
with Meatball and Clive Stapler. A pre-import DB backup sits at
`data/ruru.db.bak-preimport`.

Note the transcripts: several Ephergent `ref_text` values existed **only** in
the old dev database, not on disk. `import_voices.py` writes them out as
`{slug}_ref.txt`, so they now survive a database reset.

---

### Production install, verified 2026-07-26

- `ruru-web.service` **active and enabled** (starts on boot), running
  `/8TB/bin/ruru/.venv/bin/ruru` as `jeremy` on port 5151.
- `http://ruru.nexus.home.test/` returns the ruru Dashboard; `/voices`,
  `/narrate`, `/static/`, and `/api/voices` all 200. The Lilith voice
  auto-seeded from `voice_clones/`.
- **Full narrate job through nginx**: 331-word document → 4 chunks → a 101.60 s
  MP3, normalized to max −3.4 dBFS, downloadable through the
  `/audio/output/` alias (1.6 MB, `audio/mpeg`). Zero errors in the journal.
- `./reset_service.sh restart-only` stops, clears state, and restarts cleanly;
  the seeded voice survives.
- **sprecher fully retired**: unit file deleted, no sprecher units remain, both
  nginx configs gone. Its ~35,000-restart crash loop is over. Repo files on
  disk untouched.
- The production clone is a **separate checkout** at `/8TB/bin/ruru` with its
  own Python 3.13.2 venv — not the Syncthing-synced dev tree. Its test suite
  passes there too (167 passed, 4 skipped).

### Two bugs found and fixed during the install

1. **`pipefail` + `grep -q`** (commit `44de990`). `systemctl list-unit-files |
   grep -q ...` returns 141, not 0, on a match: grep exits at the first hit,
   the producer dies of SIGPIPE, and `pipefail` propagates that. The installer
   therefore reported "no sprecher-web.service — nothing to do" while the unit
   was live and crash-looping. The same pattern was in both of
   `reset_service.sh`'s `stop_service` branches — the `launchctl` one would
   have made **every macOS restart silently skip unloading the agent**, leaving
   the old process holding port 5151. Capture to a variable, then grep it.
2. **`reset_service.sh` checked for sudo too late** (commit `a686573`). It
   stopped the service and cleared state before discovering it couldn't
   authenticate to start it again, leaving nginx serving 502s. Now verified up
   front, before anything destructive.

Evidence already collected on Linux, with **ComfyUI stopped** and the **1.7B**
models (`RURU_MODEL_SIZE=large`), i.e. the real production configuration:

- `uv run pytest` → **167 passed, 4 skipped**, stable across repeat runs. The
  skips are the two MLX-only generation tests, the MLX branch of the contract
  suite, and one pre-existing `test_routes_voices` skip.
- **Real 1.7B CUDA inference**, not mocks. Clone (`Qwen3-TTS-12Hz-1.7B-Base`)
  off `voice_clones/lilith.mp3`: 6.08 s of speech in 13.3 s. Design
  (`-1.7B-VoiceDesign`): 4.08 s of speech in 8.5 s. Both wrote valid audio —
  MP3 via the on-demand encode path, WAV via soundfile. No OOM warnings.
- **The clone-prompt cache works**: first chunk 13.3 s (model load + prompt
  build), second 3.4 s reusing the cached prompt — a ~4x saving per chunk.
- **The single-model eviction policy works**: switching clone → design logged
  the eviction, and afterwards `_clone_model is None` / `_design_model` resident.
  VRAM went 9.90 GB free → 5.75 GB (clone) → 6.13 GB (design) → 9.90 GB after
  `unload()`. One 1.7B model costs ~4-5 GB resident, so the pair would not fit
  alongside ComfyUI — which is exactly why the policy exists.
- **Full end-to-end narrate job through the running app** (`RURU_TTS_BACKEND=cuda`,
  a 331-word document): parsed → 4 chunks → each cloned → assembled to a
  102.64 s MP3, normalized to max -3.4 dBFS. Zero retries, zero errors. The
  prompt was built once and reused for the other 3 chunks.
- The app boots on the CUDA backend, serves `/`, `/voices`, `/api/voices`, and
  auto-scans `voice_clones/lilith.{mp3,txt}` into the library on startup.

**Known cost, not a regression:** each job constructs a fresh engine
(`get_engine_class()()` per handler, as `TTSEngine()` always did), so the model
reloads per job — about 15-20 s of startup before the first chunk. Pre-existing
MLX behaviour; worth revisiting separately if narration throughput matters.

`install_linux.sh` has been run twice against `/8TB/bin/ruru` and is confirmed
idempotent: the second run retired sprecher and left the healthy ruru service
undisturbed.

---

## Pre-flight — verified 2026-07-26 on the Linux box. Do not re-run.

| Check | Result |
|---|---|
| `torch.cuda.is_available()` | `True` — torch 2.13.0+cu130, "NVIDIA GeForce RTX 3060" |
| `qwen_tts` | 0.1.1 installed; `Qwen3TTSModel.from_pretrained` / `create_voice_clone_prompt` / `generate_voice_clone` / `generate_voice_design` all present with the signatures WeirDing expects |
| Weights cached | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` (4.3 G), `…-1.7B-VoiceDesign` (4.3 G), `…-0.6B-Base` (2.4 G) under `~/.cache/huggingface/hub` |
| VRAM | 12288 MiB total. ~10.6 GB free with ComfyUI stopped; ComfyUI running holds ~8.2 GB and leaves too little for a 1.7B model |
| Disk | `/8TB` — 1.1 T free |
| `uv` | 0.9.17 |

`flash-attn` is not installed; `qwen_tts` warns and falls back to the manual
PyTorch path. That is fine — slower, not broken.

## Decisions already made. Do not re-litigate.

- **Linux prod install** is a fresh `git clone git@github.com:BitsofJeremy/ruru.git
  /8TB/bin/ruru` with its own `.venv` and its own `RURU_WORK_DIR`.
- **sprecher is fully removed** — its `sprecher-web.service` is crash-looping
  every 5 s (`203/EXEC`: the checked-in `.venv` is a macOS venv, dangling on
  Linux). Disable + delete the unit and both nginx configs. Leave the sprecher
  repo files on disk alone.
- **Address**: `ruru.nexus.home.test` → `127.0.0.1:5151`.
- **VRAM policy**: `CUDAEngine` keeps **one model resident at a time** and always
  uses the **1.7B** pair. It fails loudly when VRAM is short rather than silently
  degrading. Stop ComfyUI before narrating.

## Corrections — the docs lie about these

- `reset_service.sh` had been deleted in `62ec10e` while CLAUDE.md and the old
  TODO still told you to run it. **Recreated** in Phase 3, now cross-platform
  (systemd on Linux, launchd on macOS).
- `archive/install_linux.sh` **does not exist** on disk (purged in `99c9817`).
  Recover it for reference with `git show 99c9817^:archive/install_linux.sh`.
- `characters_data.py` and `ephergent_voices/` no longer exist. Voice seeding is
  now a graceful no-op (`models/seed_voices.py:22-28`); voices come from tracked
  `voice_clones/lilith.{mp3,txt}` via `models/scan_voice_clones.py`.
- `models/db.py` is **SQLAlchemy ORM** now (`set_db_path`, `get_sync_session`,
  `Job`), not the raw SQL that CLAUDE.md describes.
- `docs/nvidia_feasibility.md` §3's `reference_material/` and
  `archive/private_removed_20260725/` paths do not exist. The live CUDA
  references are `../WeirDing/engine/tts.py` (sync, the direct ancestor) and
  `../sprecher/core/tts/qwen_engine.py` (the async ABC variant).
- `docs/nvidia_feasibility.md` §1/§5's `jobs/tasks.py` line numbers are stale.
  Actual engine call sites: `jobs/tasks.py:61-63`, `:115-120`, `:215-217`.

---

### Phase 0 — Make the repo installable on Linux — ✅ DONE (commit `60be0c8`)

`pyproject.toml` already carries the platform markers and the CUDA extra:

```toml
"mlx ; sys_platform == 'darwin'",
"mlx-audio ; sys_platform == 'darwin'",

[project.optional-dependencies]
cuda = ["torch>=2.0.0", "qwen-tts"]
```

`uv.lock` stays gitignored — each machine resolves its own. Do not start
committing it.

This works because every MLX import in `engine/tts.py` is lazy and
method-scoped (`_load_clone` :130-137, `_load_design` :139-146, `_run_generate`
:188-208). Module level needs only `numpy` + `soundfile`, so the whole suite
imports on a machine with no MLX.

### Phase 0.5 — Green pytest baseline on Linux — ✅ DONE

`uv run pytest` on the Linux box is **3 failed, 116 passed, 1 skipped** today.
Fix this first, so later failures mean something.

- [x] `tests/test_tts_engine.py` patches `mlx_audio.tts.utils.load_model` and
      `mlx_audio.tts.generate.generate_audio` (lines 56-57, 73-74), which don't
      import off Darwin. Add a module-level
      `pytest.importorskip("mlx_audio")`. These become MLX-only tests; the new
      contract suite in Phase 1 covers the shared surface on both platforms.
- [x] `tests/test_job_queue.py::TestJobRunner::test_enqueue_returns_future`
      passes alone but fails in a full run — the real `run_generate_tts` runs
      and hits `RuntimeError: Database path not set`. Make it enqueue a stub
      handler (or `set_db_path` to a tmp DB) instead of depending on test order.
- [x] Verify on Linux: `uv run pytest` fully green.
- [ ] Verify on the **Mac**: `uv run pytest` — confirms the `find_spec` gating
      didn't skip anything that should still run there. See "What's left".

### Phase 1 — Backend-agnostic refactor — ✅ DONE (Linux; Mac verification pending)

MLX stays the only backend. Pure interface extraction, no behaviour change. Use
TDD (`superpowers:test-driven-development`). Merge to `main` before Phase 2.

- [x] `engine/result.py` — move `TTSResult` (`engine/tts.py:48-94`) out of the
      MLX module so `CUDAEngine` can import it without importing MLX code. One
      addition: make `mp3_bytes` **optional** — `.write()` to a `.mp3` path with
      empty `mp3_bytes` should encode from `self.wav`/`self.sr` via pydub
      (already a dep, ffmpeg present) instead of raising. MLX supplies mp3 bytes
      natively and keeps its zero-recode path; CUDA yields raw ndarrays and
      shouldn't be forced through a pointless encode.
      Must keep **both** existing usages: tuple unpacking
      (`__iter__`/`__len__`/`__getitem__`) and attribute access —
      `jobs/tasks.py:167` does `len(candidate.wav) / candidate.sr`.
- [x] `engine/base.py` — `TTSEngineBase(ABC)`, **synchronous** (doc §4.1).
      Abstract: `load(size="auto")`, `unload()`, `is_loaded` (property),
      `generate_clone(text, ref_audio_path, ref_text, language="English") -> TTSResult`,
      `generate_design(text, voice_description, language="English") -> TTSResult`.
      Concrete on the base, since both backends are identical here — lift
      `generate_to_file` (`engine/tts.py:298-326`) and the `_resolve_ref_text`
      static helper (`:163-173`) as-is.
      Do **not** add `list_voices`/`validate_voice` (doc §4.2). Keep
      `create_clone_prompt` **off** the ABC — MLX's is a no-op shim, CUDA's is a
      real optimization.
- [x] `engine/mlx_engine.py` — today's `TTSEngine` (`engine/tts.py:97`) moved and
      renamed `MLXEngine(TTSEngineBase)`. Leave `engine/tts.py` as a thin
      re-export shim (`from engine.mlx_engine import MLXEngine as TTSEngine`) so
      the move commit doesn't also churn every `patch("engine.tts.TTSEngine")`.
- [x] `config.py` — `TTS_BACKEND = _env("TTS_BACKEND", "mlx")`, next to
      `MODEL_SIZE` (`config.py:30`).
- [x] `engine/__init__.py` (empty today) — `get_engine_class(backend=None)`
      factory, the single place that knows both class names exist (doc §5).
      Defaults to `config.TTS_BACKEND`. Import the concrete class **lazily inside
      the function** so a Mac never imports `torch` and Linux never imports
      `mlx`. Unknown value → `ValueError` naming the valid options.
- [x] `jobs/tasks.py` — replace the three
      `from engine.tts import TTSEngine; engine = TTSEngine()` pairs (`:61-63`,
      `:115-120`, `:215-217`) with
      `from engine import get_engine_class; engine = get_engine_class()()`.
- [x] Repoint the tests — `tests/conftest.py:47`'s `mock_tts_engine` and the
      seven `@patch("engine.tts.TTSEngine")` decorators in
      `tests/test_job_tasks.py` (lines 94, 121, 147, 185, 210, 237, 267) → patch
      `engine.get_engine_class`. While there: `mock_tts_engine` returns a raw
      `(MOCK_WAV, 24000)` tuple, which has no `.wav`/`.sr`/`.write` and so can't
      actually drive `jobs/tasks.py` — which is why `test_job_tasks.py` ignores
      the fixture and rolls its own `_fake_audio()`. Make the fixture return a
      real `TTSResult` and delete the duplicate helper.
- [x] `tests/test_engine_contract.py` (new — **the important one**) —
      parametrized suite asserting a backend class satisfies the ABC surface
      from doc §2: method presence, signature compatibility, and that
      `generate_clone`/`generate_design` return something both tuple-unpackable
      **and** `.write()`-able with `.wav`/`.sr`. Parametrize over available
      backends, skipping any whose deps aren't installed. **This is what makes
      Linux development safe** — it proves a CUDA engine written on Linux drops
      into the Mac's call sites.
- [x] `tests/test_engine_factory.py` (new) — `RURU_TTS_BACKEND` unset →
      `MLXEngine`; `=cuda` → `CUDAEngine`; unknown value → clear error.
- [x] Update `CLAUDE.md` (file layout + config table, plus the stale facts in
      "Corrections" above) and mark `docs/nvidia_feasibility.md` §5 implemented.
- [ ] **Not yet done** — verify on the Mac: `uv run pytest`, a real end-to-end
      narrate job through the UI, then restart the launchd agent. (Kill any
      manually started `uv run ruru` first — known port conflict.) See
      "What's left".

### Phase 2 — `CUDAEngine` on the Linux clone — ✅ DONE

```bash
uv sync --extra cuda
uv run pytest            # must be green before writing anything
```

- [x] `engine/cuda_engine.py` — `CUDAEngine(TTSEngineBase)`, ported from
      `../WeirDing/engine/tts.py` (`load` :43, `unload` :100, `is_loaded` :114,
      `generate_clone` :121, `generate_design` :223, `generate_to_file` :257).
      Required changes against that source:
      - Return `TTSResult` from `engine/result.py`, not raw `(wav, sr)` tuples
        (doc §2). WeirDing returns `wavs[0], sr` — wrap it. **This is the one
        real porting hazard.**
      - Keep `torch` / `qwen_tts` imports lazy and method-scoped (WeirDing
        already does this) so importing the module on the Mac is harmless.
      - Keep the `torch.cuda.is_available()` hard gate in `load()`.
      - **Single-model VRAM policy.** `_load_clone()` evicts `_design_model` and
        vice versa (`del`, `gc.collect()`, `torch.cuda.empty_cache()`) before
        loading. Always the 1.7B pair; `size="small"` is honoured only when
        explicitly passed, `auto` means large. Do **not** copy WeirDing's
        total-VRAM auto-detect — on this box it reads 12 GB and ignores that
        ComfyUI holds 9 of them. Log free VRAM at load and let an OOM surface as
        a real error.
      - **Cache the clone prompt.** `jobs/tasks.py:161` calls `generate_clone`
        once per chunk, and building the voice-clone prompt is the expensive
        per-call GPU op that WeirDing explicitly hoists out
        (`create_clone_prompt` :166, `generate_clone_with_prompt` :193) and that
        sprecher wastefully rebuilds every call. Cache it inside `CUDAEngine`
        keyed by `(ref_audio_path, mtime, ref_text)` so `generate_clone` reuses
        it transparently — no ABC change, no caller change.
      - `generate_voice_design` takes `instruct=`, not `voice_description=`.
- [x] Verify on Linux: contract suite green for `CUDAEngine`; then stop ComfyUI,
      `RURU_TTS_BACKEND=cuda uv run ruru`, and run a real narrate job end to end.

### Phase 3 — Linux production install at `/8TB/bin/ruru` — ✅ DONE

New `install_linux.sh` at the repo root. Model it on `install_macos.sh` — the
more complete script; it does `uv sync`, DB init, and derives every path from
`BASH_SOURCE` rather than `$HOME`, which matters under sudo — with the
systemd/nginx bits from `git show 99c9817^:archive/install_linux.sh`. Idempotent,
run as `sudo bash install_linux.sh`.

- [x] **Decommission sprecher**, guarded so it skips cleanly when already gone:
      `systemctl disable --now sprecher-web`,
      `rm /etc/systemd/system/sprecher-web.service`, `systemctl daemon-reload`,
      `rm /etc/nginx/sites-{enabled,available}/sprecher-web`.
- [x] **Paths** — `APP_DIR` from `BASH_SOURCE` (`/8TB/bin/ruru`),
      `WORK_DIR=$APP_DIR/data`; mkdir `data/cache/{uploads,chunks,output,voices}`
      and `data/logs`. Note the **`cache/` level** — the old WeirDing script used
      a flat `data/{uploads,output,…}` layout that current code does not expect.
- [x] **Deps + DB** as the real user (`SUDO_USER`, via an `as_user()` helper like
      install_macos.sh's): `uv sync --extra cuda`, then DB init via `$VENV_PY`.
- [x] **systemd unit** `/etc/systemd/system/ruru-web.service`: `User=jeremy`,
      `WorkingDirectory=/8TB/bin/ruru`,
      `ExecStart=/8TB/bin/ruru/.venv/bin/ruru`, `Restart=on-failure`,
      `RestartSec=5`, journal logging, `WantedBy=multi-user.target`, and
      `Environment=` for `RURU_WORK_DIR=/8TB/bin/ruru/data`,
      `RURU_TTS_BACKEND=cuda`, `RURU_PORT=5151`, `RURU_MODEL_SIZE=large`, and
      `HF_HOME=/home/jeremy/.cache/huggingface` so the service reuses the ~11 GB
      of already-downloaded weights instead of re-fetching them.
      Then `systemctl daemon-reload && systemctl enable --now ruru-web`.
- [x] **nginx** `/etc/nginx/sites-available/ruru` + `sites-enabled` symlink:
      `server_name ruru.nexus.home.test`, `client_max_body_size 0`,
      `proxy_read_timeout 86400s`, HX-* and Upgrade/Connection headers,
      `proxy_pass http://127.0.0.1:5151`, plus direct `alias` blocks for
      `/static/`, `/audio/output/` → `data/cache/output/`, and `/audio/voices/`
      → `data/cache/voices/`. Run `nginx -t` before reloading.
- [x] **`reset_service.sh`** — recreate it, systemd-flavoured. Recover the
      deleted macOS version for structure (`git show 62ec10e^:reset_service.sh`)
      and swap `launchctl` for `systemctl`. Commands: `restart-only`,
      `clean-only`, `reset-db`, `clean-all`, `help`. Then fix the dangling
      references to it in `CLAUDE.md`.
- [x] **Docs** — add a Linux production section to `CLAUDE.md`, and Linux
      teardown steps to `docs/uninstall.md`.
- [x] Verify: `systemctl status ruru-web` active; `http://ruru.nexus.home.test/`
      loads; the Lilith voice appears in the library (auto-scanned from tracked
      `voice_clones/lilith.{mp3,txt}`); a real narrate job completes and the MP3
      downloads; `systemctl restart ruru-web` recovers cleanly.

### Phase 4 — Cross-machine verification loop

Standing rules for every round trip:

1. Push `cuda-backend` from Linux.
2. On the Mac: `git fetch && git checkout cuda-backend && uv sync --extra mlx`
   (**never** `--extra cuda` on the Mac, and never plain `uv sync` — since
   `a8ea1cf` that installs no backend at all) → `uv run pytest` → a real MLX
   narrate smoke test.
3. Merge to `main` only after the Mac is green. Restart the launchd agent last.
4. The Linux box never installs `mlx`; the Phase 0 markers enforce it.

- [ ] Optional, cheap, worth it: a GitHub Actions job running `uv sync --extra
      cuda && uv run pytest` on `ubuntu-latest`. It won't catch MLX runtime
      regressions, but it catches every "this only imports on a Mac" mistake at
      push time, so the Mac pull becomes a formality rather than the first
      signal.

### Verification summary

| Where | Command | Proves |
|---|---|---|
| Linux, Phase 0.5 | `uv run pytest` | green baseline off Darwin |
| Mac, Phase 0.5 | `uv run pytest` | the test gating didn't skip anything real on the Mac |
| Both, Phase 1 | `uv run pytest` (incl. contract + factory suites) | refactor is behaviour-neutral |
| Linux, Phase 2 | `uv run pytest` contract suite vs `CUDAEngine` | CUDA engine satisfies the shared contract |
| Linux, Phase 2 | ComfyUI stopped → `RURU_TTS_BACKEND=cuda uv run ruru` + narrate job | CUDA inference actually works |
| Linux, Phase 3 | `systemctl status ruru-web` + narrate job via `ruru.nexus.home.test` | the production install works |
| Mac, Phase 4 | `uv run pytest` + narrate job on `cuda-backend` | Linux work didn't break MLX |

---

## Other open items

- No CSRF protection (acceptable for a single-user local app).
- Voice edit page's image upload/remove controls call `/api/voices/{id}/image`,
  which doesn't exist yet (404).
- `.venv` directories are checked into the Syncthing-synced tree, so a macOS
  venv lands on Linux and vice versa. This is what broke `sprecher-web.service`
  (`203/EXEC`). Make sure `.venv/` is gitignored *and* excluded from Syncthing.
- `tests/test_voice_seed.py::TestSeedVoices::test_seed_is_noop_without_characters_data`
  and `::test_seed_idempotent_when_noop` assume `characters_data.py` is absent
  (the public-repo case). They fail in **any checkout that has a real, local,
  gitignored `characters_data.py`** — found 2026-07-26 in the Mac production
  clone (`/Users/jeremy/bin/ruru`), which keeps one on purpose for Ephergent
  seeding. Root cause: `models/seed_voices.py:20` does
  `sys.path.insert(0, repo_root)` and imports `characters_data` at module load
  time, so the "no-op" tests are only actually testing the no-op path when the
  file happens to be absent from that specific machine. Fix by isolating
  `sys.path`/`sys.modules` per test (like the third test in that file already
  does with `monkeypatch.setitem`) or by skipping those two tests outright
  when `find_spec("characters_data")` is not `None`. Out of scope for the
  dual-backend work; recording so it doesn't get re-diagnosed from scratch.
