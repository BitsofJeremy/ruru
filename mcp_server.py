"""ruru MCP server — exposes a running ruru instance as MCP tools (stdio).

Thin stdlib HTTP client in front of ruru's JSON API, built for Claude Code
(`claude mcp add --scope user ruru -- <venv>/bin/ruru-mcp`). The module-level
api_* helpers import nothing beyond the stdlib so they stay testable without
the `mcp` package; build_server() imports the server class lazily
(mcp 2.x MCPServer, falling back to 1.x FastMCP).

Host resolution mirrors ~/.claude/hooks/utils/tts/qwen_tts_hook.py:

    RURU_URL override -> /tmp/ruru-base-cache (5-min sliding affinity)
    -> ~/.claude/scripts/ruru-hosts.conf -> built-in probe chain.

Phonon live tools need a host whose /api/tts/live/voices reports
available:true — the desktop-first Qwen chain is NOT safe for them (the CUDA
box runs no ptts), so resolve_live_base() walks a localhost-first chain and
availability-checks every candidate.

All logging goes to stderr: the stdio transport owns stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Desktop-first chain for the plain API (the Qwen backend runs on both hosts);
# kept in sync with ~/.claude/scripts/ruru-hosts.conf, which wins when readable.
DEFAULT_HOSTS = ["http://ruru.nexus.home.test", "http://localhost:5151"]
# Localhost-first chain for Phonon live — only some hosts run ptts, so each
# candidate is availability-checked before use (see resolve_live_base()).
LIVE_HOSTS = ["http://localhost:5151", "http://ruru.nexus.home.test"]

HOSTS_FILE = Path(
    os.environ.get("RURU_HOSTS_FILE", str(Path.home() / ".claude" / "scripts" / "ruru-hosts.conf"))
)
CACHE_FILE = Path(tempfile.gettempdir()) / "ruru-base-cache"
CACHE_TTL = 300  # seconds; sliding — a cache hit touches the file
HEALTH_TIMEOUT = 3  # hard cap on a single probe
HTTP_TIMEOUT = 300  # first Qwen generate loads the MLX model; give it room
LIVE_TIMEOUT = 120  # phonon is ~6x realtime; generous

_live_base: str | None = None  # in-process cache of the live-capable host


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _load_hosts() -> list[str]:
    """Read the shared host list; fall back to the built-in chain."""
    try:
        lines = [
            ln.strip()
            for ln in HOSTS_FILE.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        if lines:
            return lines
    except OSError:
        pass
    return DEFAULT_HOSTS


def _read_cache() -> str | None:
    """Return a still-fresh cached base URL, or None on miss/staleness/corruption."""
    try:
        url = CACHE_FILE.read_text().splitlines()[0].strip()
        age = time.time() - CACHE_FILE.stat().st_mtime
        if url.startswith("http") and age < CACHE_TTL:
            os.utime(CACHE_FILE, None)  # slide the window, matching ruru-base.sh
            return url
    except Exception:  # noqa: BLE001 — any problem is simply a cache miss
        pass
    return None


def _write_cache(url: str) -> None:
    """Atomically record the winning host so bash callers share the affinity window."""
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=str(CACHE_FILE.parent), delete=False, suffix=".tmp"
        )
        with tmp:
            tmp.write(url + "\n")
        os.replace(tmp.name, CACHE_FILE)
    except Exception as e:  # noqa: BLE001 — caching is best-effort
        _log(f"ruru base cache write failed (non-fatal): {e}")


def _probe_health(url: str) -> bool:
    """Fast, hang-proof health check.

    Uses curl rather than urllib on purpose: socket-level timeouts don't
    reliably bound getaddrinfo, so a wedged DNS resolver could stall the
    server startup. curl's --connect-timeout covers name resolution, and the
    subprocess timeout is a hard Python-level backstop on top of it.
    """
    try:
        return subprocess.run(
            ["curl", "-sf", "-o", "/dev/null",
             "--connect-timeout", "1", "--max-time", "2", f"{url}/health"],
            capture_output=True, timeout=HEALTH_TIMEOUT,
        ).returncode == 0
    except Exception:  # noqa: BLE001 — probe failure (incl. no curl) means unhealthy
        return False


def resolve_base_url() -> str:
    """RURU_URL override -> fresh cache -> probe the chain. Raises if none answer."""
    override = os.environ.get("RURU_URL")
    if override:
        return override.rstrip("/")

    cached = _read_cache()
    if cached:
        return cached

    hosts = _load_hosts()
    for host in hosts:
        host = host.rstrip("/")
        if _probe_health(host):
            _write_cache(host)
            return host

    raise RuntimeError(f"no ruru host reachable (tried: {', '.join(hosts)})")


def resolve_live_base() -> str:
    """RURU_URL override, else the first host whose live endpoint reports available."""
    global _live_base
    override = os.environ.get("RURU_URL")
    if override:
        return override.rstrip("/")
    if _live_base:
        return _live_base

    for host in LIVE_HOSTS:
        host = host.rstrip("/")
        try:
            catalog = _get_json(f"{host}/api/tts/live/voices", timeout=HEALTH_TIMEOUT + 2)
            if isinstance(catalog, dict) and catalog.get("available"):
                _live_base = host
                return host
        except Exception:  # noqa: BLE001 — unreachable or disabled: try the next host
            continue
    raise RuntimeError(f"no ruru host with live TTS available (tried: {', '.join(LIVE_HOSTS)})")


def _get_json(url: str, timeout: int = HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _post_json(url: str, payload: dict, timeout: int = HTTP_TIMEOUT):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _post_wav(url: str, payload: dict, timeout: int = LIVE_TIMEOUT) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Accept": "audio/wav"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def api_speak(text: str, voice: str = "Hugh", play: bool = True) -> str:
    """Synthesize (and optionally play) short text via Phonon live TTS.

    Nothing is persisted server-side; the WAV is written to a temp file that
    is removed after playback (kept when play=False).
    """
    base = resolve_live_base()
    wav = _post_wav(f"{base}/api/tts/live", {"text": text, "voice": voice, "format": "wav"})
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="ruru-live-")
    with os.fdopen(fd, "wb") as f:
        f.write(wav)
    if play and sys.platform == "darwin":
        try:
            subprocess.run(["afplay", path], check=True)
        finally:
            os.unlink(path)
        return f"played {len(wav)} bytes of live WAV (voice: {voice})"
    return f"saved WAV ({len(wav)} bytes) to {path}"


def api_generate(text: str, voice_id: int, audio_format: str = "mp3") -> dict:
    """Job-based Qwen synthesis; blocks until the single clip is done."""
    base = resolve_base_url()
    resp = _post_json(
        f"{base}/api/tts/generate",
        {"text": text, "voice_id": voice_id, "audio_format": audio_format},
    )
    if isinstance(resp, dict) and resp.get("audio_url"):
        resp["audio_url"] = f"{base}{resp['audio_url']}"
    return resp


def api_job_status(job_id: int) -> dict:
    return _get_json(f"{resolve_base_url()}/api/jobs/{job_id}")


def api_delete_job(job_id: int) -> str:
    req = urllib.request.Request(f"{resolve_base_url()}/api/jobs/{job_id}", method="DELETE")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        resp.read()
    return f"job {job_id} deleted"


def api_health() -> dict:
    return _get_json(f"{resolve_base_url()}/health")


def api_list_voices() -> list:
    return _get_json(f"{resolve_base_url()}/api/voices")


def api_list_live_voices() -> dict:
    return _get_json(f"{resolve_live_base()}/api/tts/live/voices")


def build_server():
    try:  # mcp 2.x renamed FastMCP -> MCPServer (same decorator/run surface)
        from mcp.server.mcpserver import MCPServer as _Server
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as _Server

    mcp = _Server("ruru")

    @mcp.tool()
    def speak(text: str, voice: str = "Hugh", play: bool = True) -> str:
        """Speak short text out loud instantly via ruru's Phonon live TTS (CPU,
        roughly 6x realtime, nothing is saved). voice is a canned catalog name —
        default Hugh; see list_live_voices(). play=False skips the speakers and
        returns the temp WAV path instead of playing it."""
        try:
            return api_speak(text, voice, play)
        except Exception as e:  # noqa: BLE001 — tool output, not a crashed server
            return f"error: {e}"

    @mcp.tool()
    def generate_tts(text: str, voice_id: int, audio_format: str = "mp3") -> str:
        """Synthesize a short clip with a cloned/designed Qwen voice (job API;
        blocks until the single clip is done — slow on a cold model load).
        voice_id comes from list_voices(). Returns job JSON with an absolute
        audio_url."""
        try:
            return json.dumps(api_generate(text, voice_id, audio_format))
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    @mcp.tool()
    def list_voices() -> str:
        """List ruru's cloned/designed voices: [{id, name, slug, voice_type}]."""
        try:
            return json.dumps(api_list_voices())
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    @mcp.tool()
    def list_live_voices() -> str:
        """List Phonon live canned voices:
        {available, voices: [{name, voice_id, language, description}]}."""
        try:
            return json.dumps(api_list_live_voices())
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    @mcp.tool()
    def job_status(job_id: int) -> str:
        """Fetch {id, status, progress, audio_url} for a TTS job id."""
        try:
            return json.dumps(api_job_status(job_id))
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    @mcp.tool()
    def delete_job(job_id: int) -> str:
        """Delete a finished TTS job and its audio from ruru."""
        try:
            return api_delete_job(job_id)
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    @mcp.tool()
    def health() -> str:
        """ruru instance health: {status, version}."""
        try:
            return json.dumps(api_health())
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    return mcp


def main() -> None:
    try:
        server = build_server()
    except ImportError as e:
        _log(f"ruru-mcp requires the 'mcp' extra: uv sync --extra mcp ({e})")
        raise SystemExit(1)
    server.run()  # stdio transport (default)


if __name__ == "__main__":
    main()
