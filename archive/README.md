# Archived Files

These files have been replaced by the simplification (2026-05-05):

## Why These Were Archived

The previous architecture required:
- Redis server running separately
- Celery worker process for GPU jobs
- Complex setup scripts with Redis init

**New architecture (2026-05-05):**
- Single FastAPI process with in-process asyncio job runner
- No external dependencies (Redis, Celery removed)
- `uv sync` for dependency management
- Idempotent install script

## Files

| File | What it did |
|------|-------------|
| `setup.sh` | Full stack install with Redis, Celery, Nginx, systemd |
| `update.sh` | Pull latest code, restart services |
| `uninstall.sh` | Remove all components |
| `worker.py` | Celery worker entry point (GPU job processor) |

See `docs/uninstall.md` for teardown instructions.
See `install.sh` for the new simplified install.