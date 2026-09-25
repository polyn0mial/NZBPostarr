"""Media vocabulary: categories, item-type labels and file-extension sets.

One owner for the words the queue, the processor, the stream monitor and the classifier
share. Persisted ``uploads.itype`` strings differ by writer, so each writer keeps its own
dialect of the item-type table below; the strings are byte-identical to what each wrote
before the table existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True, slots=True)
class MediaCategory:
    """One system category: internal id, the indexer YAML key that maps to it, its label and sort order."""

    id: str
    key: str
    label: str
    order: int


CATEGORIES: Tuple[MediaCategory, ...] = (
    MediaCategory("movies", "movie", "Movies", 0),
    MediaCategory("tv", "tv", "TV Shows", 1),
    MediaCategory("anime", "anime", "Anime", 2),
    MediaCategory("disc", "disc", "DISC", 3),
    MediaCategory("music", "music", "Music", 4),
    MediaCategory("audiobooks", "audiobooks", "Audiobooks", 5),
    MediaCategory("books", "books", "Books", 6),
    MediaCategory("apps", "apps", "Apps", 7),
    MediaCategory("misc", "misc", "Misc", 8),
)

CATEGORIES_BY_KEY: Dict[str, MediaCategory] = {cat.key: cat for cat in CATEGORIES}

_SUBMISSION_CATEGORY_ALIASES = {
    "movie": "movies",
    "movies": "movies",
    "tv": "tv",
    "show": "tv",
    "shows": "tv",
    "tv show": "tv",
    "tv episode": "tv",
    "episode": "tv",
    "anime": "anime",
    "music": "music",
    "book": "books",
    "books": "books",
    "audiobook": "audiobooks",
    "audiobooks": "audiobooks",
    "ebook": "books",
    "ebooks": "books",
    "app": "apps",
    "apps": "apps",
    "game": "apps",
    "games": "apps",
    "misc": "misc",
    "other": "misc",
}


def normalize_category(raw: Any) -> str:
    """Normalize user-, queue-, and scanner-facing category aliases."""
    cleaned = " ".join(str(raw or "").strip().lower().split())
    return _SUBMISSION_CATEGORY_ALIASES.get(cleaned, cleaned)


_ITYPE_TO_CATEGORY = {
    "tv show": "tv",
    "tv episode": "tv",
    "tv pack": "tv",
    "season pack": "tv",
    "anime": "anime",
    "movie": "movies",
    "movie pack": "movies",
    "music": "music",
    "audiobook": "audiobooks",
    "ebook": "books",
    "app": "apps",
    "disc": "disc",
    "misc": "misc",
    "unknown": "misc",
}


def category_for_itype(itype: str, default: str = "misc") -> str:
    """Map a display item type to its canonical submission category."""
    return _ITYPE_TO_CATEGORY.get(str(itype or "").strip().lower(), default)


# Item-type labels per writer dialect. "tv" is not in the tables: it depends on whether the
# item is a season pack / directory (processing, default) and is handled by each function.
_ITYPE_DIALECTS: Dict[str, Dict[str, str]] = {
    # logic/processing: the DB-facing label of a processed queue item.
    "processing": {
        "movies": "Movies",
        "anime": "Anime",
        "disc": "DISC",
        "music": "Music",
        "books": "Books",
        "apps": "Apps",
    },
    # Uploads with no classifier verdict (queue API, pending selection).
    "default": {
        "movies": "Movie",
        "anime": "Anime",
        "music": "Music",
        "audiobooks": "Audiobook",
        "books": "Ebook",
        "apps": "App",
    },
    # The usenet stream monitor.
    "stream": {
        "movies": "Movie",
        "tv": "TV Show",
    },
}


def processing_itype(category: str, *, season_pack: bool) -> str:
    """Item type the processor records for a routed queue category."""
    if category == "tv":
        return "TV Show" if season_pack else "TV Episode"
    return _ITYPE_DIALECTS["processing"].get(category, "Misc")


def default_itype(category: str, *, is_dir: bool) -> str:
    """Item type an upload of this category is posted with when no classifier verdict applies."""
    normalized = str(category or "").strip().lower()
    if normalized == "tv":
        return "TV Show" if is_dir else "TV Episode"
    return _ITYPE_DIALECTS["default"].get(normalized, "Misc")


def stream_itype(category: str) -> str:
    """Item type the stream monitor records for a category."""
    normalized = str(category or "misc").strip().lower()
    label = _ITYPE_DIALECTS["stream"].get(normalized)
    if label is not None:
        return label
    return normalized.replace("_", " ").title() or "Misc"


VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".ts",
    ".m2ts",
    ".mov",
    ".m4v",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".flv",
    ".vob",
    ".webm",
    ".ogv",
    ".ogm",
}

MUSIC_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".aac",
    ".wav",
    ".m4a",
    ".ogg",
    ".wma",
    ".ape",
    ".opus",
    ".aiff",
    ".alac",
    ".dsf",
    ".dff",
    ".mka",
}

EBOOK_EXTENSIONS = {
    ".epub",
    ".mobi",
    ".azw",
    ".azw3",
    ".cbr",
    ".cbz",
    ".lit",
    ".djvu",
    ".pdf",
}

AUDIOBOOK_EXTENSIONS = {
    ".m4b",
}


# Archive and installer suffixes the processor counts as application content.
APP_EXTENSIONS = {
    ".7z",
    ".apk",
    ".bat",
    ".bin",
    ".deb",
    ".dmg",
    ".exe",
    ".img",
    ".ipa",
    ".iso",
    ".msi",
    ".pkg",
    ".rar",
    ".rpm",
    ".tar",
    ".tbz2",
    ".tgz",
    ".xz",
    ".zip",
}

DISC_IMAGE_EXTENSIONS = {".iso", ".img", ".mdf", ".mds", ".nrg"}

DISC_STRUCTURE_DIRS = {"bdmv", "certificate", "video_ts"}

# Extension-first subsets: narrower than the sets above on purpose (the Pending page's
# standalone-file rule and the classifier's extension-first pass count only these).
EXTENSION_FIRST_APP = {
    ".exe",
    ".msi",
    ".apk",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
    ".zip",
    ".rar",
    ".7z",
} | DISC_IMAGE_EXTENSIONS

EXTENSION_FIRST_VIDEO = {
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".m4v",
    ".wmv",
    ".ts",
    ".m2ts",
    ".mpg",
    ".mpeg",
    ".webm",
    ".flv",
}
EXTENSION_FIRST_AUDIOBOOK = {".m4b"}
EXTENSION_FIRST_MUSIC = {".m4a", ".mp3", ".flac", ".cue"}
EXTENSION_FIRST_EBOOK = {".epub", ".pdf", ".mobi"}
