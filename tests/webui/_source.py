"""Read webui source files for the source-text pins."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_repo_text(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _queue_source() -> str:
    """The queue page source as one string. The page is split into feature modules under
    pages/queue/ that esbuild bundles from pages/queue/index.js - read them all so
    source-text assertions still see the whole page."""
    pages = REPO_ROOT / "webui" / "assets" / "js" / "pages"
    parts = sorted((pages / "queue").glob("*.js"))
    return "\n".join(p.read_text(encoding="utf-8") for p in parts)
