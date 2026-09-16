"""One-off smoke test: generate a ~30s instrumental lofi track via the
pytorch YuE2 runtime + instrumental LoRA on the RTX 3060.

Usage: uv run python smoke_yue2_lofi.py
Not a pytest test (no test_ prefix) — real GPU work.
"""
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

def main():
    import torch
    from engine.yue2_engine import YuE2Engine

    assert torch.cuda.is_available(), "CUDA not available"
    print(f"GPU: {torch.cuda.get_device_name(0)}")

    eng = YuE2Engine()
    t0 = time.time()
    result = eng.generate(
        style="lofi hip hop, warm vinyl crackle, mellow Rhodes piano, soft brushed drums, upright bass, relaxed, slow, 70 BPM",
        # Timed tags are the LoRA's strongest structural steer; 30s total.
        lyrics=(
            "[intro 0:00-0:05]\n"
            "[verse 0:05-0:15]\n"
            "[chorus 0:15-0:25]\n"
            "[outro 0:25-0:30]"
        ),
        instrumental=True,
        seed=21,
    )
    wav, sr = result
    dur = len(wav) / sr
    out = result.write("/home/lilith/repos/ruru/data/output/yue2_lofi_30s.wav")
    print(f"DONE in {time.time()-t0:.0f}s wall — {dur:.1f}s of audio @ {sr} Hz")
    print(f"Wrote {out}")
    print(f"Peak VRAM: {torch.cuda.max_memory_allocated()/2**30:.2f} GiB")

if __name__ == "__main__":
    main()
