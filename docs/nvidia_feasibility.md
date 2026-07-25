# Nvidia/CUDA Backend Feasibility Assessment

**Status:** Design/planning document only. Nothing in this document has been
implemented. No abstract base class, subclass, or refactor exists yet — see
"Recommendation" below for why this is deliberately deferred.

**Question this doc answers:** Is dual-backend support (MLX on Apple Silicon
+ CUDA on Nvidia) feasible in a single repo, and if so, what would the
interface look like?

**Short answer:** Yes, feasible, with a small, well-isolated surface area.
Recommend one repo, one abstract base class (`engine/base.py`), with
`MLXEngine`/`CUDAEngine` subclasses selected by a `RURU_TTS_BACKEND` env var.
No implementation in this pass.

---

## 1. Current MLX confinement

The app today has exactly one concrete TTS engine and no interface
indirection. Everything MLX-specific is isolated to `engine/tts.py`:

- **`class TTSEngine`** — `engine/tts.py:97`. The only TTS engine class in
  the codebase; nothing subclasses or wraps it.
- **MLX-specific imports are lazy, scoped inside methods only** (not at
  module level, so importing `engine.tts` doesn't require MLX to be
  installed until a model actually loads):
  - `_load_clone()` — `engine/tts.py:130-137`, imports
    `mlx_audio.tts.utils.load_model`.
  - `_load_design()` — `engine/tts.py:139-146`, imports the same
    `load_model` function for the VoiceDesign model.
  - `_run_generate()` — `engine/tts.py:188-208`, imports
    `mlx_audio.tts.generate.generate_audio`, the function that actually
    drives inference for both clone and design modes.
- **MLX-community 8-bit model ID constants at module level** —
  `engine/tts.py:26-27`:
  ```python
  CLONE_MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
  DESIGN_MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"
  ```
- **Callers import `TTSEngine` directly, with no interface indirection
  today:**
  - `jobs/tasks.py:72,126,226` — `from engine.tts import TTSEngine`, once
    per job handler (`run_generate_tts`, `run_narrate_document`,
    `run_regenerate_chapter`).
  - `app.py` doesn't import `TTSEngine` directly (voice preview/other routes
    go through the job layer), but `jobs/tasks.py` is the single choke
    point every generation path already funnels through — which is good
    news for swapping backends later.

Net effect: MLX touches exactly one file. A CUDA backend would not need to
touch `jobs/tasks.py`'s call sites at all if it satisfies the same method
contract (see §2) — only the choice of *which* engine class to instantiate
would need to change, and that's a single-point decision if routed through a
factory (see §4).

## 2. The de-facto public contract

Nothing today declares this as a formal interface (no ABC, no `Protocol`),
but every caller only ever uses this subset of `TTSEngine`, so it's the
contract any CUDA backend would need to satisfy to be a drop-in replacement:

| Member | Signature (current MLX impl) | Notes |
|---|---|---|
| `load(size)` | `load(self, size: str = "auto") -> None` | Eager pre-warm of both models. |
| `unload()` | `unload(self) -> None` | Frees loaded models. |
| `is_loaded` | `@property -> bool` | True if any model resident. |
| `generate_clone(...)` | `(text, ref_audio_path, ref_text, language="English") -> TTSResult` | Voice cloning from reference audio. |
| `generate_design(...)` | `(text, voice_description, language="English") -> TTSResult` | Voice from a text description. |
| `generate_to_file(...)` | `(text, output_path, *, voice_description=None, ref_audio_path=None, ref_text=None, language="English") -> Path` | Dispatches to clone or design based on which kwargs are set. |
| `create_clone_prompt(...)` | `(ref_audio_path, ref_text) -> tuple` | Compatibility shim — see below. |
| `TTSResult` | tuple-unpackable as `(wav, sr)`, plus `.write(path)` | Return shape for `generate_clone`/`generate_design`. |

`TTSResult` (`engine/tts.py:48-94`) matters as much as the method
signatures: callers do both `wav, sr = engine.generate_clone(...)` (tuple
unpacking, `engine/tts.py:64-73`) and `result.write(output_path)`
(`jobs/tasks.py` job handlers). Any CUDA implementation must return
something that supports both usages, not just a raw `(np.ndarray, int)`
tuple.

## 3. What already exists in `reference_material/` (historical CUDA code)

**Do not restore these to active use in this task** — they're preserved
purely for reference. (`reference_material/` was removed from the public
repo in the Task 1 privacy cleanup and now lives in
`archive/private_removed_20260725/reference_material/` — untracked,
local-only.)

- **`reference_material/tts-nvidia.py`** — the original CUDA
  implementation. Key differences from today's MLX `engine/tts.py`:
  - Hardcodes `self._device: str = "cuda:0"` and raises
    `RuntimeError("CUDA is not available...")` if `torch.cuda.is_available()`
    is false — a hard dependency check the MLX engine has no equivalent of
    (MLX doesn't need a "is a GPU present" gate the same way).
  - Uses `torch` + a `qwen_tts` package's `Qwen3TTSModel.from_pretrained(...,
    device_map="cuda:0", dtype=torch.bfloat16)` — a completely different
    model-loading API from MLX-Audio's `load_model(MODEL_ID)`.
  - Already has a synchronous, non-async surface (`load`, `unload`,
    `is_loaded`, `generate_clone`, `generate_design`, `generate_to_file`,
    plus `create_clone_prompt`/`generate_clone_with_prompt` for reusing a
    voice-clone prompt across multiple chunks) that lines up closely with
    the current MLX contract in §2 — this file is effectively an earlier,
    less-polished ancestor of `engine/tts.py`, not a fundamentally
    different design.
- **`reference_material/qwen_engine-nvidia.py`** — `class QwenEngine(TTSEngine)`,
  subclassing an abstract `core.tts.base.TTSEngine` from the *original*
  NVIDIA repo (that base class itself isn't in this repo — only the
  subclass survived into `reference_material/`). This is the more
  significant reference point because it shows what a *formal* abstract
  interface looked like previously:
  - Its inherited abstract methods were **`async`**:
    `generate`, `generate_clone`, `generate_to_file` (all defined with
    `async def` and awaited internally via
    `loop.run_in_executor(...)` to push blocking CUDA work off the event
    loop — see lines 154-233).
  - It also exposed **`list_voices()`** and **`validate_voice()`**, concepts
    the current MLX engine has no equivalent of (voice listing/validation
    lives in `models/db.py` and route handlers today, not the engine layer).
  - It used a **singleton pattern** (`QwenEngine.get_engine()` /
    `_instance`), whereas the current MLX `TTSEngine` is instantiated fresh
    per call site (`jobs/tasks.py:74,131,228` each do `engine = TTSEngine()`).
  - Model IDs were configured via a *different* env var family entirely
    (`SPRECHER_QWEN_MODEL`, `SPRECHER_QWEN_CLONE_MODEL`) — a naming
    convention neither the `WEIRDING_`/`RURU_` prefix nor `MODEL_SIZE`/
    `MLX_MODELS_PATH` inherited.

- Original NVIDIA repo location for cross-reference:
  `/Users/jeremy/Documents/current_projects/ai_speech/WeirDing` (this
  reference is intentionally kept here rather than scattered through the
  codebase — see Task 1's cleanup notes in `TODO.md`).

## 4. The interface mismatch, and how to resolve it

Two concrete mismatches between the old NVIDIA engine and today's MLX
engine, both of which a shared interface must pick a side on:

1. **Async vs. sync.** The old `core.tts.base.TTSEngine` (via
   `QwenEngine`) declared `generate`/`generate_clone`/`generate_to_file` as
   `async def`. The current MLX `TTSEngine` is fully synchronous — no
   `async`/`await` anywhere in `engine/tts.py`.

   **Recommendation: keep the engine layer synchronous.** The FastAPI
   routes and job handlers already dispatch *synchronous* job functions to
   a thread pool via `asyncio.to_thread()` (see `CLAUDE.md`'s "Two database
   access patterns" section and `jobs/queue.py`'s `JobRunner`). An `async`
   engine method would just be awaited immediately inside a function that's
   already running in a worker thread via `to_thread()` — pure ceremony
   with no concurrency benefit, since CUDA/MLX inference is CPU/GPU-bound,
   not I/O-bound. The old NVIDIA repo's `async def` + `run_in_executor`
   pattern was solving the same "don't block the event loop" problem that
   `asyncio.to_thread()` at the job-runner layer already solves once, for
   every backend, at a single point — pushing async down into the engine
   itself would duplicate that.

2. **Surface area: `list_voices`/`validate_voice`.** The old `QwenEngine`
   exposed these; the current MLX engine doesn't, because that
   responsibility moved to `models/db.py` (the `voices` table) and route
   handlers. **Recommendation: do not add these back to the engine
   interface.** They're a data/routing concern, not a TTS-backend concern,
   and re-introducing them at the engine layer would re-couple the engine
   to DB schema decisions it shouldn't know about.

## 5. Recommendation: one repo, one abstract base class

**Single repo.** Splitting into two repos (one MLX, one CUDA) would
duplicate the entire job queue, SQLite schema, FastAPI routes, and
templates — none of which differ between backends — for the sake of
isolating a genuinely small amount of backend-specific code (one file,
~250 lines, per §1). The MLX surface area is already naturally contained to
`engine/tts.py`; a second repo buys isolation the current file layout
already provides for free, at the cost of keeping two job/DB/template
layers in sync forever.

**One abstract base class**, e.g. `engine/base.py`:

```python
from abc import ABC, abstractmethod

class TTSEngineBase(ABC):
    @abstractmethod
    def load(self, size: str = "auto") -> None: ...

    @abstractmethod
    def unload(self) -> None: ...

    @property
    @abstractmethod
    def is_loaded(self) -> bool: ...

    @abstractmethod
    def generate_clone(self, text, ref_audio_path, ref_text, language="English"): ...

    @abstractmethod
    def generate_design(self, text, voice_description, language="English"): ...

    @abstractmethod
    def generate_to_file(self, text, output_path, *, voice_description=None,
                          ref_audio_path=None, ref_text=None, language="English"): ...
```

...with `MLXEngine(TTSEngineBase)` (today's `engine/tts.py:TTSEngine`,
renamed/moved) and a new `CUDAEngine(TTSEngineBase)` built from
`reference_material/tts-nvidia.py`'s logic, updated to match the current
`TTSResult` return shape (§2) instead of the old raw-tuple return.

**Backend selection** via a new env var following the existing
`MODEL_SIZE`/`MLX_MODELS_PATH` naming convention:
`RURU_TTS_BACKEND=mlx|cuda` (default `mlx`), read once at the same call
sites that currently do `from engine.tts import TTSEngine; engine =
TTSEngine()` (`jobs/tasks.py:72,126,226`) — ideally routed through one small
factory function (e.g. `engine.get_engine_class()`) rather than three
separate `if backend == ...` branches, so there's exactly one place that
knows both class names exist.

### Why this, concretely

- **(a) Is dual-backend feasible?** Yes — low surface area. Exactly one
  file (`engine/tts.py`, ~250 lines) is MLX-specific; imports are already
  lazy and scoped to methods, not module level, so a `CUDAEngine` module
  can equally defer its `torch`/`qwen_tts` imports and the two backends
  never need to be importable in the same environment simultaneously.
- **(b) One repo or two?** One repo, abstract-base pattern. The
  alternative (two repos) would duplicate `jobs/`, `models/`, `templates/`,
  and all FastAPI routes — none of which are backend-specific — while only
  saving the isolation of a single already-isolated file.

## What this doc does *not* do

No code in this repo was changed to produce this document. No
`engine/base.py`, no `CUDAEngine`, no `RURU_TTS_BACKEND` env var, no
factory function — all of the above is a recommendation for a future
implementation pass, not a description of current code.
