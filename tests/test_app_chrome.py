"""The sidebar chrome in base.html: version string and backend label.

`app_version` was referenced by the template but passed by no route, so it
rendered blank on every page. Both values are Jinja globals now; these tests
guard against them silently going empty again.
"""
import re
from pathlib import Path

import pytest

import config
from engine import get_backend_label


class TestAppVersion:
    def test_matches_pyproject(self):
        """config.APP_VERSION is single-sourced from pyproject.toml."""
        text = (Path(__file__).parent.parent / "pyproject.toml").read_text()
        declared = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)
        assert config.APP_VERSION == declared

    def test_is_a_real_version_not_a_placeholder(self):
        assert config.APP_VERSION not in ("", "0.0.0")
        assert re.fullmatch(r"\d+\.\d+\.\d+", config.APP_VERSION)


class TestBackendLabel:
    @pytest.mark.parametrize("backend,expected", [("mlx", "MLX"), ("cuda", "CUDA")])
    def test_known_backends(self, backend, expected):
        assert get_backend_label(backend) == expected

    def test_follows_configured_backend(self, monkeypatch):
        """The Mac must say MLX and the CUDA box must say CUDA."""
        monkeypatch.setattr(config, "TTS_BACKEND", "cuda")
        assert get_backend_label() == "CUDA"
        monkeypatch.setattr(config, "TTS_BACKEND", "mlx")
        assert get_backend_label() == "MLX"

    def test_never_renders_empty(self):
        """A blank label would leave a stray separator in the sidebar."""
        for value in ("", "  ", None, "weird"):
            assert get_backend_label(value).strip()


class TestSidebarRenders:
    def test_version_and_backend_appear_in_rendered_html(self, seeded_db, client):
        """End to end: the values actually reach the page."""
        html = client.get("/").text
        assert f"v{config.APP_VERSION}" in html
        assert f"{get_backend_label()} &middot; Audiobook" in html or \
               f"{get_backend_label()} · Audiobook" in html
