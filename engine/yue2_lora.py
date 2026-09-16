"""Vendored LoRA merge for YuE2.

Adapted from Mothersuperior/YuE2-instrumental-cot-full-loras scripts/
(ckpt_io.py + ar_generate.py): the AR-branch LoRA is folded into the base
Linear weights (``W += scale * B @ A``) so the pipeline's CUDA-graph sampler
keeps working. NAR-LoRA and vae2llm/llm2vae IO handling is intentionally
dropped — ruru uses the stock NAR decoder.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (module attr on each layer, projection names) in the checkpoint's
# layer-major order: self_attn q,k,v,o then mlp gate,up,down.
_AR_MODS = [
    ("self_attn", ("q_proj", "k_proj", "v_proj", "o_proj")),
    ("mlp", ("gate_proj", "up_proj", "down_proj")),
]


def load_ckpt(path, map_location="cpu"):
    """Load the AR LoRA checkpoint (.pt or .safetensors).

    Mirrors the upstream script's loader, AR branch only. Returns
    ``{"lora": [A0, B0, A1, B1, ...], "rank": int}`` in layer-major order.
    """
    import torch

    if not str(path).endswith(".safetensors"):
        return torch.load(path, map_location=map_location, weights_only=False)
    from safetensors import safe_open
    from safetensors.torch import load_file

    t = load_file(path, device=str(map_location))
    with safe_open(path, "pt") as f:
        meta = f.metadata() or {}
    layers = sorted({int(k.split(".")[1]) for k in t if k.startswith("layers.")})
    lora = []
    for L in layers:
        for blk_name, proj_names in _AR_MODS:
            for proj in proj_names:
                lora += [
                    t[f"layers.{L}.{blk_name}.{proj}.lora_A"],
                    t[f"layers.{L}.{blk_name}.{proj}.lora_B"],
                ]
    return {"lora": lora, "rank": int(meta.get("rank", lora[0].shape[0]))}


def merge_ar_lora(backbone, lora_tensors, scale: float = 1.0) -> int:
    """Fold [A0, B0, A1, B1, ...] (layer-major, checkpoint order) into weights.

    ``backbone`` is the AR backbone module exposing ``.layers``, each layer
    with ``.self_attn`` / ``.mlp`` and the named projections. Call inside
    ``torch.no_grad()``. Returns the number of projections merged (392 for
    the full 28-layer rank-64 checkpoint).
    """
    it = iter(lora_tensors)
    n_merged = 0
    for layer in backbone.layers:
        for mod_name, proj_names in _AR_MODS:
            mod = getattr(layer, mod_name)
            for n in proj_names:
                A = next(it).float()
                B = next(it).float()
                lin = getattr(mod, n)
                lin.weight.add_((scale * (B @ A)).to(lin.weight.dtype))
                n_merged += 1
    return n_merged
