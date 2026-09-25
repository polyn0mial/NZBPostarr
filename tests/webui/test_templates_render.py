"""Every page template, rendered through the app's Jinja env, must match its committed baseline.

The baseline pins the rendered HTML while templates move into partials: a split that changes a
single byte of output shows up here. Regenerate on purpose with
    python -m tests.webui.test_templates_render
and review the diff of tests/webui/render_baseline/ before committing it.
"""

from pathlib import Path
from typing import Any

import pytest

from api.assets import WEBUI_ROOT, templates

BASELINE_DIR = Path(__file__).resolve().parent / "render_baseline"

# Globals that change per run or read the live config, pinned so the render is reproducible.
FIXED_CONTEXT: dict[str, Any] = {
    "cache_bust": "0",
    "app_version": "0.0.0",
    "auth_enabled": lambda: False,
}

PAGE_CONTEXT: dict[str, dict[str, Any]] = {
    "error.html": {
        "status_code": 404,
        "error_title": "Page Not Found",
        "error_message": "Oops! The page you're looking for doesn't exist.",
    },
    "login.html": {"error": "Invalid password", "next_url": "/queue"},
}


def _page_templates() -> list[str]:
    return sorted(path.name for path in WEBUI_ROOT.glob("*.html") if path.name != "base.html")


def _render(name: str) -> str:
    context = {**FIXED_CONTEXT, **PAGE_CONTEXT.get(name, {})}
    return templates.env.get_template(name).render(context)


def _read_baseline(name: str) -> str:
    return (BASELINE_DIR / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize("name", _page_templates())
def test_page_template_renders_like_the_baseline(name: str) -> None:
    assert (BASELINE_DIR / name).is_file(), f"no render baseline for {name}"
    assert _render(name) == _read_baseline(name), f"{name} renders differently from its baseline"


def test_every_baseline_has_a_page_template() -> None:
    assert sorted(path.name for path in BASELINE_DIR.glob("*.html")) == _page_templates()


if __name__ == "__main__":
    BASELINE_DIR.mkdir(exist_ok=True)
    for page in _page_templates():
        (BASELINE_DIR / page).write_bytes(_render(page).encode("utf-8"))
