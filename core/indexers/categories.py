"""Category discovery across loaded indexers and submission category resolution.

Ids, labels and order come from core.media.CATEGORIES; presentation lives in the browser.
"""

from typing import Any, Dict, List, Optional


from core.indexers.models import CategoryMapping, IndexerDefinition
from core.indexers import registry
from core.media import CATEGORIES, CATEGORIES_BY_KEY


def _cat_key_to_id(key: str) -> str:
    """Convert an indexer YAML category key to the internal system category ID."""
    known = CATEGORIES_BY_KEY.get(key)
    return known.id if known else key


def get_available_categories() -> List[Dict[str, Any]]:
    """Discover available categories from all loaded indexer YAML definitions.

    Returns a list of category dicts, each with:
        id       - internal system name (e.g. 'movies', 'tv', 'misc')
        key      - indexer YAML key (e.g. 'movie', 'tv', 'misc')
        label    - human-readable label
        order    - display order (known categories first, custom ones after, alphabetically)
        indexers - list of indexer info dicts that support this category
    """
    loaded = registry.get_registry()
    cats: Dict[str, Dict[str, Any]] = {}

    for indexer in loaded.all():
        mapping = indexer.categories.model_dump()
        for yaml_key, code in mapping.items():
            if yaml_key == "default" or not code:
                continue

            cat_id = _cat_key_to_id(yaml_key)
            known = CATEGORIES_BY_KEY.get(yaml_key)

            if cat_id not in cats:
                cats[cat_id] = {
                    "id": cat_id,
                    "key": yaml_key,
                    "label": known.label if known else yaml_key.title(),
                    "order": known.order if known else len(CATEGORIES),
                    "indexers": [],
                }

            cats[cat_id]["indexers"].append(
                {
                    "id": indexer.id,
                    "name": indexer.name,
                    "favicon_url": indexer.favicon_url,
                    "color": indexer.color,
                }
            )

    result = sorted(cats.values(), key=lambda c: (c["order"], c["label"]))
    for index, cat in enumerate(result):
        cat["order"] = index
    return result


def _resolve_category(
    indexer: IndexerDefinition, cat: str
) -> tuple[Optional[str], Optional[str], bool]:
    """Resolve a submission category without silently falling back to `default`."""
    return indexer.categories.resolve_code(cat)


def _resolve_movie_submission_key(cat: str, rls_name: str) -> str:
    """Refine generic movie categories into movie sub-types based on release metadata."""
    normalized = CategoryMapping.normalize_key(cat)
    if normalized != "movie":
        return cat

    upper_name = str(rls_name or "").upper()
    dvd_tokens = ("DVD", "DVD5", "DVD9", "DVDR", "DVDRIP", "NTSC", "PAL", "VIDEO_TS")
    if any(token in upper_name for token in dvd_tokens):
        return "movie_dvd"
    if any(token in upper_name for token in ("2160P", "UHD", "4K")):
        return "movie_uhd"
    if any(
        token in upper_name for token in ("FULLBR", "FULL.BR", "BDISO", "BD25", "BD50")
    ):
        return "movie_full_br"
    if any(
        token in upper_name
        for token in ("1080P", "720P", "BLURAY", "WEB-DL", "WEBRIP", "HDTV", "REMUX")
    ):
        return "movie_hd"
    return "movie_sd"
