"""Read webui source files for the source-text pins."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_repo_text(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _queue_source() -> str:
    """The queue page source as one string. queue.js was split into sibling ES modules
    (queue-methods-*.js / queue-computed-*.js) that esbuild bundles back together, so
    the page's logic now spans several files - read them all so source-text assertions
    still see the whole page."""
    pages = REPO_ROOT / "webui" / "assets" / "js" / "pages"
    parts = [pages / "queue.js"] + sorted(pages.glob("queue-*.js"))
    return "\n".join(p.read_text(encoding="utf-8") for p in parts)
