# ruff: noqa: F403,F405

"""Regression tests for the db-registry fixes ported from the live server."""

from loguru import logger

from tests.support import *

from core.database import pin_folder_ts_to_children

_OLD_TS = "2020-01-01 00:00:00.000000"


def _set_updated_at(key: str, value: str) -> None:
    with session_scope() as session:
        session.execute(
            db.text("UPDATE uploads SET updated_at = :ts WHERE item_name = :name"),
            {"ts": value, "name": key},
        )


def _raw_updated_at(key: str) -> str:
    with session_scope() as session:
        return session.execute(
            db.text("SELECT updated_at FROM uploads WHERE item_name = :name"),
            {"name": key},
        ).scalar_one()


def _set_filesize(key: str, value) -> None:
    with session_scope() as session:
        session.execute(
            db.text("UPDATE uploads SET filesize = :size WHERE item_name = :name"),
            {"size": value, "name": key},
        )


def _success(key: str, dest: str = "geek", size: int = 100, **extra) -> bool:
    return update_db_destination(dest, key, size, key, itype="TV Episode", status="success", **extra)


# --- db-registry-01 -----------------------------------------------------------


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_engine_pool_matches_server_sizing() -> None:
    engine = db.get_engine()
    assert engine.pool.size() == 10
    assert engine.pool._max_overflow == 5


# --- db-registry-02 -----------------------------------------------------------


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_record_nntp_success_only_bumps_timestamp_on_first_upload() -> None:
    key = "Show/Show.S01E01.1080p.WEB-DL.mkv"
    record_nntp_success(key, 100, "TV Episode")
    assert not _raw_updated_at(key).startswith("2020-")

    assert _success(key, _bump_timestamp=False) is True
    _set_updated_at(key, _OLD_TS)

    # A result already exists: resuming a partial upload keeps the original position.
    record_nntp_success(key, 200, "TV Episode")
    assert _raw_updated_at(key) == _OLD_TS

    record_nntp_success(key, 300, "TV Episode", bump_timestamp=False)
    assert _raw_updated_at(key) == _OLD_TS
    with session_scope() as session:
        assert session.query(Upload).filter_by(item_name=key).one().filesize == 300


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_update_db_destination_keeps_timestamp_after_first_success() -> None:
    key = "Show/Show.S01E02.1080p.WEB-DL.mkv"
    assert _success(key) is True
    _set_updated_at(key, _OLD_TS)

    # Second destination / retry after a prior success must not float the row to the top.
    assert _success(key, dest="omg") is True
    assert _raw_updated_at(key) == _OLD_TS

    assert _success(key, dest="in", _bump_timestamp=False) is True
    assert _raw_updated_at(key) == _OLD_TS


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_folder_rows_do_not_bump_when_requested() -> None:
    folder = "Pack.S01"
    record_nntp_success(folder, 100, "TV Pack")
    _set_updated_at(folder, _OLD_TS)

    record_nntp_success(folder, 150, "TV Pack", bump_timestamp=False)
    assert _raw_updated_at(folder) == _OLD_TS
    assert update_db_destination("geek", folder, 150, folder, itype="TV Pack", _bump_timestamp=False) is True
    assert _raw_updated_at(folder) == _OLD_TS


# --- db-registry-03 -----------------------------------------------------------


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_pin_folder_ts_to_children_sits_one_microsecond_above_newest_child() -> None:
    folder = "Pack.S02"
    children = [f"{folder}/Pack.S02E01.mkv", f"{folder}/Pack.S02E02.mkv"]
    for key in [folder, *children, "Other.Show.S01E01.mkv"]:
        record_nntp_success(key, 100, "TV Episode")
    _set_updated_at(children[0], "2024-05-01 10:00:00.000000")
    _set_updated_at(children[1], "2024-05-01 11:00:00.999999")
    _set_updated_at("Other.Show.S01E01.mkv", "2024-05-01 10:30:00.000000")
    _set_updated_at(folder, "2025-01-01 00:00:00.000000")

    pin_folder_ts_to_children(folder)

    # Same text format the ORM writes, so SQLite string ordering stays consistent.
    assert _raw_updated_at(folder) == "2024-05-01 11:00:01.000000"
    with session_scope() as session:
        row = session.query(Upload).filter_by(item_name=folder).one()
        assert row.updated_at == datetime(2024, 5, 1, 11, 0, 1)
        ordered = [u.item_name for u in session.query(Upload).order_by(Upload.updated_at.desc()).all()]
    assert ordered[:3] == [folder, children[1], "Other.Show.S01E01.mkv"]

    # Missing folder rows and folders without children are a no-op.
    pin_folder_ts_to_children("Missing.Pack")
    record_nntp_success("Empty.Pack", 1, "TV Pack")
    _set_updated_at("Empty.Pack", _OLD_TS)
    pin_folder_ts_to_children("Empty.Pack")
    assert _raw_updated_at("Empty.Pack") == _OLD_TS


# --- db-registry-04 -----------------------------------------------------------


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_failed_retry_never_overwrites_prior_success() -> None:
    key = "Show/Show.S01E03.1080p.WEB-DL.mkv"
    assert _success(key, server_name="first") is True
    assert (
        update_db_destination("geek", key, 100, key, itype="TV Episode", status="failed", error="boom") is True
    )

    with session_scope() as session:
        result = session.query(UploadResult).filter_by(indexer_id="geek").one()
        assert result.status == "success"
        assert result.error is None
        assert result.server_name == "first"

    _fully_done, success_map, failed_map, _sizes = db.get_dashboard_data(["geek"])
    assert success_map[key] == {"geek"}
    assert key not in failed_map

    # A failure with no prior success is still recorded.
    other = "Show/Show.S01E04.1080p.WEB-DL.mkv"
    assert update_db_destination("geek", other, 100, other, status="failed", error="boom") is True
    _fully_done, _success_map, failed_map, _sizes = db.get_dashboard_data(["geek"])
    assert failed_map[other] == {"geek": "boom"}


# --- db-registry-05 -----------------------------------------------------------


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_check_duplicate_dynamic_ignores_suffix_matches_without_size() -> None:
    stored = "Old.Folder/Show.S02E01.mkv"
    assert _success(stored) is True
    _set_filesize(stored, None)

    new_key = "New.Folder/Show.S02E01.mkv"
    assert db.check_duplicate_dynamic(new_key, "TV Episode", ["geek"], filesize=100)["geek"] is None
    # Without a size to compare the old behaviour stays.
    assert db.check_duplicate_dynamic(new_key, "TV Episode", ["geek"])["geek"] is not None
    # An exact-key record still counts even when its size is unknown.
    assert db.check_duplicate_dynamic(stored, "TV Episode", ["geek"], filesize=100)["geek"] is not None


# --- db-registry-06 -----------------------------------------------------------


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_duplicate_status_batch_is_size_aware() -> None:
    sized = "Folder.A/Show.S03E01.mkv"
    unsized = "Folder.B/Show.S03E02.mkv"
    assert _success(sized, size=100) is True
    assert _success(unsized, size=100) is True
    _set_filesize(unsized, None)
    suffix_key = "Folder.C/Show.S03E02.mkv"
    keys = [sized, unsized, suffix_key]

    plain = db.get_duplicate_status_batch(keys, ["geek"])
    assert all(plain[key]["geek"] is not None for key in keys)

    same = db.get_duplicate_status_batch(keys, ["geek"], filesizes={sized: 100, unsized: 5, suffix_key: 5})
    assert same[sized]["geek"] is not None
    assert same[unsized]["geek"] is not None
    assert same[suffix_key]["geek"] is None

    replaced = db.get_duplicate_status_batch([sized], ["geek"], filesizes={sized: 200})
    assert replaced[sized]["geek"] is None


# --- db-registry-07 -----------------------------------------------------------


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_dashboard_data_returns_filesize_by_indexer() -> None:
    assert db.get_dashboard_data([]) == (set(), {}, {}, {})

    int_key = "Show/Show.S04E01.mkv"
    text_key = "Show/Show.S04E02.mkv"
    bad_key = "Show/Show.S04E03.mkv"
    for key in (int_key, text_key, bad_key):
        assert _success(key, size=123) is True
        assert _success(key, dest="omg", size=123) is True
    # The live schema stores filesize as TEXT; legacy rows may hold text values.
    _set_filesize(text_key, "456")
    _set_filesize(bad_key, "not-a-size")

    fully_done, success_map, failed_map, sizes = db.get_dashboard_data(["geek", "omg"])
    assert {int_key, text_key, bad_key} <= fully_done
    assert success_map[int_key] == {"geek", "omg"}
    assert failed_map == {}
    assert sizes[int_key] == {"geek": 123, "omg": 123}
    assert sizes[text_key] == {"geek": 456, "omg": 456}
    assert bad_key not in sizes


# --- db-registry-08 / 09 / 10 / 11 --------------------------------------------


def _curl_indexer(**overrides) -> IndexerDefinition:
    values = dict(
        id="curl-port",
        name="CURL Port",
        submit_url="https://example.invalid/api-upload.php",
        method="CURL",
        auth=AuthConfig(method="none"),
        success=registry_mod.SuccessPatterns(text_patterns=["upload successful"], duplicate_patterns=["duplicate"]),
    )
    values.update(overrides)
    return IndexerDefinition(**values)


def _api_indexer() -> IndexerDefinition:
    return IndexerDefinition(
        id="geek",
        name="NZBGeek",
        submit_url="https://example.invalid/api",
        auth=AuthConfig(method="query_param", api_key_param="apikey"),
        success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
    )


def _response(status_code: int, body: str, reason: str = "") -> registry_mod.requests.Response:
    response = registry_mod.requests.Response()
    response.status_code = status_code
    response.reason = reason
    response.encoding = "utf-8"
    response._content = body.encode("utf-8")
    response.url = "https://example.invalid/api?apikey=secret-key-123"
    return response


def _capture_logs() -> tuple[list[str], int]:
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="DEBUG")
    return messages, sink_id


def test_curl_submission_follows_redirects_and_uses_final_page(tmp_path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_request(*_args, **kwargs):
        seen.update(kwargs)
        return _response(200, "<html><body>Upload successful</body></html>", "OK")

    monkeypatch.setattr(registry_mod.requests, "request", fake_request)
    ok, status, _reason = submit_to_indexer(
        indexer=_curl_indexer(),
        rls_name="Some.Release.2026.1080p.WEB-DL",
        nzb_path=_make_sample_nzb(tmp_path),
        config=_DummySubmitConfig(api_key=""),
    )

    assert "allow_redirects" not in seen  # requests follows redirects by default
    assert seen["verify"] is False
    assert (ok, status) == (True, "success")
    assert not hasattr(registry_mod, "_curl_redirect_result")


def test_curl_submission_error_page_after_redirect_is_a_network_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        registry_mod.requests,
        "request",
        lambda *_args, **_kwargs: _response(500, "<html><title>Error</title><p>inf=err3</p></html>", "Internal Server Error"),
    )
    ok, status, reason = submit_to_indexer(
        indexer=_curl_indexer(),
        rls_name="Some.Release.2026.1080p.WEB-DL",
        nzb_path=_make_sample_nzb(tmp_path),
        config=_DummySubmitConfig(api_key=""),
    )

    assert (ok, status) == (False, "network_error")
    assert reason == "HTTP 500 Internal Server Error | Body: Error inf=err3"


def test_http_error_message_uses_status_line_and_meta_description(tmp_path, monkeypatch) -> None:
    body = (
        '<html><head><meta content="Upload rejected for secret-key-123" name="description">'
        "</head><body><h1>Nope</h1></body></html>"
    )
    monkeypatch.setattr(registry_mod.requests, "request", lambda *_args, **_kwargs: _response(400, body, "Bad Request"))
    ok, status, reason = submit_to_indexer(
        indexer=_api_indexer(),
        rls_name="Some.Release.2026.1080p.WEB-DL",
        nzb_path=_make_sample_nzb(tmp_path),
        config=_DummySubmitConfig(api_key="secret-key-123"),
    )

    assert (ok, status) == (False, "network_error")
    assert reason.startswith("HTTP 400 Bad Request | Body: Upload rejected for ")
    assert "secret-key-123" not in reason
    assert "example.invalid" not in reason


def test_http_error_message_plain_text_body(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        registry_mod.requests,
        "request",
        lambda *_args, **_kwargs: _response(503, "Service\nUnavailable", ""),
    )
    _ok, _status, reason = submit_to_indexer(
        indexer=_api_indexer(),
        rls_name="Some.Release.2026.1080p.WEB-DL",
        nzb_path=_make_sample_nzb(tmp_path),
        config=_DummySubmitConfig(api_key="secret-key-123"),
    )
    assert reason == "HTTP 503 Error | Body: Service Unavailable"


def test_connection_error_message_names_the_failure_and_redacts(tmp_path, monkeypatch) -> None:
    def fake_request(*_args, **_kwargs):
        raise registry_mod.requests.exceptions.SSLError(
            "HTTPSConnectionPool: Max retries exceeded with url: /api?apikey=secret-key-123 (certificate verify failed)"
        )

    monkeypatch.setattr(registry_mod.requests, "request", fake_request)
    ok, status, reason = submit_to_indexer(
        indexer=_api_indexer(),
        rls_name="Some.Release.2026.1080p.WEB-DL",
        nzb_path=_make_sample_nzb(tmp_path),
        config=_DummySubmitConfig(api_key="secret-key-123"),
    )

    assert (ok, status) == (False, "network_error")
    assert reason.startswith("SSLError: ")
    assert "certificate verify failed" in reason
    assert "secret-key-123" not in reason


def test_successful_submission_logs_redacted_response_body(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        registry_mod.requests,
        "request",
        lambda *_args, **_kwargs: _response(200, "OK\nuploaded with key secret-key-123", "OK"),
    )
    messages, sink_id = _capture_logs()
    try:
        ok, _status, _reason = submit_to_indexer(
            indexer=_api_indexer(),
            rls_name="Some.Release.2026.1080p.WEB-DL",
            nzb_path=_make_sample_nzb(tmp_path),
            config=_DummySubmitConfig(api_key="secret-key-123"),
        )
    finally:
        logger.remove(sink_id)

    assert ok is True
    response_lines = [line for line in messages if "Response: HTTP 200 | OK uploaded with key " in line]
    assert response_lines
    assert all("secret-key-123" not in line for line in messages)


# --- db-registry-D03 / D04 ----------------------------------------------------


def test_available_categories_do_not_mirror_audiobooks(monkeypatch) -> None:
    books = registry_mod.CategoryMapping(books="7020")
    indexer = _api_indexer().model_copy(update={"categories": books})
    monkeypatch.setattr(registry_mod, "get_registry", lambda: SimpleNamespace(all=lambda: [indexer]))

    assert [cat["id"] for cat in registry_mod.get_available_categories()] == ["books"]
    assert books.resolve_code("audiobooks")[0] == "7020"  # submit-time fallback stays


def _server_indexers_dir() -> Path | None:
    override = os.environ.get("NZBPOSTARR_SERVER_INDEXERS_DIR")
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        local = parent / ".local"
        if not local.is_dir():
            continue
        snapshots = sorted(local.glob("server-snapshot-*/nzbpostarr/indexers"))
        if snapshots:
            return snapshots[-1]
    return None


def _yaml_indexer_ids(directory: Path) -> set[str]:
    ids: set[str] = set()
    for yaml_file in directory.glob("*.yaml"):
        stem_lower = yaml_file.stem.lower()
        if yaml_file.name.startswith("_") or stem_lower.endswith((".example", ".template")):
            continue
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or []
        for entry in data if isinstance(data, list) else [data]:
            ids.add(str(entry["id"]))
    return ids


def test_every_server_indexer_yaml_loads_through_strict_registry(monkeypatch) -> None:
    """Strict method/auth validation must not reject any YAML the live server uses."""
    server_dir = _server_indexers_dir()
    if server_dir is None or not server_dir.is_dir():
        pytest.skip("no local server snapshot of indexers/*.yaml available")

    expected_ids = _yaml_indexer_ids(server_dir)
    assert expected_ids

    monkeypatch.setattr(registry_mod.IndexerRegistry, "_instance", None)
    monkeypatch.setattr(registry_mod.IndexerRegistry, "indexers_dir", property(lambda _self: server_dir))
    loaded = registry_mod.IndexerRegistry()

    assert {indexer.id for indexer in loaded.all()} == expected_ids
