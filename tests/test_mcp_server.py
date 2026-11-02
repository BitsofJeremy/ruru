"""Tests for the MCP server's stdlib HTTP-client layer (mcp_server.py).

The module must be importable WITHOUT the `mcp` package (build_server()
imports the server class lazily), so these tests target the module-level
resolve_*/api_* functions with urllib + subprocess monkeypatched — no
sockets, no afplay.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

import mcp_server


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """No env override, no shared cache file, no live-host memo between tests."""
    monkeypatch.delenv("RURU_URL", raising=False)
    monkeypatch.setattr(mcp_server, "CACHE_FILE", tmp_path / "base-cache")
    monkeypatch.setattr(mcp_server, "_live_base", None)


@pytest.fixture
def http(monkeypatch):
    """Swap urllib.request.urlopen for a recording fake.

    install(handler) returns the call list; handler(full_url, request) returns
    a response-like object (or raises). Requests sent via urllib.request.Request
    expose .full_url/.get_method()/.data; bare-string URLs log method "GET".
    """
    calls = []

    def install(handler):
        def fake_urlopen(url, timeout=None):
            req = url if isinstance(url, urllib.request.Request) else None
            full = req.full_url if req is not None else url
            calls.append({
                "url": full,
                "method": req.get_method() if req is not None else "GET",
                "data": req.data if req is not None else None,
                "timeout": timeout,
            })
            return handler(full, req)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        return calls

    return install


class JsonResp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class BytesResp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.payload


def payload_of(call):
    return json.loads(call["data"])


class TestResolveBaseUrl:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("RURU_URL", "http://pinned:9999/")
        assert mcp_server.resolve_base_url() == "http://pinned:9999"

    def test_fresh_cache_hit(self, monkeypatch, tmp_path):
        cache = tmp_path / "base-cache"
        cache.write_text("http://cached:5151\n")
        monkeypatch.setattr(mcp_server, "CACHE_FILE", cache)
        assert mcp_server.resolve_base_url() == "http://cached:5151"

    def test_probe_picks_first_healthy_host_and_caches(self, monkeypatch, tmp_path):
        cache = tmp_path / "base-cache"
        monkeypatch.setattr(mcp_server, "CACHE_FILE", cache)
        monkeypatch.setattr(
            mcp_server, "_probe_health",
            lambda url: url == "http://ruru.nexus.home.test",
        )
        assert mcp_server.resolve_base_url() == "http://ruru.nexus.home.test"
        assert cache.read_text().strip() == "http://ruru.nexus.home.test"

    def test_no_host_reachable_raises(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "_probe_health", lambda url: False)
        with pytest.raises(RuntimeError, match="no ruru host reachable"):
            mcp_server.resolve_base_url()


class TestResolveLiveBase:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("RURU_URL", "http://pinned:9999")
        assert mcp_server.resolve_live_base() == "http://pinned:9999"

    def test_skips_unavailable_picks_available(self, http):
        def handler(url, req):
            if url == "http://localhost:5151/api/tts/live/voices":
                return JsonResp({"available": False, "voices": []})
            assert url == "http://ruru.nexus.home.test/api/tts/live/voices"
            return JsonResp({"available": True, "voices": [{"name": "Hugh"}]})

        calls = http(handler)
        assert mcp_server.resolve_live_base() == "http://ruru.nexus.home.test"
        assert len(calls) == 2

    def test_choice_is_memoized(self, http):
        calls = http(lambda url, req: JsonResp({"available": True, "voices": []}))
        assert mcp_server.resolve_live_base() == "http://localhost:5151"
        assert mcp_server.resolve_live_base() == "http://localhost:5151"
        assert len(calls) == 1

    def test_unreachable_host_falls_through(self, http):
        def handler(url, req):
            if url.startswith("http://localhost:5151"):
                raise urllib.error.URLError("connection refused")
            return JsonResp({"available": True, "voices": []})

        calls = http(handler)

        assert mcp_server.resolve_live_base() == "http://ruru.nexus.home.test"
        assert len(calls) == 2

    def test_no_live_host_raises(self, http):
        http(lambda url, req: JsonResp({"available": False, "voices": []}))
        with pytest.raises(RuntimeError, match="no ruru host with live TTS"):
            mcp_server.resolve_live_base()


class TestApiSpeak:
    def test_posts_live_payload_and_plays(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_live_base", lambda: "http://loc:5151")
        monkeypatch.setattr(sys, "platform", "darwin")
        plays = []

        def fake_run(cmd, **kwargs):
            plays.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)
        calls = http(lambda url, req: BytesResp(b"RIFF-fake-wav"))

        result = mcp_server.api_speak("hello there", voice="Hugh")

        assert result == "played 13 bytes of live WAV (voice: Hugh)"
        assert payload_of(calls[0]) == {"text": "hello there", "voice": "Hugh", "format": "wav"}
        assert calls[0]["timeout"] == mcp_server.LIVE_TIMEOUT
        assert plays and plays[0][0] == "afplay" and plays[0][1].endswith(".wav")
        assert not os.path.exists(plays[0][1])  # temp WAV removed after playback

    def test_play_false_keeps_temp_file(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_live_base", lambda: "http://loc:5151")
        runs = []
        monkeypatch.setattr(
            mcp_server.subprocess, "run",
            lambda cmd, **kwargs: runs.append(cmd),
        )
        http(lambda url, req: BytesResp(b"RIFF-fake-wav"))

        result = mcp_server.api_speak("hello", play=False)

        path = result.split("to ")[1]
        assert os.path.exists(path)
        with open(path, "rb") as f:
            assert f.read() == b"RIFF-fake-wav"
        os.unlink(path)
        assert runs == []  # play=False never touches the speakers

    def test_non_darwin_saves_without_playing(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_live_base", lambda: "http://loc:5151")
        monkeypatch.setattr(sys, "platform", "linux")
        runs = []
        monkeypatch.setattr(
            mcp_server.subprocess, "run",
            lambda cmd, **kwargs: runs.append(cmd),
        )
        http(lambda url, req: BytesResp(b"RIFF-fake-wav"))

        result = mcp_server.api_speak("hello")

        assert result.startswith("saved WAV")
        assert runs == []

    def test_http_error_propagates_to_tool_wrapper(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_live_base", lambda: "http://loc:5151")

        def raise_503(url, req):
            raise urllib.error.HTTPError(url, 503, "feature disabled", {}, None)

        http(raise_503)
        with pytest.raises(urllib.error.HTTPError):
            mcp_server.api_speak("hello")


class TestApiGenerate:
    def test_payload_shape_and_absolute_url(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_base_url", lambda: "http://loc:5151")
        calls = http(lambda url, req: JsonResp(
            {"job_id": 7, "status": "completed", "audio_url": "/audio/output/tts_7.wav"}))

        result = mcp_server.api_generate("hi", voice_id=12)

        assert payload_of(calls[0]) == {"text": "hi", "voice_id": 12, "audio_format": "mp3"}
        assert result["audio_url"] == "http://loc:5151/audio/output/tts_7.wav"


class TestJobsAndHealth:
    def test_job_status_get(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_base_url", lambda: "http://loc:5151")
        calls = http(lambda url, req: JsonResp({"id": 7, "status": "completed"}))

        assert mcp_server.api_job_status(7) == {"id": 7, "status": "completed"}
        assert calls[0]["url"] == "http://loc:5151/api/jobs/7"
        assert calls[0]["method"] == "GET"

    def test_delete_job(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_base_url", lambda: "http://loc:5151")
        calls = http(lambda url, req: JsonResp({"status": "deleted"}))

        assert mcp_server.api_delete_job(9) == "job 9 deleted"
        assert calls[0]["method"] == "DELETE"
        assert calls[0]["url"] == "http://loc:5151/api/jobs/9"

    def test_health_and_voices_paths(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_base_url", lambda: "http://loc:5151")
        calls = http(lambda url, req: JsonResp({}))

        mcp_server.api_health()
        mcp_server.api_list_voices()

        assert [c["url"] for c in calls] == [
            "http://loc:5151/health",
            "http://loc:5151/api/voices",
        ]

    def test_list_live_voices_uses_live_host(self, monkeypatch, http):
        monkeypatch.setattr(mcp_server, "resolve_live_base", lambda: "http://live:5151")
        calls = http(lambda url, req: JsonResp({"available": True, "voices": []}))

        mcp_server.api_list_live_voices()

        assert calls[0]["url"] == "http://live:5151/api/tts/live/voices"


class TestMain:
    def test_missing_mcp_extra_exits_1(self, monkeypatch):
        def boom():
            raise ImportError("No module named 'mcp'")

        monkeypatch.setattr(mcp_server, "build_server", boom)
        with pytest.raises(SystemExit) as exc:
            mcp_server.main()
        assert exc.value.code == 1
