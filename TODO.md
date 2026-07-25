# TODO — ruru Rebrand & Public Release Plan

## Context

WeirDing is being prepared for public release under a new name: **ruru** (the morepork owl — a two-note night call, fitting for a tool that runs overnight turning books into audio). This document is an execution plan for three sequential tasks. Work them **in order** — cleanup first, so nothing sensitive is ever committed under the new name; rebrand second; the nvidia feasibility doc last since it depends on nothing above and is a design doc only (no code changes).

### Cross-cutting decisions already made (do not re-ask)

- **Rebrand depth is FULL**, including internal identifiers: env var prefix (`WEIRDING_` → `RURU_`), DB filename, console script name, launchd label, nginx conf name. This is not just a cosmetic string swap.
- Because internal identifiers change, **migration steps are mandatory** so the currently-running local service and its existing job history in SQLite survive the rename. Do not just rename and abandon old state.
- The nvidia task is a **written feasibility/design document only** — no abstract base class, no subclasses, no code refactor. Do not implement it.
- Task sequence is fixed: **1) GitHub cleanup → 2) Rebrand → 3) Nvidia feasibility doc.**
- Where code is touched (rebrand task), use **TDD** (`superpowers:test-driven-development` skill) — write/adjust the failing test first, then make it pass.
- For the rebrand's visual/palette pass, use the **impeccable** skill to derive and apply a polished color system from `icon.png`.
- **Keep `voice_clones/lilith.mp3` + `lilith.txt`** — this is the user's default voice and must remain the app's out-of-the-box default after cleanup. Everything else in `voice_clones/` (the Neil/Ray narrator clones) is removed. All other voice assets (Ephergent characters) may be refactored/removed freely.
- **Stopping/restarting the running service is authorized** — Sonnet may `launchctl bootout` the old agent, kill manually-started processes, and restart under the new label as needed to complete the rebrand + migration. Confirm no unrelated work is in flight first.

---

## Task 1 — Public GitHub Cleanup (privacy/legal)

**Goal:** Remove all tracked sensitive/private content and personal identifiers from the repo (working tree *and* git history) before any public rename happens, so `ruru` never inherits leaked data in its history.

### Actions

1. **Inventory sensitive tracked files** (confirm nothing new has crept in since this plan was written):
   ```
   git ls-files | grep -E 'characters_data.py|ephergent_voices/|voice_clones/|hello_lilith.mp3|reference_material/|sqlalchemy_orm_integration/|docs/superpowers/|.claude/settings.local.json'
   ```
2. **Remove from the working tree and untrack**:
   - `characters_data.py` — Ephergent character voice profiles.
   - `ephergent_voices/*.wav` — 10 character reference audio files.
   - `voice_clones/` — **KEEP `lilith.mp3` + `lilith.txt`** (the user's default voice; it ships with the public repo). **REMOVE the named professional narrator clones** `Neil_Hellegers_Voice.mp3`/`_Text.txt` and `Ray_Porter_Voice.mp3`/`_Text.txt` — real voice actors' names and cloned audio, the biggest legal exposure, highest priority removal. Do NOT gitignore the whole `voice_clones/` dir (that would exclude lilith); instead untrack/remove only the narrator files and keep lilith tracked.
   - `hello_lilith.mp3` at repo root.
   - `reference_material/` — old NVIDIA code, `elevenlabs.md`, a copyrighted third-party PDF, test WAVs.
   - `sqlalchemy_orm_integration/` — internal dev notes, not needed publicly.
   - `docs/superpowers/` — internal plan/spec markdown, not for public consumption.
   - `.claude/settings.local.json` — leaks local machine config; untrack and add to `.gitignore`.
   Use `git rm -r --cached <path>` for each, then physically delete or move to a local (gitignored) archive if you want to keep them for your own use — do not leave them in the working tree if they'll be re-added accidentally.
3. **Extend `.gitignore`** to cover anything above that should never be re-tracked (e.g. `.claude/settings.local.json`, `reference_material/`, and the specific narrator clone files — but NOT the whole `voice_clones/` dir, since `lilith.mp3`/`lilith.txt` stay tracked) — check current `.gitignore` first so you don't duplicate existing entries (`data/`, `*.db`, `.env*`, `clone_voices/`, `example_epubs/`, `tasks/`, `docs/plans/`, `test_output/` are already covered).
4. **Scrub hardcoded personal paths and identifiers**:
   - `config.py` — `WORK_DIR` and `EPHERGENT_REPO` defaults currently point at `/Users/jeremy...`; replace with relative/repo-root-derived defaults.
   - `jobs/tasks.py:23` — hardcoded `/Users/jeremy` path.
   - `docs/uninstall.md:33,41` — hardcoded `/Users/jeremy` path references; genericize.
   - `TODO.md` — this file itself may reference the old NVIDIA repo path (`/Users/jeremy/Documents/current_projects/ai_speech/WeirDing`); keep that reference only inside the Task 3 feasibility doc, not scattered elsewhere.
   - `templates/base.html` — author "Jeremy Schroeder" + GitHub handle `BitsofJeremy`; decide with the user what (if anything) public attribution should say, or genericize/remove.
   - `README.md` — remove or genericize the `ephergent.com` link if it's tied to private content.
5. **Decide and execute on git history**: deleting files from HEAD does **not** remove them from history — anyone can `git log -p` and recover the voice-actor clones. Two options, pick one explicitly with the user before proceeding:
   - (a) Rewrite history with `git filter-repo` (preferred over `filter-branch`) to strip the sensitive paths from all commits/branches/reflog, then force-push if this repo already has a remote.
   - (b) Start a fresh history (`git checkout --orphan`, single clean initial commit) if the existing history has limited value and rewriting is riskier than restarting.
   Do not skip this step — it's the actual privacy/legal fix; deleting only from the working tree is cosmetic.
6. **Verify README** is accurate for a public reader post-cleanup (setup instructions still valid once `characters_data.py`/`ephergent_voices/` are gone — note that the Ephergent character seeding feature will no longer work out of the box for the public repo unless you document that as an optional/local-only feature).

### Risks / migration notes

- Voice-actor clone removal is a legal issue, not just tidiness — verify with `git log --all --full-history -- voice_clones/` after history rewrite that zero commits reference them.
- Rewriting history invalidates any existing clone URLs/PRs if this has ever been pushed publicly; confirm current remote state before force-pushing.
- Removing `characters_data.py`/`ephergent_voices/` breaks `models/seed_voices.py` at runtime unless it's made tolerant of the files being absent (needed before Task 2 verification, since you'll be running the app to check the rebrand). Refactor `seed_voices.py` to seed **only the lilith default voice** from `voice_clones/lilith.mp3`/`lilith.txt` and make Ephergent seeding optional/skipped when its files are absent.

### Acceptance / verification

```bash
git ls-files | grep -E 'characters_data.py|ephergent_voices/|Neil_Hellegers|Ray_Porter|hello_lilith.mp3|reference_material/|sqlalchemy_orm_integration/|docs/superpowers/|settings.local.json'
# must return nothing

git ls-files | grep 'voice_clones/lilith'
# must STILL return lilith.mp3 and lilith.txt (default voice is retained)

git log --all --full-history -- 'voice_clones/Neil_Hellegers*' 'voice_clones/Ray_Porter*' characters_data.py ephergent_voices/
# must return nothing after history rewrite (lilith is fine to keep in history)

grep -rn "/Users/jeremy" --include="*.py" --include="*.md" .
# must return nothing (or only intentional Task 3 doc reference)

uv run pytest
# must still pass (adjust/mark tests that depended on removed fixtures)
```

---

## Task 2 — Rebrand to "ruru"

**Goal:** Fully rebrand the app from WeirDing to ruru, including internal identifiers, with a migration path so the existing running service and job history survive the switch. Use TDD for code changes and the impeccable skill for the visual/palette pass.

### 2a. Derive the palette

- Use the **impeccable** skill against `icon.png` at repo root to derive a cohesive new color system (background/card/border/accent/accent2/text/muted) to replace the current WeirDing purple/teal scheme.
- Current source of truth to replace: inline Tailwind config in `templates/base.html` lines 9–26 (`kj-bg #0f0f14`, `kj-card #1a1a24`, `kj-border #2a2a3a`, `kj-accent #7c5cfc`, `kj-accent2 #00d4aa`, `kj-text #e4e4ef`, `kj-muted #8888a0`). Keep the same variable *names* (`kj-*`) unless impeccable recommends otherwise — renaming the CSS variables is cosmetic-only work and low priority next to the identifier rename below; don't let it balloon scope.

### 2b. User-visible strings and assets

- `templates/base.html`:
  - Line 6 `<title>` — update to ruru.
  - Line 30 favicon reference `/static/favicon.svg` — replace with a favicon derived from `icon.png` (add `static/favicon.svg` or `.ico`/`.png` as appropriate; wire the `<link>` tag correctly since no raster favicon is currently wired).
  - Line 39 logo markup `<span text-kj-accent>WEIR</span><span text-kj-accent2>DING</span>` — replace with ruru's two-note branding (e.g. split "ru"/"ru" across the accent spans to echo the two-note call).
  - Line 41 tagline, line 98 GitHub link, line 104 author — update.
- All other templates (`index.html:3` and the rest) — grep-and-replace remaining "WeirDing" occurrences.
- `static/css/style.css:1` — header comment.
- `app.py:1` (module docstring), `:112`, `:117` (log strings referencing WeirDing).
- `config.py:1` docstring, and `CREATOR_TAG` default `"Created by WeirDing"` — this string is embedded in output MP3 metadata, so changing the default changes future audio file tags (does not retroactively change already-generated files; note this in commit message, not a bug).

### 2c. Internal identifiers (the risky rename set — do this carefully, one identifier group at a time, testing after each)

- **Env var prefix**: `WEIRDING_` → `RURU_` in `config.py` (~13 vars), `jobs/tasks.py:23`, `app.py:927`, `install_macos.sh:114-116`.
  - Migration: support **both** prefixes during a transition window — read `RURU_X` first, fall back to `WEIRDING_X` with a deprecation log line, OR ship a one-shot env-var migration step in `install_macos.sh`/`reset_service.sh` that rewrites any existing `.env`/launchd plist env block from old to new prefix. Pick one approach and implement it consistently — don't leave half-migrated code.
- **Console script**: `pyproject.toml` `[project.scripts]` `weirding = "app:main"` and `name = "weirding"` → rename to `ruru`. Also update every place that invokes the old binary name: `install_macos.sh:42`, `reset_service.sh:60,159`.
- **Launchd label**: `com.jeremy.weirding.web` → `com.jeremy.ruru.web` in `install_macos.sh:38` and `reset_service.sh:9,54`.
  - **Migration is mandatory here**: you must `launchctl bootout`/unload the *old* label before loading the new one, or you'll orphan a running WeirDing agent that keeps holding the port (this is a documented pitfall in this repo already — see "Port conflict" in CLAUDE.md Common Pitfalls). Add an explicit unload-old-then-load-new step to `install_macos.sh` and to `reset_service.sh`, not just a silent rename.
- **DB filename**: `weirding.db` → `ruru.db` in `config.py` and `reset_service.sh:12`.
  - **Migration is mandatory here**: a plain rename in code with no data migration silently orphans existing job history (points at a DB file that no longer matches what's on disk). On first run under the new name, if `ruru.db` doesn't exist but `weirding.db` does, rename/copy the old file to the new path before `init_db()` runs — add this as an explicit one-shot migration check in startup or in `install_macos.sh`, and log what happened.
- **Log filenames**: `weirding.log`/`weirding.err` → `ruru.log`/`ruru.err` (paths referenced in `reset_service.sh` and produced by launchd plist stdout/stderr redirection).
- **Nginx site**: `weirding.conf` → `ruru.conf`, referenced `install_macos.sh:170,189`.
- **Leave alone**: `engine/tts.py`'s `file_prefix = "weirding_tts"` is a temp-file-only prefix, harmless to rename or leave — low priority, do it only if time permits as part of the grep sweep.

### Risks / migration notes

- Sequence the internal-identifier renames so the app is runnable and testable after each group (env prefix, then console script, then launchd label, then DB, then nginx) rather than one giant simultaneous change — easier to isolate a break.
- Test the actual migration path, not just the fresh-install path: with an existing `weirding.db` and running launchd agent under the old label present, run the upgrade path and confirm the new agent starts under `com.jeremy.ruru.web`, serves on the same port, and the UI shows prior job history.
- Document the migration steps in `docs/uninstall.md` or a new `docs/migration.md` so a human running this by hand later (not just Sonnet) knows the old-label-unload and DB-rename steps are required, not optional.

### Acceptance / verification

```bash
uv run pytest
# all tests pass with new names/config

grep -rIn "weirding\|WeirDing\|WEIRDING_" --include="*.py" --include="*.html" --include="*.css" --include="*.toml" --include="*.sh" .
# should return zero matches outside of explicit historical/migration-comment references

uv run ruru
# app starts under new console script name; visit http://localhost:5151 and confirm:
#   - title/logo/tagline show "ruru" branding
#   - new color palette derived from icon.png is applied
#   - favicon loads (no 404 in browser console/network tab)

sudo bash install_macos.sh
# re-run installer against an existing WeirDing install; confirm old launchd label
# is unloaded, new com.jeremy.ruru.web is loaded, DB migrated (job history intact),
# nginx serves ruru.conf, no port conflict per CLAUDE.md's documented pitfall
```

---

## Task 3 — Nvidia/CUDA Feasibility Assessment (doc only — NO CODE CHANGES)

**Goal:** Produce a written design document answering: is dual-backend (MLX + CUDA) support feasible in a single repo, and if so, what would the interface look like? This is an assessment for future planning — do not implement anything from it in this pass.

### Actions

1. Create the doc at `docs/nvidia_feasibility.md` (or `docs/plans/nvidia-feasibility.md` if `docs/plans/` convention is preferred — check whether that directory still exists post-Task-1 cleanup since it's gitignored).
2. Document current MLX confinement, citing exact locations:
   - `engine/tts.py` — single concrete `TTSEngine` class (line ~97), no abstract base.
   - MLX-specific imports are lazy, scoped inside methods only: `_load_clone`/`_load_design` (lines ~130–146, `mlx_audio.tts.utils.load_model`) and `_run_generate` (line ~188, `mlx_audio.tts.generate.generate_audio`).
   - MLX-community 8-bit model ID constants at module level, lines ~26–27.
   - Callers (`jobs/tasks.py`, `app.py`) import `TTSEngine` directly — no interface indirection today.
3. Document the de-facto public contract that any CUDA backend would need to satisfy: `load(size)`, `unload()`, `is_loaded`, `generate_clone(...)`, `generate_design(...)`, `generate_to_file(...)`, `create_clone_prompt(...)`, and the `TTSResult` return shape (unpackable as `(wav, sr)`, plus `.write(path)`).
4. Document what already exists in `reference_material/` (preserved for reference — do not restore it to active use in this task):
   - `reference_material/tts-nvidia.py` — old CUDA implementation using `torch`, `device="cuda:0"`.
   - `reference_material/qwen_engine-nvidia.py` — `class QwenEngine(TTSEngine)` subclassing an abstract `core.tts.base.TTSEngine` from the original NVIDIA repo, which defined `generate`/`generate_clone`/`generate_to_file`/`list_voices`/`validate_voice`/`is_available` as **async** methods.
   - Original NVIDIA repo location for cross-reference: `/Users/jeremy/Documents/current_projects/ai_speech/WeirDing` (this path reference is acceptable to keep here since it's inside the feasibility doc itself, not scattered through the codebase — see Task 1 note).
5. Reconcile and call out the key interface mismatch explicitly: **old NVIDIA methods were `async` and exposed `list_voices`/`validate_voice`; current MLX engine is synchronous and returns `TTSResult`.** Any shared interface must resolve this — recommend picking one calling convention (the doc should state a recommendation, e.g. keep sync at the engine layer since the FastAPI routes already dispatch to threads via `asyncio.to_thread()`, so an async engine interface would be redundant).
6. State a clear recommendation: **single repo, one abstract base class** (e.g. `engine/base.py` defining `generate_clone`/`generate_design`/`generate_to_file`/`load`/`unload`/`is_loaded`), with `MLXEngine` and `CUDAEngine` subclasses, selected via a new backend-selection env var following the existing `MODEL_SIZE`/`MLX_MODELS_PATH` naming convention (e.g. `RURU_TTS_BACKEND=mlx|cuda`). State this is a recommendation only — no base class or subclass should actually be created in this task.
7. Explicitly answer the two questions the original TODO raised: (a) is dual-backend feasible — yes, low surface area; (b) one repo or two — recommend one repo with the abstract-base pattern above, and give the reasoning (small isolated MLX surface, shared job/DB/template layer would otherwise be duplicated across two repos).

### Risks / notes

- This is explicitly scoped as **assessment only**. If Sonnet notices the abstract base would be easy to write, resist the urge — the user asked for a doc, not an implementation, in this pass.
- Keep the doc grounded in the actual current file/line references above rather than generic advice, so it's directly actionable when someone picks up implementation later.

### Acceptance / verification

```bash
test -f docs/nvidia_feasibility.md
# file exists

git diff --stat -- engine/ jobs/ app.py
# must be EMPTY — confirms no code changes were made as part of this task

grep -c "recommend" docs/nvidia_feasibility.md
# doc contains an explicit recommendation, not just a list of facts
```
