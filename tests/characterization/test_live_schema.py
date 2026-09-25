"""The live history database schema keeps opening, migrating and reading unchanged."""

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from core import config as config_mod
from core.db import engine as db_engine
from core.db import history as db_history
from core.db import ledger as db_ledger
from core.db import models as db_models
from core.db import schema as db_schema
from tests.characterization._snapshot import HERE

LIVE_SCHEMA = HERE / "live_schema.sql"
ITEM = "The.Matrix.1999.1080p.BluRay.x264-GRP"


def _schema(path: Path) -> dict[str, object]:
    con = sqlite3.connect(path)
    try:
        objects = {
            (kind, name): sql
            for kind, name, sql in con.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        columns = {
            table: {row[1]: row[2] for row in con.execute(f'PRAGMA table_info("{table}")')}
            for kind, table in objects
            if kind == "table"
        }
    finally:
        con.close()
    return {"objects": objects, "columns": columns}


def _reset_engine() -> None:
    engine = getattr(db_engine, "_ENGINE", None)
    if engine is not None:
        engine.dispose()
    db_engine._ENGINE = None
    db_engine._SESSION_FACTORY = None


@pytest.fixture()
def live_db(tmp_path, monkeypatch) -> Iterator[Path]:
    path = tmp_path / "usenet_uploads.db"
    con = sqlite3.connect(path)
    con.executescript(LIVE_SCHEMA.read_text(encoding="utf-8"))
    con.execute(
        "INSERT INTO uploads (item_name, filesize, itype, uploaded_at_in, created_at, updated_at) "
        "VALUES (?, '1234', 'Movie', '2026-01-02 03:04:05', '2026-01-02 03:04:05', '2026-01-02 03:04:05')",
        (ITEM,),
    )
    upload_id = con.execute("SELECT id FROM uploads WHERE item_name = ?", (ITEM,)).fetchone()[0]
    con.executemany(
        "INSERT INTO upload_results (upload_id, indexer_id, uploaded_at, status, error) VALUES (?, ?, ?, ?, ?)",
        [
            (upload_id, "in", "2026-01-02 03:04:05", "success", None),
            (upload_id, "geek", "2026-01-02 03:04:05", "failed", "HTTP 500"),
        ],
    )
    con.commit()
    con.close()

    monkeypatch.setattr(config_mod.Config, "log_db", property(lambda _self: path))
    _reset_engine()
    yield path
    _reset_engine()


def test_init_database_keeps_the_live_schema(live_db: Path) -> None:
    before = _schema(live_db)

    assert db_schema.init_database() is True
    _reset_engine()
    assert db_schema.init_database() is True
    _reset_engine()
    after = _schema(live_db)

    for key in before["objects"]:
        assert key in after["objects"], f"{key} disappeared"
    for table, columns in before["columns"].items():
        for column, column_type in columns.items():
            assert after["columns"][table].get(column) == column_type, f"{table}.{column} changed"

    model_tables = set(db_models.Base.metadata.tables)
    legacy = {key for key in before["objects"] if key[0] == "table" and key[1] not in model_tables}
    assert {name for _kind, name in legacy} == {
        "server_failures",
        "server_speeds",
        "stage_timings",
        "system_stats_history",
        "upload_stats",
        "upload_stats_avg",
    }
    for key in legacy | {("trigger", "update_avg_on_insert")}:
        assert after["objects"][key] == before["objects"][key], f"{key} was altered"

    for column in ("uploaded_at_in", "uploaded_at_geek", "uploaded_at_omg"):
        assert column in after["columns"]["uploads"]


def test_live_schema_serves_history_duplicate_and_dashboard_reads(live_db: Path) -> None:
    assert db_schema.init_database() is True

    grouped = db_history.get_grouped_uploads()
    assert grouped["total_groups"] == 1
    group = grouped["groups"][0]
    assert (group["show_name"], group["media_type"]) == ("The Matrix", "movie")
    item = group["items"][0]
    assert (item["item_name"], item["filesize"], item["parsed_title"]) == (ITEM, "1234", "The Matrix")
    assert [(dest["id"], dest["status"], dest["error"]) for dest in item["destinations"]] == [
        ("in", "success", None),
        ("geek", "failed", "HTTP 500"),
    ]

    duplicate = db_ledger.destinations_for(ITEM, "Movie", ["in", "geek"])
    assert duplicate == {"in": "2026-01-02T03:04:05", "geek": None}
    batch = db_ledger.destinations_for_batch([ITEM], ["in", "geek"])
    assert batch[ITEM]["in"] is not None
    assert batch[ITEM]["geek"] is None

    fully_done, success_map, failed_map, _filesizes = db_ledger.completion_index(["in", "geek"])
    assert fully_done == set()
    assert success_map[ITEM] == {"in"}
    assert failed_map[ITEM] == {"geek": "HTTP 500"}
