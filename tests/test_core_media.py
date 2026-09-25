import pytest

from core import media

# Snapshot of the uploads.itype strings each writer produced before the dialects moved into
# core.media. These are persisted: a change here rewrites history labels.
PROCESSING_SNAPSHOT = [
    ("tv", True, "TV Show"),
    ("tv", False, "TV Episode"),
    ("movies", False, "Movies"),
    ("anime", False, "Anime"),
    ("disc", False, "DISC"),
    ("music", False, "Music"),
    ("books", False, "Books"),
    ("apps", False, "Apps"),
    ("audiobooks", False, "Misc"),
    ("misc", False, "Misc"),
    ("", False, "Misc"),
]

DEFAULT_SNAPSHOT = [
    ("tv", True, "TV Show"),
    ("tv", False, "TV Episode"),
    (" TV ", False, "TV Episode"),
    ("movies", False, "Movie"),
    ("Movies", True, "Movie"),
    ("anime", False, "Anime"),
    ("music", True, "Music"),
    ("audiobooks", True, "Audiobook"),
    ("books", False, "Ebook"),
    ("apps", True, "App"),
    ("disc", True, "Misc"),
    ("misc", False, "Misc"),
    ("", False, "Misc"),
]

STREAM_SNAPSHOT = [
    ("movies", "Movie"),
    ("tv", "TV Show"),
    ("TV", "TV Show"),
    ("anime", "Anime"),
    ("music", "Music"),
    ("audiobooks", "Audiobooks"),
    ("some_thing", "Some Thing"),
    ("", "Misc"),
    (None, "Misc"),
]


@pytest.mark.parametrize(("category", "season_pack", "expected"), PROCESSING_SNAPSHOT)
def test_processing_itype_matches_snapshot(category, season_pack, expected) -> None:
    assert media.processing_itype(category, season_pack=season_pack) == expected


@pytest.mark.parametrize(("category", "is_dir", "expected"), DEFAULT_SNAPSHOT)
def test_default_itype_matches_snapshot(category, is_dir, expected) -> None:
    assert media.default_itype(category, is_dir=is_dir) == expected


@pytest.mark.parametrize(("category", "expected"), STREAM_SNAPSHOT)
def test_stream_itype_matches_snapshot(category, expected) -> None:
    assert media.stream_itype(category) == expected


def test_processing_writer_uses_the_processing_dialect(tmp_path) -> None:
    from logic import processing

    episode = tmp_path / "Show.S01E01.1080p.mkv"
    episode.write_bytes(b"x")

    assert processing._processing_db_type(episode, "tv") == "TV Episode"
    assert processing._processing_db_type(episode, "movies") == "Movies"
    assert processing._processing_db_type(episode, "books") == "Books"


def test_pending_and_queue_writers_use_the_default_dialect(tmp_path) -> None:
    from pathlib import Path

    from api import jobs as jobs_api
    from logic.pending.selection import upload_itype

    assert jobs_api._default_itype_for_category(Path("missing.mkv"), "tv") == "TV Episode"
    assert jobs_api._default_itype_for_category(tmp_path, "tv") == "TV Show"
    assert upload_itype({"category": "books", "is_dir": True}) == "Ebook"


def test_normalize_category_and_itype_mapping() -> None:
    assert media.normalize_category("Movie") == media.normalize_category("movies")
    assert media.category_for_itype("TV Show") == media.category_for_itype("tv episode")
    assert media.category_for_itype("nonsense") == "misc"
    assert {category.id for category in media.CATEGORIES} >= {"movies", "tv", "anime", "music"}

