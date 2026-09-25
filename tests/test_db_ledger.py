"""core/db: the one size predicate, golden duplicate/completion verdicts, schema tolerance, no logic imports."""

import ast
from pathlib import Path

import pytest
from sqlalchemy import text

from core.db import ledger as db_ledger
from core.db import schema as db_schema
from core.db.engine import session_scope
from core.db.models import Upload, UploadResult
from core.db.uploads import update_db_destination

ROOT = Path(__file__).resolve().parents[1]
IDS = ["geek", "in"]


@pytest.mark.parametrize(
    ("stored", "expected", "verdict"),
    [
        (None, 100, None),
        (100, None, None),
        (None, None, None),
        (100, 100, True),
        ("100", 100, True),
        (100, "100", True),
        (0, 0, True),
        (100, 101, False),
        ("abc", 100, None),
        (100, "abc", None),
    ],
)
def test_size_matches_table(stored, expected, verdict) -> None:
    assert db_ledger.size_matches(stored, expected) is verdict


def _seed_ledger() -> None:
    rows = [
        ("Movies/Film.2020.mkv", 100, "geek", "success", None),
        ("Film.2021.mkv", None, "geek", "success", None),  # bare-name row, size unknown
        ("Film.2022.mkv", "300", "in", "success", None),  # bare-name row, legacy text size
        ("Movies/Film.2023.mkv", 400, "geek", "failed", "boom"),
    ]
    with session_scope() as session:
        for name, size, dest, status, error in rows:
            upload = Upload(item_name=name, filesize=size, itype="Movie")
            session.add(upload)
            session.flush()
            session.add(UploadResult(upload_id=upload.id, indexer_id=dest, status=status, error=error))


# (key, current size) -> indexers counted as done. These are the verdicts of the pre-move
# check_duplicate_dynamic / get_duplicate_status_batch rules.
GOLDEN = [
    (("Movies/Film.2020.mkv", None), {"geek"}),
    (("Movies/Film.2020.mkv", 100), {"geek"}),
    (("Movies/Film.2020.mkv", 999), set()),
    (("Other/Film.2021.mkv", None), {"geek"}),
    (("Other/Film.2021.mkv", 50), set()),
    (("Film.2021.mkv", 50), {"geek"}),
    (("X/Film.2022.mkv", 300), {"in"}),
    (("X/Film.2022.mkv", 301), set()),
    (("Movies/Film.2023.mkv", None), set()),
]


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_single_and_batch_lookups_give_the_golden_verdicts() -> None:
    _seed_ledger()
    for (key, size), done in GOLDEN:
        single = db_ledger.destinations_for(key, "Movie", IDS, filesize=size)
        assert {idx for idx, at in single.items() if at} == done, (key, size)
        batch = db_ledger.destinations_for_batch([key], IDS, filesizes={key: size} if size is not None else None)
        assert {idx for idx, at in batch[key].items() if at} == done, (key, size)


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_completion_index_is_named_and_gives_the_golden_maps() -> None:
    _seed_ledger()
    index = db_ledger.completion_index(IDS)
    assert isinstance(index, db_ledger.CompletionIndex)
    assert index.fully_done == set()
    assert index.success_map == {"Movies/Film.2020.mkv": {"geek"}, "Film.2021.mkv": {"geek"}, "Film.2022.mkv": {"in"}}
    assert index.failed_map == {"Movies/Film.2023.mkv": {"geek": "boom"}}
    assert index.filesize_by_indexer == {"Movies/Film.2020.mkv": {"geek": 100}, "Film.2022.mkv": {"in": 300}}
    fully_done, _success, _failed, _sizes = db_ledger.completion_index(["geek"])
    assert fully_done == {"Movies/Film.2020.mkv", "Film.2021.mkv"}
    assert db_ledger.completion_index([]) == (set(), {}, {}, {})


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_init_database_never_touches_tolerated_tables() -> None:
    with session_scope() as session:
        session.execute(text("CREATE TABLE upload_stats (id INTEGER PRIMARY KEY, note VARCHAR)"))
        session.execute(text("INSERT INTO upload_stats (note) VALUES ('kept')"))
    assert db_schema.init_database() is True
    assert db_schema.init_database() is True
    with session_scope() as session:
        assert session.execute(text("SELECT note FROM upload_stats")).scalars().all() == ["kept"]
        created = {row[0] for row in session.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))}
    assert not (db_schema.TOLERATED_TABLES - {"upload_stats"}) & created


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_successful_upload_reads_the_cached_uploads_columns(monkeypatch) -> None:
    db_schema.uploads_columns(None)  # init already probed this engine: no connection needed

    def _no_probe(_conn):
        raise AssertionError("PRAGMA table_info(uploads) probed again")

    monkeypatch.setattr(db_schema, "_probe_uploads_columns", _no_probe)
    key = "Movies/Film.2024.mkv"
    assert update_db_destination("geek", key, 100, key, itype="Movie", status="success") is True
    assert update_db_destination("in", key, 100, key, itype="Movie", status="success") is True


def test_core_db_imports_nothing_from_logic() -> None:
    offenders = []
    for path in sorted((ROOT / "core" / "db").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {name}" for name in names if name == "logic" or name.startswith("logic.")]
    assert offenders == []
