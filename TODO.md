# TODO

## Dual-backend (MLX + CUDA) support

Implements the recommendation in `docs/nvidia_feasibility.md`: one repo, an
abstract `engine/base.py`, `MLXEngine`/`CUDAEngine` subclasses, backend chosen
by `RURU_TTS_BACKEND`. Nothing below is implemented yet.

**Development setup:** CUDA work happens on the NVIDIA Linux desktop (a plain
`git clone` of this repo — the only box that can run CUDA inference). This Mac
is the production install (launchd + nginx) and must never break, so every
Linux branch gets pulled and verified here before merging to `main`.

### Corrections to `docs/nvidia_feasibility.md`

- §3's `reference_material/` and `archive/private_removed_20260725/` paths do
  not exist. The live CUDA references are `../WeirDing/engine/tts.py` (sync,
  the direct ancestor) and `../sprecher/core/tts/qwen_engine.py` (the async
  ABC variant).
- §1's `jobs/tasks.py` line numbers are stale. Actual engine call sites:
  `jobs/tasks.py:61-63`, `115-120`, `215-217`.

---

### Phase 0 — Make the repo installable on Linux (on the Mac)

Without this, `uv sync` fails on Linux before any code runs.

- [ ] `pyproject.toml`: add platform markers so Apple-only wheels are skipped
      elsewhere:
      ```toml
      "mlx ; sys_platform == 'darwin'",
      "mlx-audio ; sys_platform == 'darwin'",
      ```
- [ ] `pyproject.toml`: add the CUDA extra, copied from
      `../WeirDing/pyproject.toml:12-13`:
      ```toml
      [project.optional-dependencies]
      cuda = ["torch>=2.0.0", "qwen-tts"]
      ```
- [ ] Leave `uv.lock` gitignored (`.gitignore`, "uv" block). Each machine
      resolves its own — this removes cross-platform lock churn entirely. Do
      not start committing it.
- [ ] Verify: `uv run pytest` green on the Mac, then merge to `main`.

Why this is safe: every MLX import in `engine/tts.py` is already lazy and
method-scoped (`_load_clone` :130-137, `_load_design` :139-146,
`_run_generate` :188-208). Module level only needs `numpy` + `soundfile`, so
the whole pytest suite imports and runs on a machine with no MLX installed.

### Phase 1 — Backend-agnostic refactor (on the Mac, merged before Phase 2)

MLX stays the only backend. Pure interface extraction, no behaviour change.
Use TDD (`superpowers:test-driven-development`).

- [ ] `engine/base.py` — `TTSEngineBase(ABC)` as sketched in feasibility doc
      §5. Keep it **synchronous** (doc §4.1); do **not** add
      `list_voices`/`validate_voice` (doc §4.2).
- [ ] `engine/result.py` — move `TTSResult` (`engine/tts.py:48-94`) out of the
      MLX module so `CUDAEngine` can import it without importing MLX code.
      Must keep both usages: tuple unpacking and `.write(path)`.
- [ ] `engine/mlx_engine.py` — today's `TTSEngine` (`engine/tts.py:97`) moved
      and renamed to `MLXEngine(TTSEngineBase)`. Leave `engine/tts.py` as a
      thin re-export shim (`from engine.mlx_engine import MLXEngine as
      TTSEngine`) so the move commit doesn't also churn
      `tests/conftest.py:47`'s `patch("engine.tts.TTSEngine")`.
- [ ] `config.py` — `TTS_BACKEND = _env("TTS_BACKEND", "mlx")`, next to
      `MODEL_SIZE` (`config.py:30`).
- [ ] `engine/__init__.py` — `get_engine_class()` factory, the single place
      that knows both class names exist (doc §5). Import the concrete class
      lazily inside the function so a CUDA-less Mac never imports `torch`.
- [ ] `jobs/tasks.py` — replace the three `from engine.tts import TTSEngine;
      engine = TTSEngine()` pairs (`:61-63`, `:115-120`, `:215-217`) with the
      factory. Update `tests/conftest.py`'s `mock_tts_engine` fixture to patch
      the factory instead of the class.
- [ ] `tests/test_engine_contract.py` (new, the important one) — parametrized
      suite asserting a backend class satisfies the ABC surface from doc §2:
      method presence and signatures, and that `generate_clone` /
      `generate_design` return something both tuple-unpackable and
      `.write()`-able. Parametrize over available backends, skipping any whose
      deps aren't installed. **This is what makes Linux development safe** —
      it proves a CUDA engine written on the Linux box drops into the Mac's
      call sites.
- [ ] `tests/test_engine_factory.py` (new) — `RURU_TTS_BACKEND` unset → MLX;
      `=cuda` → CUDA class; unknown value → clear error.
- [ ] Update `CLAUDE.md` (file layout + config table) and mark
      `docs/nvidia_feasibility.md` §5 as implemented.
- [ ] Verify on the Mac: `uv run pytest`, a real end-to-end narrate job through
      the UI, then `./reset_service.sh restart-only`. Merge to `main` only
      after that. (Kill any manually started `uv run ruru` first — known port
      conflict.)

### Phase 2 — `CUDAEngine` on the Linux clone

```bash
git clone git@github.com:BitsofJeremy/ruru.git && cd ruru
uv sync --extra cuda
uv run pytest            # must be green before writing anything
git checkout -b cuda-backend
```

- [ ] **Pre-flight before any code** — the phase is moot if the box isn't
      ready:
      - `nvidia-smi`
      - `uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`
      - Confirm `qwen-tts` still installs and the **non-MLX** Qwen3-TTS base +
        VoiceDesign weights are downloadable; note free VRAM.
      - If either fails, stop — don't write an engine against an API you can't
        run.
- [ ] `engine/cuda_engine.py` — `CUDAEngine(TTSEngineBase)`, ported from
      `../WeirDing/engine/tts.py` (`load` :43, `unload` :100, `is_loaded` :114,
      `generate_clone` :121, `generate_design` :223, `generate_to_file` :257).
      Required changes against that source:
      - Return `TTSResult` from `engine/result.py`, not raw `(wav, sr)` tuples
        (doc §2) — the one real porting hazard.
      - Keep `torch` / `qwen_tts` imports lazy and method-scoped, matching the
        MLX engine, so importing the module on the Mac is harmless.
      - Keep the `torch.cuda.is_available()` hard gate in `load()`.
      - `create_clone_prompt` / `generate_clone_with_prompt` (WeirDing :166,
        :193) are a CUDA-only optimization — port as extras, not ABC members.
- [ ] Verify on Linux: contract suite passes for `CUDAEngine`, then
      `RURU_TTS_BACKEND=cuda uv run ruru` and a real narrate job end to end.

### Phase 3 — Cross-machine verification loop

Standing rules for every round trip:

1. Push `cuda-backend` from Linux.
2. On the Mac: `git fetch && git checkout cuda-backend && uv sync` (plain —
   **never** `--extra cuda` on the Mac) → `uv run pytest` → real MLX narrate
   smoke test.
3. Merge to `main` only after the Mac is green. Restart the launchd agent last.
4. The Linux box never installs `mlx`; the Phase 0 markers enforce it.

- [ ] Optional, cheap, worth it: a GitHub Actions job running `uv sync && uv
      run pytest` on `ubuntu-latest`. It won't catch MLX runtime regressions,
      but it catches every "this only imports on a Mac" mistake at push time,
      so the Mac pull becomes a formality rather than the first signal.

### Verification summary

| Where | Command | Proves |
|---|---|---|
| Mac, Phase 0 | `uv run pytest` | markers didn't break the Mac install |
| Mac, Phase 1 | `uv run pytest` + UI narrate job + `./reset_service.sh restart-only` | refactor is behaviour-neutral in prod |
| Linux, Phase 2 | `uv run pytest` (incl. contract suite vs `CUDAEngine`) | CUDA engine satisfies the shared contract |
| Linux, Phase 2 | `RURU_TTS_BACKEND=cuda uv run ruru` + narrate job | CUDA inference actually works |
| Mac, Phase 3 | `uv run pytest` + narrate job on `cuda-backend` | Linux work didn't break MLX |

---

## Other open items

- No CSRF protection (acceptable for a single-user local app).
- Voice edit page's image upload/remove controls call `/api/voices/{id}/image`,
  which doesn't exist yet (404). See `PROGRESS.md` "Session: 2026-07-10".
