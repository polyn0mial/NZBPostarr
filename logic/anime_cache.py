"""
Jikan-backed anime identification cache.

Queries the free Jikan v4 API (MyAnimeList proxy) to determine whether a
given show title is an anime.  Results are cached persistently in a JSON
file so repeated scans never re-query the same title.

Rate-limit compliance
─────────────────────
Jikan allows **3 requests / second** and **60 requests / minute**.
We enforce both limits with a simple token-bucket approach:
  • A deque of recent request timestamps gates the per-second burst.
  • A counter + minute-window gates the per-minute budget.
If the budget is exhausted the lookup returns ``None`` (unknown) and the
caller should fall back to the non-anime classification.
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import requests
from loguru import logger

# ── Jikan API ───────────────────────────────────────────────────────────────
_JIKAN_SEARCH_URL = "https://api.jikan.moe/v4/anime"
_REQ_TIMEOUT = 8  # seconds

# ── Rate-limit constants ────────────────────────────────────────────────────
_MAX_PER_SECOND = 3
_MAX_PER_MINUTE = 60

# ── Module-level state (thread-safe via _lock) ──────────────────────────────
# Bump this whenever the matching algorithm changes - existing caches with a
# different (or missing) version will be discarded and rebuilt automatically.
_CACHE_VERSION = 8

# Score thresholds for 2-content-word tiebreaker.
# When the primary (romanized) title has at least one word overlap with our
# query (e.g. "Trigun" in "Trigun Stampede"), we trust a 7.0 score.
# When the match came purely from an English or alt title with NO overlap on
# the primary title (e.g. an anime whose English title happens to be
# "Beauty and the Beast" but whose Japanese title is completely different),
# we require a much higher bar - only genuinely famous franchise titles
# like Demon Slayer (8.4) or Jujutsu Kaisen (8.7) should clear it.
_MIN_SCORE_2WORD = 7.0  # primary title has word overlap with query
_MIN_SCORE_2WORD_ENG_ONLY = 8.0  # match is only on english/alt title
_MIN_MEMBERS_2WORD_UNSCORED = 20_000  # unscored, primary overlap
_MIN_MEMBERS_2WORD_ENG_ONLY = 100_000  # unscored, english-only match

_lock = threading.Lock()
_cache: Dict[str, bool] = {}  # normalised_name → is_anime
_cache_loaded = False
_cache_path: Optional[Path] = None

# Token-bucket timestamps
_second_window: deque[float] = deque()  # timestamps of last N requests
_minute_window: deque[float] = deque()

# ── Helpers ────────────────────────────────────────────────────────────────
_PUNC_RE = re.compile(r"[^\w\s]")
_RELEASE_YEAR_RE = re.compile(r"(?:^|[.\s_(-])((?:19|20)\d{2})(?=$|[.\s_)-])")
# Common English articles/prepositions excluded from word-overlap scoring
_STOPWORDS = frozenset({"a", "an", "the", "of", "in", "on", "at", "to", "and", "or", "is", "for", "no"})


def _fold_latin_diacritics(value: str) -> str:
    """Fold Latin accents without altering non-Latin scripts."""
    folded: list[str] = []
    previous_was_latin = False
    for char in unicodedata.normalize("NFD", value):
        if unicodedata.combining(char):
            if not previous_was_latin:
                folded.append(char)
            continue
        previous_was_latin = "LATIN" in unicodedata.name(char, "")
        folded.append(char)
    return unicodedata.normalize("NFC", "".join(folded))


def _strip_punc(s: str) -> str:
    """Remove punctuation and collapse whitespace for fuzzy title comparison."""
    folded = _fold_latin_diacritics(s)
    return re.sub(r"\s+", " ", _PUNC_RE.sub(" ", folded)).strip()


def _content_words(s: str) -> list[str]:
    """Return significant (non-stopword) words from a cleaned title string."""
    return [w for w in _strip_punc(s.lower()).split() if w not in _STOPWORDS]


def _title_matches(query: str, candidate: str) -> bool:
    """Return True when *query* and *candidate* refer to the same title.

    Rules (evaluated in order):
    1. Compute significant (non-stopword) words for the query.
       a. 0 words  - no match.
       b. 1 word   - exact string match only (safe for acronyms like "BNA").
       c. 2 words  - no match.  These titles are too ambiguous after stopword
          stripping (e.g. "Beauty and the Beast" → ["beauty","beast"]) and
          frequently share names with Western IP.
       d. 3+ words - proceed to exact match then bidirectional word-overlap.
    2. Exact string match (after punctuation strip) - only reached for 3+ words.
    3. Bidirectional content-word coverage ≥ 75 % in both directions.
    """
    q = _strip_punc(query.lower())
    c = _strip_punc(candidate.lower())
    if not q or not c:
        return False

    q_words = _content_words(q)
    c_words = _content_words(c)

    if not q_words or not c_words:
        return False

    # 1-word query: acronym / single-word title - exact string match only.
    if len(q_words) == 1:
        return q == c

    # 2-word query: too ambiguous (e.g. "beauty and the beast", "sweet reincarnation").
    if len(q_words) == 2:
        return False

    # 3+ words: exact match first, then bidirectional word-overlap.
    if q == c:
        return True

    q_set = set(q_words)
    c_set = set(c_words)
    common = q_set & c_set
    if not common:
        return False
    q_coverage = len(common) / len(q_set)
    c_coverage = len(common) / len(c_set)
    return q_coverage >= 0.75 and c_coverage >= 0.75


# ── Name normalisation ──────────────────────────────────────────────────────
_STRIP_TAGS_RE = re.compile(
    r"(?:"
    # Fansub group bracket: [SubsPlease]
    r"^\[[^\]]{1,40}\]\s*"
    # Resolution / source / codec suffixes: everything after year or SxxExx
    r"|(?:\.|\s)(?:S\d{2}|Season|Series|COMPLETE|Remux|Hybrid)"
    r".*"
    r")",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"[.\-_]+")


def _normalise_title(raw: str) -> str:
    """Strip structural metadata from a release name to get a searchable title.

    Examples:
        'Shakugan.no.Shana.S01.1080p.BluRay.REMUX'  →  'Shakugan no Shana'
        '[SubsPlease] Frieren - 01 (1080p)'          →  'Frieren'
        'Hunter X Hunter (2011)(3 files)'             →  'Hunter X Hunter'
    """
    name = raw.strip()

    # Strip common video/archive file extensions (filenames passed in directly)
    name = re.sub(
        r"\.(mkv|mp4|avi|m4v|mov|wmv|ts|m2ts|mpeg|mpg|flv|webm|nfo|nzb|zip|rar|7z)$",
        "",
        name,
        flags=re.IGNORECASE,
    )

    # Strip leading fansub bracket
    name = re.sub(r"^\[[^\]]{1,40}\]\s*", "", name)

    # Strip trailing "(N files)" descriptor
    name = re.sub(r"\(\d+\s*files?\)\s*$", "", name, flags=re.IGNORECASE).strip()

    # Strip everything from the first SxxExx / SxxPyy / Season / year / resolution token onward
    # SxxPyy  = Season xx Part yy  (e.g. S01P2, S02P1)
    name = re.sub(
        r"[.\s_-](?:S\d{1,2}(?:[.\s_-]|[EP]\d)|Season[.\s-]?\d|Series[.\s-]?\d"
        r"|(?:19|20)\d{2}(?:[.\s_\)-]|$)"
        r"|\d{3,4}p|COMPLETE|Remux|Hybrid|BluRay|WEB|DVD|NTSC|PAL|FLAC"
        r"|DTS|DD|AAC|AVC|HEVC|H\.?26[45]|x26[45]|10bit).*$",
        "",
        name,
        flags=re.IGNORECASE,
    )

    # Replace separators with spaces
    name = _SEPARATOR_RE.sub(" ", name).strip()

    # Strip bare trailing season code that the above regex missed
    # (e.g. "Title S01" at end of string where no separator follows the digits)
    name = re.sub(r"[\s._-]+S(?:eason|eries)?[\s._-]?\d{1,2}\s*$", "", name, flags=re.IGNORECASE).strip()

    # Collapse whitespace
    name = re.sub(r"\s{2,}", " ", name)

    return name


def _cache_key(title: str) -> str:
    """Consistent lowercase key for the cache dict."""
    return _strip_punc(title.strip().casefold())


def _release_year(raw_name: str) -> Optional[int]:
    """Return the last release-year token carried by a raw release name."""
    matches = _RELEASE_YEAR_RE.findall(str(raw_name or ""))
    return int(matches[-1]) if matches else None


def _cache_key_for_release(raw_name: str, title: str) -> str:
    """Keep year-specific verdicts from affecting another release of a title."""
    key = _cache_key(title)
    year = _release_year(raw_name)
    return f"{key}::{year}" if year is not None else key


def _jikan_entry_year(entry: Mapping[str, Any]) -> Optional[int]:
    """Read a production year from the fields returned by Jikan v4."""
    year = entry.get("year")
    if isinstance(year, int):
        return year
    if isinstance(year, str) and year.isdigit():
        return int(year)

    aired = entry.get("aired")
    aired_from = aired.get("from") if isinstance(aired, dict) else None
    if isinstance(aired_from, str):
        match = re.match(r"((?:19|20)\d{2})", aired_from)
        if match:
            return int(match.group(1))
    return None


# ── Persistence ─────────────────────────────────────────────────────────────


def _get_cache_path() -> Path:
    """Return the path to the anime cache JSON file."""
    global _cache_path
    if _cache_path is None:
        from core.config import APP_ROOT

        _cache_path = APP_ROOT / "data" / "cache" / "anime.json"
    return _cache_path


def _load_cache() -> None:
    """Load the cache from disk (called once on first access).

    Discards the on-disk cache if its ``__version__`` key doesn't match
    ``_CACHE_VERSION``, so algorithm upgrades automatically force a rebuild.
    """
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    path = _get_cache_path()
    from core.config import APP_ROOT

    legacy_path = APP_ROOT / "anime_cache.json"
    load_path = path if path.exists() else legacy_path
    if load_path.exists():
        try:
            with open(load_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                if raw.get("__version__") != _CACHE_VERSION:
                    logger.info(
                        f"Anime cache version mismatch (got {raw.get('__version__')!r}, "
                        f"need {_CACHE_VERSION}) - discarding stale cache at {load_path}"
                    )
                    _cache = {}
                else:
                    _cache = {k: bool(v) for k, v in raw.items() if k != "__version__"}
                    logger.debug(f"Anime cache loaded: {len(_cache)} entries from {load_path}")
        except Exception as e:
            logger.warning(f"Failed to load anime cache: {e}")
            _cache = {}
    _cache_loaded = True


def _save_cache() -> None:
    """Persist the cache to disk (includes version stamp)."""
    path = _get_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"__version__": _CACHE_VERSION, **_cache}, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save anime cache: {e}")


# ── Rate limiting ───────────────────────────────────────────────────────────


def _can_request() -> bool:
    """Check whether we can make another Jikan request within limits."""
    now = time.monotonic()

    # Prune old entries
    while _second_window and now - _second_window[0] > 1.0:
        _second_window.popleft()
    while _minute_window and now - _minute_window[0] > 60.0:
        _minute_window.popleft()

    return len(_second_window) < _MAX_PER_SECOND and len(_minute_window) < _MAX_PER_MINUTE


def _record_request() -> None:
    """Record that a request was made."""
    now = time.monotonic()
    _second_window.append(now)
    _minute_window.append(now)


def _wait_for_slot() -> bool:
    """Block (up to ~2 s) until a rate-limit slot opens, or give up.

    Returns True if a slot is available, False if we timed out.
    """
    for _ in range(20):  # 20 × 100 ms = 2 s max wait
        if _can_request():
            return True
        time.sleep(0.1)
    return False


# ── Jikan lookup ────────────────────────────────────────────────────────────


def _query_jikan(title: str, *, release_year: Optional[int] = None) -> Optional[bool]:
    """Query Jikan to determine if *title* is an anime.

    Returns True (anime), False (not anime), or None (lookup failed / rate-limited).

    Matching strategy
    ─────────────────
    • 3+ content words  ↔  ``_title_matches()`` bidirectional word-overlap.
    • 2 content words   ↔  exact string match only, then a score/membership
                           tiebreaker to reject obscure Western-IP OVAs
                           (e.g. the 1992 "Beauty and the Beast" OVA on MAL)
                           while still catching major anime like Demon Slayer.
    • 1 content word    ↔  ``_title_matches()`` handles (exact-only for acronyms).
    """
    if not _wait_for_slot():
        logger.trace(f"Jikan rate-limited, skipping lookup for: {title}")
        return None

    _record_request()

    # Pre-compute whether this is a short (2-content-word) title so we can
    # apply the score tiebreaker path inside the loop.
    q_norm = _strip_punc(title.lower())
    q_cwords = _content_words(q_norm)
    is_short = len(q_cwords) == 2

    try:
        params: dict[str, str | int] = {"q": title, "limit": 5, "sfw": "true"}
        resp = requests.get(
            _JIKAN_SEARCH_URL,
            params=params,
            timeout=_REQ_TIMEOUT,
        )
        if resp.status_code == 429:
            logger.debug("Jikan 429 (rate-limited)")
            return None
        if resp.status_code != 200:
            logger.debug(f"Jikan HTTP {resp.status_code} for '{title}'")
            return None

        data = resp.json().get("data", [])
        if not data:
            return False  # No results → not anime

        for entry in data:
            # Collect all title strings for this entry.
            candidates = [
                entry.get("title") or "",
                entry.get("title_english") or "",
                entry.get("title_japanese") or "",
            ]
            for alt in entry.get("titles", []):
                candidates.append(alt.get("title") or "")

            if is_short:
                # 2-content-word path: require an exact string match, then gate
                # on popularity to reject obscure OVAs.
                exact_hit = any(q_norm == _strip_punc(c.lower()) for c in candidates if c)
                if not exact_hit:
                    continue

                # Tiered score threshold: if the primary (romanized) title has
                # at least one content word in common with our query, this is a
                # genuine romaji-echoing title (e.g. "Trigun Stampede") - use
                # the standard 7.0 bar.  If the match came only from a translated
                # English or alt title with no overlap on the primary title (e.g.
                # an anime whose English alt title is "Beauty and the Beast" but
                # whose Japanese title is "Katsute Mahou Shoujo..."), require a
                # stricter 8.0 bar so only franchise-level hits pass.
                primary_words = _content_words(_strip_punc((entry.get("title") or "").lower()))
                has_primary_overlap = bool(set(q_cwords) & set(primary_words))
                if not has_primary_overlap and release_year is not None:
                    entry_year = _jikan_entry_year(entry)
                    if entry_year is not None and entry_year != release_year:
                        logger.debug(
                            f"Anime: '{title}' matched translated title '{entry.get('title')}' "
                            f"but release year {release_year} != anime year {entry_year} -> skipping"
                        )
                        continue
                score_threshold = _MIN_SCORE_2WORD if has_primary_overlap else _MIN_SCORE_2WORD_ENG_ONLY
                members_threshold = _MIN_MEMBERS_2WORD_UNSCORED if has_primary_overlap else _MIN_MEMBERS_2WORD_ENG_ONLY

                score = entry.get("score")  # float or None
                members = entry.get("members") or 0

                if score is not None:
                    if score >= score_threshold:
                        logger.debug(
                            f"Anime: '{title}' matched '{entry.get('title')}' "
                            f"score={score:.1f} >= {score_threshold} "
                            f"(primary_overlap={has_primary_overlap}) → anime"
                        )
                        return True
                    logger.debug(
                        f"Anime: '{title}' matched '{entry.get('title')}' "
                        f"but score={score:.1f} < {score_threshold} "
                        f"(primary_overlap={has_primary_overlap}) → skipping"
                    )
                    # Don't return False yet - another entry might score higher.
                else:
                    # No score yet (new/niche anime). Fall back to member count.
                    if members >= members_threshold:
                        logger.debug(
                            f"Anime: '{title}' matched '{entry.get('title')}' "
                            f"(unscored, members={members:,} >= {members_threshold:,}) → anime"
                        )
                        return True
                    logger.debug(
                        f"Anime: '{title}' matched '{entry.get('title')}' "
                        f"(unscored, members={members:,} < {members_threshold:,}) → skipping"
                    )
            else:
                # 3+-word path: use the strict bidirectional word-overlap matcher.
                if any(_title_matches(title, c) for c in candidates if c):
                    return True

        return False

    except requests.RequestException as e:
        logger.debug(f"Jikan request failed for '{title}': {e}")
        return None


# ── Public API ──────────────────────────────────────────────────────────────


def is_anime(raw_name: str) -> Optional[bool]:
    """Check whether *raw_name* (release/folder name) is an anime title.

    Returns:
        True   – confirmed anime (cached or freshly looked up)
        False  – confirmed NOT anime
        None   – unknown (rate-limited or lookup failed; try again later)

    Thread-safe.  The first call loads the persistent cache from disk.
    """
    with _lock:
        _load_cache()

        title = _normalise_title(raw_name)
        if not title or len(title) < 2:
            return None

        release_year = _release_year(raw_name)
        key = _cache_key_for_release(raw_name, title)

        # Check cache
        if key in _cache:
            return _cache[key]

        # Lookup
        result = _query_jikan(title, release_year=release_year)
        if result is not None:
            _cache[key] = result
            _save_cache()
            logger.debug(f"Anime cache: '{title}' → {'anime' if result else 'not anime'}")

        return result


def check_titles_batch(raw_names: list[str]) -> Dict[str, Optional[bool]]:
    """Check multiple titles, respecting rate limits.

    Returns a dict mapping each raw_name to True/False/None.
    Stops querying Jikan when the rate limit is exhausted; remaining
    unchecked titles get None.
    """
    results: Dict[str, Optional[bool]] = {}
    with _lock:
        _load_cache()

        for raw in raw_names:
            title = _normalise_title(raw)
            release_year = _release_year(raw)
            key = _cache_key_for_release(raw, title)

            if key in _cache:
                results[raw] = _cache[key]
                continue

            # Try lookup (will block briefly for rate-limit slots)
            r = _query_jikan(title, release_year=release_year)
            if r is not None:
                _cache[key] = r
                results[raw] = r
            else:
                results[raw] = None  # rate-limited

        _save_cache()
    return results


def get_cached(raw_name: str) -> Optional[bool]:
    """Return cached anime status without making any network requests.

    Returns True/False if cached, None if unknown.
    """
    with _lock:
        _load_cache()
        title = _normalise_title(raw_name)
        if not title:
            return None
        return _cache.get(_cache_key_for_release(raw_name, title))


def set_cached(raw_name: str, value: bool) -> bool:
    """Persist a user-confirmed anime verdict for one normalized release."""
    with _lock:
        _load_cache()
        title = _normalise_title(raw_name)
        if not title:
            return False
        _cache[_cache_key_for_release(raw_name, title)] = bool(value)
        _save_cache()
        return True


def invalidate(raw_name: str) -> bool:
    """Remove one cached anime verdict without affecting other releases."""
    with _lock:
        _load_cache()
        title = _normalise_title(raw_name)
        if not title:
            return False
        key = _cache_key_for_release(raw_name, title)
        if key not in _cache:
            return False
        del _cache[key]
        _save_cache()
        return True
