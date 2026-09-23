"""YuE2-3B music-generation engine (NOT a TTSEngineBase subclass).

Two runtimes behind one generate() method:

- "gguf"    — shells out to an audio.cpp binary (Q4_K_M quant, ~7 GB VRAM).
              Default on the 12 GB RTX 3060 box. No LoRA support.
- "pytorch" — m-a-p yue2_infer wheel, optional instrumental-LoRA fold.
              Only runtime that supports the Mothersuperior LoRA; song length
              is capped (config.YUE2_MAX_SECONDS) to stay inside 12 GB.
- "mlx"     — Apple Silicon via ahmadw/YuE2-3B-MLX generate.py (8-bit).
              No LoRA support (verified 2026-09-16: the MLX repo ships no
              adapter files and no --lora flag).

Heavy imports are method-scoped so importing this module on any platform is
harmless (same rule as cuda_engine.py / mlx_engine.py).
"""
from __future__ import annotations

import logging
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from engine.result import TTSResult

logger = logging.getLogger(__name__)

GGUF_MAIN = "yue2-3b-q4_k_m.gguf"
GGUF_VAE = "yue2-vae-f16.gguf"


class YuE2Engine:
    """Wraps YuE2 music generation. One generate() call, one song."""

    def __init__(self) -> None:
        self._pipe = None
        self._pipe_lora: bool | None = None

    # -- lifecycle --------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._pipe is not None

    def unload(self) -> None:
        self._pipe = None
        self._pipe_lora = None

    def _resolve_runtime(self) -> str:
        import config

        runtime = (config.YUE2_RUNTIME or "").strip().lower()
        if runtime:
            return runtime
        return "mlx" if platform.system() == "Darwin" else "gguf"

    @staticmethod
    def _configured_runtime() -> str:
        """The raw RURU_YUE2_RUNTIME value ('' when auto)."""
        import config

        return (config.YUE2_RUNTIME or "").strip().lower()

    @staticmethod
    def _pytorch_ready() -> tuple[bool, str]:
        """(ready, reason) — is the yue2_infer wheel + CUDA actually usable?

        Cached on the class: importing torch takes seconds, and both the
        music page render and every pytorch dispatch ask this. The mlx/cuda
        extras conflict (pyproject), so on macOS this is (False, ...) forever:
        there is no LoRA path on Apple Silicon.
        """
        cached = YuE2Engine.__dict__.get("_pytorch_ready_cache")
        if cached is not None:
            return cached
        try:
            import yue2  # noqa: F401
        except ImportError:
            result = (False, "the yue2_infer wheel is not installed")
        else:
            try:
                import torch

                if torch.cuda.is_available():
                    result = (True, "")
                else:
                    result = (False, "no CUDA device available")
            except ImportError:
                result = (False, "torch is not installed")
        YuE2Engine._pytorch_ready_cache = result
        return result

    @staticmethod
    def _pytorch_unavailable_error(reason: str, *, instrumental: bool) -> RuntimeError:
        """Actionable error for a pytorch dispatch that cannot run here.

        The two real-world failure modes: macOS (mlx/cuda extras conflict,
        MLX port has no LoRA support) and Linux after `uv sync` pruned the
        --no-deps yue2_infer wheel. Both used to surface as a raw
        ModuleNotFoundError / torch error.
        """
        if instrumental:
            return RuntimeError(
                "Instrumental LoRA is only supported by the PyTorch (CUDA) "
                f"runtime, which is unavailable here ({reason}). macOS has no "
                "LoRA path at all — uncheck Instrumental to generate with the "
                "local runtime instead."
            )
        return RuntimeError(
            f"YuE2 PyTorch runtime unavailable ({reason}). The yue2_infer "
            "wheel is installed --no-deps outside the lockfile: if uv sync "
            "pruned it, reinstall per README 'Music (YuE2)'."
        )

    # -- generation ---------------------------------------------------------
    def generate(
        self,
        style: str,
        lyrics: str,
        instrumental: bool = False,
        seed: int | None = None,
        cot: str | None = None,
        cfg_scale: float | None = None,
        steps: int | None = None,
        max_seconds: int | None = None,
    ) -> TTSResult:
        """Generate one song; returns a TTSResult (tuple-unpackable, .write-able)."""
        import config

        runtime = self._resolve_runtime()
        explicit = bool((config_runtime := self._configured_runtime()))
        if instrumental and runtime != "pytorch":
            logger.info(
                "Instrumental LoRA requested: switching %s -> pytorch runtime", runtime
            )
            runtime = "pytorch"
        if runtime == "gguf" and not explicit and not self._gguf_ready():
            # gguf is the AUTO default on Linux, but if the weights/binary
            # aren't set up, fall back to pytorch instead of failing — base
            # pytorch handles vocal songs fine (only instrumental needs LoRA).
            # An explicit RURU_YUE2_RUNTIME=gguf gets no fallback: the operator
            # asked for gguf, so a missing setup must fail loudly.
            logger.info(
                "YuE2 gguf runtime not ready (missing weights or "
                "RURU_YUE2_AUDIOCPP_BIN) — falling back to pytorch"
            )
            runtime = "pytorch"
        # Effective length cap, computed once after every runtime switch:
        # per-call max_seconds always wins; the config default applies to
        # pytorch only (12 GB VRAM headroom), so blank fields behave exactly
        # as before.
        cap = (
            max_seconds
            if max_seconds is not None
            else (config.YUE2_MAX_SECONDS if runtime == "pytorch" else None)
        )
        if cap:
            lyrics = self._cap_lyrics(lyrics, cap_seconds=cap)
        if runtime != "mlx" and (cfg_scale is not None or steps is not None):
            logger.info("cfg_scale/steps are MLX-only — ignoring")
        if runtime == "gguf":
            return self._generate_gguf(style, lyrics, seed, cot, cap)
        if runtime == "pytorch":
            ready, reason = self._pytorch_ready()
            if not ready:
                raise self._pytorch_unavailable_error(
                    reason, instrumental=instrumental
                )
            return self._generate_pytorch(style, lyrics, instrumental, seed, cot, cap)
        if runtime == "mlx":
            return self._generate_mlx(
                style, lyrics, seed, cot, cfg_scale, steps, max_seconds, cap
            )
        raise ValueError(f"Unknown YuE2 runtime: {runtime!r}")

    @staticmethod
    def _gguf_ready() -> bool:
        """True when the gguf runtime has both its weights and a binary."""
        import config

        if not config.YUE2_AUDIOCPP_BIN:
            return False
        return (Path(config.YUE2_GGUF_DIR) / GGUF_MAIN).is_file()

    @staticmethod
    def _cap_lyrics(lyrics: str, *, cap_seconds: int) -> str:
        """Cap requested song length when a length cap is active.

        Called for every runtime when generate() resolves a cap (the pytorch
        default via config.YUE2_MAX_SECONDS, or any runtime via max_seconds).

        The 12 GB card OOMs on long-context pytorch runs (upstream peaked at
        14 GiB). Timed section tags are the strongest length steer the LoRA
        knows, so when the lyrics use ``[tag m:ss-m:ss]`` lines that run past
        the cap, compress the times proportionally. Untagged / bare lyrics are
        passed through untouched (the cap alone can't steer those).
        """
        pat = re.compile(r"^(\[\w+)( \d{1,2}:\d{2})-(\d{1,2}:\d{2})(\])$")

        def secs(ts: str) -> int:
            m, s = ts.strip().split(":")
            return int(m) * 60 + int(s)

        def fmt(total: int) -> str:
            return f"{total // 60}:{total % 60:02d}"

        lines = lyrics.splitlines()
        matches = [pat.match(ln) for ln in lines]
        if not matches or not any(matches):
            return lyrics
        ends = [secs(m.group(3)) for m in matches if m]
        longest = max(ends)
        cap = int(cap_seconds)
        if longest <= cap:
            return lyrics
        factor = cap / longest
        out = []
        for ln, m in zip(lines, matches):
            if not m:
                out.append(ln)
                continue
            new_start = int(secs(m.group(2)) * factor)
            new_end = int(secs(m.group(3)) * factor)
            out.append(f"{m.group(1)} {fmt(new_start)}-{fmt(new_end)}{m.group(4)}")
        logger.info(
            "YuE2: lyrics requested %ds, compressing section times to ~%ds cap",
            longest, cap,
        )
        return "\n".join(out)

    # -- gguf (audio.cpp subprocess) -----------------------------------------
    def _audiocpp_binary(self) -> Path:
        import config

        if config.YUE2_AUDIOCPP_BIN:
            return Path(config.YUE2_AUDIOCPP_BIN)
        raise RuntimeError(
            "RURU_YUE2_AUDIOCPP_BIN is not set. Build audio.cpp (dev branch) "
            "and point RURU_YUE2_AUDIOCPP_BIN at the binary — see README "
            "'Music (YuE2)' section."
        )

    @staticmethod
    def _gguf_output_target(out_dir: Path) -> Path:
        return out_dir / "song.wav"

    def _generate_gguf(
        self, style, lyrics, seed, cot, cap_seconds: int | None
    ) -> TTSResult:
        import config

        model_dir = Path(config.YUE2_GGUF_DIR)
        if not (model_dir / GGUF_MAIN).is_file():
            raise RuntimeError(
                f"{model_dir / GGUF_MAIN} missing — run the GGUF download step "
                "in README 'Music (YuE2)'."
            )
        with tempfile.TemporaryDirectory(prefix="ruru_yue2_") as td:
            out = self._gguf_output_target(Path(td))
            cmd = [
                str(self._audiocpp_binary()),
                "--model", str(model_dir / GGUF_MAIN),
                "--vae", str(model_dir / GGUF_VAE),
                "--style", style,
                "--text", lyrics,
                "--request-option", f"cot={cot or config.YUE2_COT}",
                "--out", str(out),
            ]
            if seed is not None:
                cmd += ["--request-option", f"seed={seed}"]
            logger.info("YuE2 gguf: %s", " ".join(cmd))
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"audio.cpp failed ({proc.returncode}): {proc.stderr[-2000:]}"
                )
            return self._finish(out, cap_seconds=cap_seconds)

    def _read_result_wav(self, path: Path) -> TTSResult:
        wav, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if wav.ndim == 2:  # stereo -> fold to mono for TTSResult
            wav = wav.mean(axis=1)
        return TTSResult(wav, int(sr))

    def _finish(self, out_path: Path, cap_seconds: int | None) -> TTSResult:
        """Read a runtime's output wav and apply the effective length cap.

        All three runtimes end here: one read, stereo folded to mono, then an
        in-memory trim to ``cap_seconds * sr`` when a cap is set. No temp-file
        rewrite (the old path re-encoded the trim into a ``.flac``-named temp
        with WAV PCM data and re-read config instead of the effective cap).
        """
        wav, sr = self._read_result_wav(out_path)
        if cap_seconds is not None and len(wav) > cap_seconds * sr:
            wav = wav[: cap_seconds * sr]
        return TTSResult(wav, int(sr))

    # -- pytorch (yue2_infer wheel, optional LoRA) ----------------------------
    def _load_pytorch_pipeline(self, with_lora: bool):
        import config

        if self._pipe is not None and self._pipe_lora == with_lora:
            return self._pipe
        from yue2 import YuE2Pipeline

        self.unload()
        pipe = YuE2Pipeline.from_pretrained(config.YUE2_MODEL_REPO, device="cuda")
        if with_lora:
            self._apply_instrumental_lora(pipe)
        self._pipe = pipe
        self._pipe_lora = with_lora
        return pipe

    def _apply_instrumental_lora(self, pipe) -> None:
        """Fold the Mothersuperior AR LoRA into the base weights: W += scale*B@A.

        The proven path from the LoRA repo's own scripts/ (vendored in
        ``engine/yue2_lora.py``). Folding into the weights (rather than
        wrapping modules) keeps the pipeline's CUDA-graph sampler working.
        """
        import torch

        import config
        from engine.yue2_lora import load_ckpt, merge_ar_lora
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(
            config.YUE2_INST_LORA_REPO, "ar_lora_inst_v3abc.bf16.safetensors"
        )
        ckpt = load_ckpt(path, "cuda")
        scale = float(config.YUE2_INST_LORA_SCALE)
        model = pipe._load_model()      # exact call ar_generate.py uses
        bb = model.model                # backbone: .layers / .self_attn / .mlp
        with torch.no_grad():
            n = merge_ar_lora(bb, ckpt["lora"], scale=scale)
        logger.info(
            "YuE2: instrumental AR LoRA folded (%d projections, scale %s)", n, scale
        )

    def _generate_pytorch(
        self, style, lyrics, instrumental, seed, cot, cap_seconds: int | None
    ) -> TTSResult:
        import config

        pipe = self._load_pytorch_pipeline(with_lora=instrumental)
        gen_kwargs = {
            "style": style,
            "lyrics": lyrics,
            "cot": cot or config.YUE2_COT,
        }
        if seed is not None:
            # yue2's SongRequest rejects seed=None explicitly (default 831001);
            # only pass a seed when the user gave one.
            gen_kwargs["seed"] = seed
        with tempfile.TemporaryDirectory(prefix="ruru_yue2_") as td:
            song = pipe(**gen_kwargs)
            out = Path(td) / "song.flac"
            song.save(str(out))
            return self._finish(out, cap_seconds=cap_seconds)

    # -- mlx (macOS; no LoRA — verified) ---------------------------------------
    def _generate_mlx(
        self,
        style,
        lyrics,
        seed,
        cot,
        cfg_scale,
        steps,
        max_seconds,
        cap_seconds: int | None,
    ) -> TTSResult:
        import config

        if platform.system() != "Darwin":
            raise RuntimeError("MLX YuE2 runtime is macOS-only")
        mlx_dir = Path(config.MLX_MODELS_PATH) / "YuE2-3B-MLX"
        with tempfile.TemporaryDirectory(prefix="ruru_yue2_") as td:
            out = Path(td) / "song.wav"
            cmd = [
                # sys.executable, not "python": the launchd PATH has no bare
                # `python`, and the venv interpreter is the one holding mlx +
                # tiktoken that generate.py needs.
                sys.executable, str(mlx_dir / "generate.py"),
                "--model", "8bit", "--style", style, "--lyrics", lyrics,
                "--cot", cot or config.YUE2_COT, "--out", str(out),
            ]
            if seed is not None:
                cmd += ["--seed", str(seed)]
            if cfg_scale is not None:
                cmd += ["--cfg-scale", str(float(cfg_scale))]
            if steps is not None:
                cmd += ["--steps", str(int(steps))]
            if max_seconds is not None:
                # upstream generate.py decodes semantic tokens at 40 ms/frame
                cmd += ["--max-semantic-tokens", str(int(max_seconds * 25))]
            # cwd=mlx_dir: generate.py resolves the relative "--model 8bit"
            # against the subprocess cwd, not its own script dir.
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=3600, cwd=str(mlx_dir)
            )
            if proc.returncode != 0:
                raise RuntimeError(f"MLX YuE2 failed: {proc.stderr[-2000:]}")
            return self._finish(out, cap_seconds=cap_seconds)
