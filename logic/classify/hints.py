"""Category hints from folder and entry names."""

from __future__ import annotations

import re
from pathlib import Path

from logic.classify.patterns import _ANIME_SEASONAL_FOLDER_RE


_FOLDER_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("audiobooks", ("audiobook", "audiobooks", "audio book", "audio books", "podcast", "podcasts", "m4b")),
    ("books", ("ebook", "ebooks", "book", "books", "comic", "comics", "manga", "pdf", "epub")),
    ("music", ("music", "album", "albums", "flac", "mp3", "discography")),
    ("apps", ("app", "apps", "game", "games", "software", "program", "programs")),
    ("anime", ("anime", "animes", "seasonal")),
    ("tv", ("tv", "show", "shows", "series", "season", "television")),
    ("movies", ("movie", "movies", "film", "films")),
)

def infer_folder_category_hint(folder: Path | str | None) -> str:
    """Infer a weak category bias from a configured root folder name."""
    if not folder:
        return ""

    try:
        parts = Path(str(folder)).parts[-3:]
    except (TypeError, ValueError):
        parts = ()

    for raw_part in reversed(parts):
        normalized = re.sub(r"[^a-z0-9]+", " ", str(raw_part).lower()).strip()
        if not normalized:
            continue
        tokens = set(normalized.split())
        for category, keywords in _FOLDER_CATEGORY_HINTS:
            if any(
                keyword in tokens
                or bool(re.search(rf"(?:^|\s){re.escape(keyword)}(?:$|\s)", normalized))
                for keyword in keywords
            ):
                return category
        if _ANIME_SEASONAL_FOLDER_RE.search(normalized):
            return "anime"
    return ""

def _coerce_category_hint(raw: str) -> str:
    normalized = str(raw or "").strip().lower()
    return {
        "movie": "movies",
        "movies": "movies",
        "tv": "tv",
        "anime": "anime",
        "books": "books",
        "ebooks": "books",
        "ebook": "books",
        "audiobook": "audiobooks",
        "audiobooks": "audiobooks",
        "music": "music",
        "apps": "apps",
    }.get(normalized, "")

def _hint_category_from_itype(raw: str) -> str:
    normalized = str(raw or "").strip().lower()
    return {
        "movie": "movies",
        "movies": "movies",
        "tv show": "tv",
        "tv episode": "tv",
        "anime": "anime",
        "music": "music",
        "ebook": "books",
        "audiobook": "audiobooks",
        "app": "apps",
    }.get(normalized, "")

def _entry_hint_text(entry: Path) -> str:
    try:
        parts = entry.parts[-6:]
    except (TypeError, ValueError):
        parts = (str(entry),)
    return " ".join(str(part) for part in parts)

def infer_entry_category_hint(entry: Path, explicit_hint: str = "", itype_hint: str = "") -> str:
    """Infer the weakest-possible category hint from path structure and UI metadata."""
    for hinted in (_coerce_category_hint(explicit_hint), _hint_category_from_itype(itype_hint)):
        if hinted:
            return hinted

    parts = []
    try:
        base = entry.parent
        parts = list(base.parts[-5:])
    except OSError:
        parts = []

    for raw_part in reversed(parts):
        hinted = infer_folder_category_hint(raw_part)
        if hinted:
            return hinted
    return ""
