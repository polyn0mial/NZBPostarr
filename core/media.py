"""Media category facts: the one table of category ids, indexer YAML keys, labels and display order."""

from dataclasses import dataclass
from typing import Dict, Tuple


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
