"""Browser smoke test: every page loads without a page error or a console error.

The page scripts are ES modules bundled by esbuild. A name one module uses without
importing it survives the build as a free global and only fails in the browser with a
ReferenceError, which no source-text test sees. This test opens each page in Chromium
against a server started with a fixture config and DB and fails on any uncaught
exception or console.error.

It skips when the Playwright browsers are not installed; set NZBPOSTARR_REQUIRE_E2E=1
(CI does) to make that a failure instead.
"""

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest
import yaml

try:
    from playwright.sync_api import Page, sync_playwright
except ImportError:  # pragma: no cover - playwright is a dev dependency
    Page = None  # type: ignore[assignment,misc]
    sync_playwright = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVE_APP = Path(__file__).with_name("serve_app.py")
PAGES = ["/", "/queue", "/history", "/settings", "/stats", "/docs", "/indexer-guides"]
STARTUP_TIMEOUT_S = 90
SETTLE_MS = 3000


def _browsers_available() -> bool:
    if sync_playwright is None:
        return False
    try:
        with sync_playwright() as playwright:
            return Path(str(playwright.chromium.executable_path or "")).exists()
    except Exception:
        return False


# Checked at collection: once pytest-playwright's session is up, a second sync_playwright()
# cannot start. When the browsers are required, the test runs and its browser fixture fails.
pytestmark = pytest.mark.skipif(
    os.environ.get("NZBPOSTARR_REQUIRE_E2E") != "1" and not _browsers_available(),
    reason="Playwright browsers are not installed",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_fixture_config(root: Path) -> Path:
    defaults = yaml.safe_load((REPO_ROOT / "core" / "config.defaults.yaml").read_text(encoding="utf-8"))
    media = root / "media"
    release = media / "Movies" / "Example.Movie.2020.1080p.WEB-DL.x264-GRP"
    release.mkdir(parents=True)
    (release / "Example.Movie.2020.1080p.WEB-DL.x264-GRP.mkv").write_bytes(b"\0" * 1024)
    (media / "TV").mkdir()
    (media / "Misc").mkdir()

    defaults.update(
        base_folder=media.as_posix(),
        backup_folder=(root / "backups").as_posix(),
        enable_password=False,
    )
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(defaults, sort_keys=False), encoding="utf-8")
    return path


def _wait_until_serving(url: str, server: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"the app exited during startup with code {server.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"the app did not answer {url} within {STARTUP_TIMEOUT_S}s")


@pytest.fixture(scope="module")
def app_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("e2e_app")
    env = {key: value for key, value in os.environ.items() if not key.startswith("NZBP_")}
    env["NZBPOSTARR_CONFIG"] = str(_write_fixture_config(root))
    port = _free_port()
    log_path = root / "server.log"
    with open(log_path, "wb") as log:
        server = subprocess.Popen(
            [sys.executable, str(SERVE_APP), str(root / "state"), str(port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        try:
            _wait_until_serving(url + "/", server)
        except RuntimeError as exc:
            pytest.fail(f"{exc}\n{log_path.read_text(encoding='utf-8', errors='replace')[-4000:]}")
        yield url
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=30)


@pytest.mark.parametrize("path", PAGES)
def test_page_loads_without_console_errors(app_url: str, page: Page, path: str) -> None:
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    page.on(
        "console",
        lambda message: problems.append(f"console.error: {message.text} ({message.location})")
        if message.type == "error"
        else None,
    )

    response = page.goto(app_url + path, wait_until="load", timeout=60000)
    assert response is not None and response.status == 200, f"{path} answered {response and response.status}"
    # createVuePage publishes the mounted instance; the pages poll and stream, so the
    # network never goes idle. Give the first API round trips time to land after mount.
    page.wait_for_function("() => !!window.nzbVue", timeout=60000)
    page.wait_for_timeout(SETTLE_MS)

    assert problems == [], f"{path}:\n" + "\n".join(problems)
