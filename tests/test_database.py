# ruff: noqa: F403,F405

"""NZBPostarr database tests."""

from tests.support import *

def test_database_models_initialize_expected_tables() -> None:
    assert init_database() is True

    expected_models = {
        "uploads": Upload,
        "upload_results": UploadResult,
        "job_history": JobHistory,
        "system_stats": SystemStat,
    }

    for table_name, model in expected_models.items():
        assert model.__tablename__ == table_name
        assert model.__table__.columns

    required = [
        "cpu_percent",
        "memory_percent",
        "upload_mbps",
        "download_mbps",
        "total_sent_mb",
        "total_recv_mb",
        "connections",
        "disk_percent",
        "disk_free_gb",
        "disk_read_mbps",
        "disk_write_mbps",
        "errors_in",
        "errors_out",
        "drops_in",
        "drops_out",
    ]
    columns = [c.name for c in SystemStat.__table__.columns]
    for col in required:
        assert col in columns, f"Missing column {col} in SystemStat model"

def test_record_nntp_success_is_idempotent() -> None:
    init_database()

    key = "tests/Test.Show.S01E01.1080p.WEB-DL.mkv"
    record_nntp_success(key, 123, "TV Episode")
    record_nntp_success(key, 456, "TV Episode")

    with session_scope() as session:
        rows = session.query(Upload).filter_by(item_name=key).all()
        assert len(rows) == 1
        assert rows[0].filesize == 456
        assert rows[0].itype == "TV Episode"

def test_upload_db_destination_is_idempotent() -> None:
    init_database()

    key = "tests/Test.Show.S01E02.1080p.WEB-DL.mkv"
    assert (
        update_db_destination(
            dest="testidx",
            _name=key,
            size=100,
            key=key,
            itype="TV Episode",
            duration=1.0,
            speed_bps=123.0,
            server_name="testsrv",
            status="success",
        )
        is True
    )
    assert (
        update_db_destination(
            dest="testidx",
            _name=key,
            size=200,
            key=key,
            itype="TV Episode",
            duration=2.0,
            speed_bps=456.0,
            server_name="testsrv",
            status="success",
        )
        is True
    )

    with session_scope() as session:
        upload = session.query(Upload).filter_by(item_name=key).one()
        results = session.query(UploadResult).filter_by(upload_id=upload.id, indexer_id="testidx").all()
        assert len(results) == 1
        assert upload.filesize == 200

def test_dashboard_data_raises_typed_error_on_db_failure(monkeypatch) -> None:
    class _BrokenSession:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(db_engine, "session_scope", lambda: _BrokenSession())

    with pytest.raises(DatabaseOperationalError):
        db_ledger.completion_index(["geek"])

def test_duplicate_and_history_queries_fail_closed_on_db_failure(monkeypatch) -> None:
    class _BrokenSession:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(db_engine, "session_scope", lambda: _BrokenSession())
    operations = [
        lambda: db_ledger.destinations_for("release", "TV Episode", ["geek"]),
        lambda: db_ledger.destinations_for_batch(["release"], ["geek"]),
        lambda: db_job_history.get_job_history(),
        lambda: db_job_history.delete_job_history(["job-id"]),
        lambda: db_history.get_recent_uploads(),
        lambda: db_history.get_grouped_uploads(),
        lambda: db_history.get_group_upload_items("title"),
        lambda: db_history.get_uploads_for_job("job-id"),
        lambda: db_uploads.delete_upload_item("release"),
        lambda: db_uploads.bulk_delete_upload_items(["release"]),
        lambda: db_issues.get_grouped_upload_errors(),
        lambda: db_issues.mute_upload_issue("geek", "auth failed"),
        lambda: db_issues.unmute_upload_issue("geek", "auth failed"),
    ]

    for operation in operations:
        with pytest.raises(DatabaseOperationalError):
            operation()

def test_scan_pending_snapshot_marks_indexer_status_unavailable_on_db_error(tmp_path, monkeypatch) -> None:
    from logic.pending import tree as pending_tree

    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    (movies_dir / "Movie.Name.2026.1080p.mkv").write_bytes(b"x")

    conf = SimpleNamespace(
        folder_paths=[],
        movies_folder=str(movies_dir),
        tv_folder=None,
        misc_folder=None,
        skip_files=None,
    )
    indexer = SimpleNamespace(id="idx1", name="Indexer 1", color="#fff", icon="database", favicon_url=None)

    _configure_pending_snapshot_environment(
        monkeypatch,
        conf,
        dashboard_data=lambda _ids: (_ for _ in ()).throw(
            db_engine.DatabaseOperationalError("db down")
        ),
        configured_folders=[("movies", movies_dir)],
        indexers=[indexer],
        compute_size_uncached=lambda path: 1,
    )

    payload = pending_tree.scan_pending_snapshot()

    assert payload["indexer_status_available"] is False
    assert payload["db_error"] == "db down"
    assert payload["summary"]["total"] == 0
    assert payload["items"]["movies"][0]["indexers"] == {}

def test_processing_db_type_supports_anime_and_media_categories(tmp_path) -> None:
    import logic.processing as processing

    video = tmp_path / "item.mkv"
    video.write_bytes(b"x")

    for category, expected in (
        ("anime", "Anime"),
        ("music", "Music"),
        ("books", "Books"),
        ("apps", "Apps"),
    ):
        assert processing._processing_db_type(video, category) == expected, category

@pytest.mark.usefixtures("isolated_sqlite_db")
def test_database_e2e_smoke() -> None:
    health = db_engine.get_database_health()
    assert health["exists"] is True
    assert health["tables"]["uploads"]["status"] == "ok"

    # --- Job history ---
    job_id = "job-e2e-1"
    db_job_history.save_job_history(
        job_id,
        category="tv",
        status="running",
        items_total=2,
        items_processed=0,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    jobs = db_job_history.get_job_history(limit=10)
    assert any(j.get("job_id") == job_id for j in jobs)

    # --- Upload tracking ---
    key1 = "Call.the.Midwife/Call.the.Midwife.S00E03.Christmas.Special.1080p.AMZN.WEB-DL.DDP2.0.H.264-NOGRP.mkv"
    db_uploads.record_nntp_success(key1, 123, "TV Episode")
    assert (
        db_uploads.update_db_destination(
            dest="geek",
            _name=key1,
            size=123,
            key=key1,
            itype="TV Episode",
            duration=1.2,
            speed_bps=100.0,
            server_name="testsrv",
            status="success",
        )
        is True
    )
    assert (
        db_uploads.update_db_destination(
            dest="omg",
            _name=key1,
            size=123,
            key=key1,
            itype="TV Episode",
            duration=1.3,
            speed_bps=200.0,
            server_name="testsrv",
            status="success",
        )
        is True
    )

    dup = db_ledger.destinations_for(key1, "TV Episode", ["geek", "omg"])
    assert dup["geek"] is not None
    assert dup["omg"] is not None

    fully_done, dash_map, _failed_map, _sizes = db_ledger.completion_index(["geek", "omg"])
    assert key1 in dash_map
    assert key1 in fully_done

    recent = db_history.get_recent_uploads(limit=10)
    assert recent["total"] >= 1
    assert any(item.get("item_name") == key1 for item in recent["items"])

    grouped = db_history.get_grouped_uploads(page=1, per_page=10)
    assert grouped["total_groups"] >= 1
    assert any(any(item.get("item_name") == key1 for item in g.get("items", [])) for g in grouped.get("groups", []))

    # Include this upload in the job's window.
    db_job_history.save_job_history(
        job_id,
        status="completed",
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    job_uploads = db_history.get_uploads_for_job(job_id)
    assert any(u.get("item_name") == key1 for u in job_uploads)

    # --- Telemetry tables ---
    db_stats.record_system_stats(
        cpu=10.0,
        mem=20.0,
        up=1.0,
        down=2.0,
        total_sent=100.0,
        total_recv=200.0,
        connections=5,
        disk_percent=30.0,
        disk_free_gb=50.0,
        disk_read=0.5,
        disk_write=0.6,
        errors_in=0,
        errors_out=0,
        drops_in=0,
        drops_out=0,
        swap_percent=0.0,
        load_avg=0.1,
    )
    assert (
        db_stats.record_interface_stats(
            [{"name": "eth0", "upload": 1.0, "download": 2.0}],
        )
        is True
    )
    sys_hist = db_stats.get_system_stats_history(limit=10)
    assert len(sys_hist.get("cpu", [])) >= 1
    iface_hist = db_stats.get_interface_stats_history(limit=10)
    assert "eth0" in iface_hist
    db_stats.prune_system_stats(max_records=1)

    # --- Aggregate/stat helpers ---
    hourly = db_stats.get_hourly_upload_stats()
    assert "tv_last_hour" in hourly
    detailed = db_stats.get_detailed_stats()
    assert "uploads" in detailed

    all_stats = db_stats.get_all_upload_stats()
    assert isinstance(all_stats, dict)

    # --- Manual mark + deletes ---
    key2 = "Movie.Title.2024.1080p.WEB-DL.mkv"
    assert db_uploads.mark_as_uploaded([key2], ["geek"], itype="Movie") == 1
    assert db_uploads.mark_as_uploaded([key2], ["geek"], itype="Movie") == 0

    assert db_uploads.delete_upload_item(key1) is True
    assert db_uploads.bulk_delete_upload_items([key2]) >= 1

    assert db_job_history.delete_job_history([job_id]) >= 1

@pytest.mark.usefixtures("isolated_sqlite_db")
def test_get_duplicate_status_batch_chunks_large_sqlite_expression() -> None:
    exact_key = "library/Show.Example.S01E01.1080p.WEB-DL.mkv"
    suffix_key = "nested/Show.Example.S01E02.1080p.WEB-DL.mkv"

    db_uploads.record_nntp_success(exact_key, 100, "TV Episode")
    assert (
        db_uploads.update_db_destination(
            dest="geek",
            _name=exact_key,
            size=100,
            key=exact_key,
            itype="TV Episode",
            duration=1.0,
            speed_bps=100.0,
            server_name="testsrv",
            status="success",
        )
        is True
    )

    db_uploads.record_nntp_success(suffix_key, 101, "TV Episode")
    assert (
        db_uploads.update_db_destination(
            dest="omg",
            _name=suffix_key,
            size=101,
            key=suffix_key,
            itype="TV Episode",
            duration=1.0,
            speed_bps=100.0,
            server_name="testsrv",
            status="success",
        )
        is True
    )

    item_keys = [f"bulk/Show.Example.S01E{i:04d}.1080p.WEB-DL.mkv" for i in range(1200)]
    item_keys[0] = exact_key
    item_keys[1] = "Show.Example.S01E02.1080p.WEB-DL.mkv"

    dupes = db_ledger.destinations_for_batch(item_keys, ["geek", "omg"])

    assert dupes[exact_key]["geek"] is not None
    assert dupes[exact_key]["omg"] is None
    assert dupes["Show.Example.S01E02.1080p.WEB-DL.mkv"]["omg"] is not None
    assert dupes["Show.Example.S01E02.1080p.WEB-DL.mkv"]["geek"] is None

@pytest.mark.usefixtures("isolated_sqlite_db")
def test_database_e2e_dual_upload_race_does_not_crash() -> None:
    """Simulate normal+priority dual upload recording in parallel threads."""

    key = "Test.Show/Test.Show.S01E01.1080p.WEB-DL.mkv"
    barrier = threading.Barrier(2)

    def _worker(dest_id: str) -> None:
        barrier.wait()
        db_uploads.record_nntp_success(key, 100, "TV Episode")
        ok = db_uploads.update_db_destination(
            dest=dest_id,
            _name=key,
            size=100,
            key=key,
            itype="TV Episode",
            duration=1.0,
            speed_bps=123.0,
            server_name="testsrv",
            status="success",
        )
        assert ok is True

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_worker, "idx_a")
        f2 = ex.submit(_worker, "idx_b")
        f1.result()
        f2.result()

    with session_scope() as session:
        uploads = session.query(Upload).filter_by(item_name=key).all()
        assert len(uploads) == 1
        upload_id = uploads[0].id
        results = session.query(UploadResult).filter_by(upload_id=upload_id).all()
        assert {r.indexer_id for r in results} == {"idx_a", "idx_b"}

    # Ensure job_history uniqueness is intact and operations still work.
    db_job_history.save_job_history("job-race", category="tv", status="running")
    with session_scope() as session:
        assert session.query(JobHistory).filter_by(job_id="job-race").count() == 1


# ============================================================
#  get_grouped_upload_errors / _fingerprint_upload_error
# ============================================================


def test_fingerprint_upload_error_collapses_variable_parts() -> None:
    """Two failures with the same underlying cause carry different paths/counts;
    the signature must match across those so grouping isn't defeated by the
    exact bytes/filename that differ per attempt."""

    a = db_issues._fingerprint_upload_error(r"Auth failed for C:\incoming\Show.S01E01.mkv (attempt 3)")
    b = db_issues._fingerprint_upload_error(r"Auth failed for C:\incoming\Movie.2024.mkv (attempt 7)")
    assert a == b

    assert db_issues._fingerprint_upload_error("") == "<empty>"
    assert db_issues._fingerprint_upload_error(None) == "<empty>"

    # Distinct error classes must not collapse into the same signature.
    assert db_issues._fingerprint_upload_error("Auth failed") != db_issues._fingerprint_upload_error("Rate limited")


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_get_grouped_upload_errors_groups_by_indexer_and_signature() -> None:
    """Repeated failures with the same shape collapse into one counted issue;
    a differently-shaped failure and a successful upload must not join it."""

    for idx, name in enumerate(
        [
            "Show.A.S01E01.1080p.WEB-DL.mkv",
            "Show.B.S01E02.1080p.WEB-DL.mkv",
            "Show.C.S01E03.1080p.WEB-DL.mkv",
        ]
    ):
        db_uploads.record_nntp_success(name, 100, "TV Episode")
        assert (
            db_uploads.update_db_destination(
                dest="geek",
                _name=name,
                size=100,
                key=name,
                itype="TV Episode",
                status="failed",
                error=f"Auth failed for /srv/incoming/{name} (attempt {idx})",
            )
            is True
        )

    # A different failure shape on the same indexer: a separate issue.
    other_name = "Show.D.S01E04.1080p.WEB-DL.mkv"
    db_uploads.record_nntp_success(other_name, 100, "TV Episode")
    assert (
        db_uploads.update_db_destination(
            dest="geek",
            _name=other_name,
            size=100,
            key=other_name,
            itype="TV Episode",
            status="failed",
            error="Rate limited, retry later",
        )
        is True
    )

    # A successful result must never contribute an issue row.
    ok_name = "Show.E.S01E05.1080p.WEB-DL.mkv"
    db_uploads.record_nntp_success(ok_name, 100, "TV Episode")
    assert (
        db_uploads.update_db_destination(
            dest="geek",
            _name=ok_name,
            size=100,
            key=ok_name,
            itype="TV Episode",
            status="success",
        )
        is True
    )

    result = db_issues.get_grouped_upload_errors(indexer_id="geek")
    assert result["total_issues"] == 2

    auth_issue = next(i for i in result["issues"] if i["sample_error"].startswith("Auth failed"))
    assert auth_issue["indexer_id"] == "geek"
    assert auth_issue["count"] == 3
    assert auth_issue["affected_item_count"] == 3
    assert auth_issue["first_seen"] is not None
    assert auth_issue["last_seen"] is not None

    rate_issue = next(i for i in result["issues"] if i["sample_error"].startswith("Rate limited"))
    assert rate_issue["count"] == 1

    # Filtering to an indexer with no failures returns an empty, not an error.
    empty = db_issues.get_grouped_upload_errors(indexer_id="omg")
    assert empty == {"issues": [], "total_issues": 0}


@pytest.mark.usefixtures("isolated_sqlite_db")
def test_mute_upload_issue_flags_without_removing_from_results() -> None:
    """Muting a known-issue group must silence it (via the `muted` flag and the
    include_muted=False filter) without deleting the underlying failure history -
    unmuting must bring it right back."""

    name = "Show.F.S01E06.1080p.WEB-DL.mkv"
    db_uploads.record_nntp_success(name, 100, "TV Episode")
    db_uploads.update_db_destination(
        dest="geek", _name=name, size=100, key=name, itype="TV Episode",
        status="failed", error="Auth failed for /srv/incoming/show.f.mkv",
    )

    before = db_issues.get_grouped_upload_errors(indexer_id="geek")
    assert before["total_issues"] == 1
    assert before["issues"][0]["muted"] is False

    signature = before["issues"][0]["signature"]
    assert db_issues.mute_upload_issue("geek", signature) is True

    # Muting is idempotent - calling it again must not raise or duplicate the row.
    assert db_issues.mute_upload_issue("geek", signature) is True

    flagged = db_issues.get_grouped_upload_errors(indexer_id="geek")
    assert flagged["total_issues"] == 1
    assert flagged["issues"][0]["muted"] is True

    filtered = db_issues.get_grouped_upload_errors(indexer_id="geek", include_muted=False)
    assert filtered["issues"] == []

    assert db_issues.unmute_upload_issue("geek", signature) is True
    # Unmuting an already-unmuted pair must not raise.
    assert db_issues.unmute_upload_issue("geek", signature) is True

    restored = db_issues.get_grouped_upload_errors(indexer_id="geek", include_muted=False)
    assert restored["total_issues"] == 1
    assert restored["issues"][0]["muted"] is False
