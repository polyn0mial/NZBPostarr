"""Page routes: every nav link, .html variant and legacy alias resolves; other webui/ files do not."""

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_mod
from api import deps as deps_api
from api import pages as pages_api
from tests.conftest import _read_repo_text, patch_hit


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    stats_on = SimpleNamespace(stats_page_enabled=True, dashboard_stats_enabled=True, dashboard_stats_modules=[])
    monkeypatch.setattr(deps_api, "get_config", lambda: stats_on)
    return TestClient(app_mod.app)


def _nav_links() -> list[str]:
    nav = re.findall(r"href: '(/[a-z-]*)'", _read_repo_text("webui", "assets", "js", "page-base.js"))
    shell = re.findall(r'href="(/(?:docs|indexer-guides))"', _read_repo_text("webui", "base.html"))
    links = sorted(set(nav) | set(shell))
    assert {"/", "/queue", "/history", "/stats", "/settings", "/docs", "/indexer-guides"} <= set(links)
    return links


def test_every_nav_link_and_html_variant_renders_a_page(client) -> None:
    for link in _nav_links():
        variants = ["/", "/index", "/index.html"] if link == "/" else [link, f"{link}.html"]
        for path in variants:
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 200, path
            assert response.headers["content-type"].startswith("text/html"), path


def test_every_declared_page_has_a_template() -> None:
    for name, template in pages_api.PAGES.items():
        assert _read_repo_text("webui", template), name


def test_legacy_page_aliases_redirect_permanently(client) -> None:
    cases = {
        "/pending": "/queue",
        "/pending.html": "/queue",
        "/uploads": "/history",
        "/uploads.html": "/history",
    }
    for path, destination in cases.items():
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 301, path
        assert response.headers["location"] == destination, path


@pytest.mark.parametrize(
    "path",
    ["/package.json", "/base.html", "/tailwind.config.cjs", "/error.html", "/login.html", "/robots", "/eslint.config.mjs"],
)
def test_non_page_files_under_webui_are_not_served(client, path) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 404, path


def test_stats_page_is_404_when_disabled(monkeypatch) -> None:
    stats_off = SimpleNamespace(stats_page_enabled=False, dashboard_stats_enabled=True, dashboard_stats_modules=["cpu"])
    patch_hit(monkeypatch, deps_api, "get_config", lambda: stats_off)
    client = TestClient(app_mod.app)

    for path in ("/stats", "/stats.html"):
        assert client.get(path, follow_redirects=False).status_code == 404, path
    assert client.get("/queue", follow_redirects=False).status_code == 200


def test_robots_txt_is_still_served(client) -> None:
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.text.startswith("User-agent: *")
