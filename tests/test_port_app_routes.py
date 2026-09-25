# ruff: noqa: F403,F405

"""App route fixes ported from the live server (area app-routes)."""

import tarfile

from tests.support import *

import app_base
from logic import category_overrides
from logic.queueing import ProcessingJobRequest as _RealProcessingJobRequest
from logic.services import UploadService


# app-routes-02: DELETE /api/uploads/queue/{job_id}/active-items


class _ActiveJobService:
    def __init__(self, *, removed: bool, job):
        self.removed = removed
        self.job = job
        self.calls: list[tuple[str, str]] = []

    def remove_active_job_item(self, job_id, path):
        self.calls.append((job_id, path))
        return self.removed

    def get_job(self, _job_id):
        return self.job


def test_remove_active_job_item_route_removes_the_path() -> None:
    service = _ActiveJobService(removed=True, job={"status": "running"})
    req = app_mod.RemoveQueuedJobItemRequest(path="/media/tv/Show.S01E02.mkv")

    result = _run_async(app_mod.remove_active_job_item_route("job-1", req, service=service))

    assert result == {"status": "removed", "job_id": "job-1", "path": "/media/tv/Show.S01E02.mkv"}
    assert service.calls == [("job-1", "/media/tv/Show.S01E02.mkv")]


def test_remove_active_job_item_route_reports_why_it_failed() -> None:
    cases = [
        ("", _ActiveJobService(removed=False, job=None), 400, "No path provided"),
        ("/x", _ActiveJobService(removed=False, job=None), 404, "Job not found"),
        ("/x", _ActiveJobService(removed=False, job={"status": "completed"}), 409, "Job is not active"),
        ("/x", _ActiveJobService(removed=False, job={"status": "paused"}), 404, "Path not found in active job"),
    ]
    for path, service, status, detail in cases:
        req = app_mod.RemoveQueuedJobItemRequest(path=path)
        with pytest.raises(HTTPException) as exc:
            _run_async(app_mod.remove_active_job_item_route("job-1", req, service=service))
        assert (exc.value.status_code, exc.value.detail) == (status, detail)


@pytest.mark.skipif(
    not hasattr(UploadService, "remove_active_job_item"),
    reason="contract: queue-processing adds UploadService.remove_active_job_item(job_id, path) -> bool",
)
def test_upload_service_implements_remove_active_job_item() -> None:
    assert callable(UploadService.remove_active_job_item)


# app-routes-03: POST /api/system/backup/create


def _make_backup_source(root: Path) -> None:
    (root / "logic").mkdir(parents=True)
    (root / "app.py").write_text("print('app')\n", encoding="utf-8")
    (root / "logic" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".env").write_text("PLACEHOLDER=1\n", encoding="utf-8")
    (root / ".config" / "nzbpostarr").mkdir(parents=True)
    (root / ".config" / "nzbpostarr" / "config.yaml").write_text("verbose: false\n", encoding="utf-8")
    (root / "data" / "history").mkdir(parents=True)
    (root / "data" / "history" / "usenet_uploads.db").write_bytes(b"db")
    (root / "data" / "tmp").mkdir(parents=True)
    (root / "data" / "tmp" / "state.json").write_text("{}", encoding="utf-8")
    (root / "data" / "tmp" / "work.part01.rar").write_bytes(b"rar")
    for excluded in (".git", ".venv", ".local", "backups"):
        (root / excluded).mkdir()
        (root / excluded / "skip.txt").write_text("skip", encoding="utf-8")


def test_create_full_backup_archives_state_owner_only(monkeypatch, tmp_path) -> None:
    root = tmp_path / "nzbpostarr"
    _make_backup_source(root)
    backup_folder = tmp_path / "out"
    conf = SimpleNamespace(backup_folder=backup_folder, log_db=root / "data" / "history" / "usenet_uploads.db")
    monkeypatch.setattr(config_mod, "APP_ROOT", root)
    monkeypatch.setattr(config_mod, "get_config", lambda: conf)

    result = _run_async(app_mod.create_full_backup(app_mod.CreateBackupRequest()))

    archive = Path(result["archive_path"])
    assert archive.parent == backup_folder
    assert result["archive_name"] == archive.name
    assert archive.name.startswith("nzbpostarr_full_backup_") and archive.name.endswith(".tar.gz")
    assert result["size_bytes"] == archive.stat().st_size > 0
    assert result["skip_tmp_contents"] is True
    if os.name != "nt":
        assert archive.stat().st_mode & 0o777 == 0o600

    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
        manifest = json.loads(tar.extractfile("nzbpostarr/backup-manifest.json").read())
    for expected in (
        "nzbpostarr/app.py",
        "nzbpostarr/logic/mod.py",
        "nzbpostarr/.env",
        "nzbpostarr/.config/nzbpostarr/config.yaml",
        "nzbpostarr/data/history/usenet_uploads.db",
        "nzbpostarr/data/tmp/state.json",
    ):
        assert expected in names
    assert "nzbpostarr/data/tmp/work.part01.rar" not in names
    for excluded in (".git", ".venv", ".local", "backups"):
        assert not any(name.startswith(f"nzbpostarr/{excluded}") for name in names)
    assert result["file_count"] == len(names)
    assert manifest["skip_tmp_contents"] is True
    assert manifest["archive_name"] == archive.name


def test_create_full_backup_can_include_tmp_contents(monkeypatch, tmp_path) -> None:
    root = tmp_path / "nzbpostarr"
    _make_backup_source(root)
    conf = SimpleNamespace(backup_folder=tmp_path / "out", log_db=root / "data" / "history" / "usenet_uploads.db")
    monkeypatch.setattr(config_mod, "APP_ROOT", root)
    monkeypatch.setattr(config_mod, "get_config", lambda: conf)

    result = app_mod._create_full_backup_archive(skip_tmp_contents=False)

    with tarfile.open(result["archive_path"], "r:gz") as tar:
        assert "nzbpostarr/data/tmp/work.part01.rar" in tar.getnames()


def test_create_full_backup_failure_is_a_readable_500(monkeypatch) -> None:
    def _boom(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app_mod, "_create_full_backup_archive", _boom)
    monkeypatch.setattr(sys.modules["app_g2"], "_create_full_backup_archive", _boom)

    with pytest.raises(HTTPException) as exc:
        _run_async(app_mod.create_full_backup(app_mod.CreateBackupRequest(skip_tmp_contents=False)))
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to create backup: disk full"


# app-routes-05: /api/pending/category-overrides


@pytest.fixture
def _override_store(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod, "APP_ROOT", tmp_path)
    monkeypatch.setattr(category_overrides, "_path", None)
    monkeypatch.setattr(category_overrides, "_store", {})
    monkeypatch.setattr(category_overrides, "_loaded", False)
    return tmp_path


def test_category_override_round_trip_persists_under_data(_override_store) -> None:
    saved = app_mod.set_category_override(app_mod.CategoryOverrideRequest(key=" ext:Movies:Some.Show.S01 ", category=" TV "))

    assert saved == {"status": "success", "key": "ext:Movies:Some.Show.S01", "category": "tv"}
    stored = _override_store / "data" / "category_overrides.json"
    assert json.loads(stored.read_text(encoding="utf-8")) == {"ext:Movies:Some.Show.S01": "tv"}
    assert app_mod.get_category_overrides() == {"overrides": {"ext:Movies:Some.Show.S01": "tv"}}

    cleared = app_mod.set_category_override(app_mod.CategoryOverrideRequest(key="ext:Movies:Some.Show.S01", category=None))

    assert cleared == {"status": "success", "key": "ext:Movies:Some.Show.S01", "category": None}
    assert app_mod.get_category_overrides() == {"overrides": {}}
    assert json.loads(stored.read_text(encoding="utf-8")) == {}


def test_category_override_rejects_a_blank_key(_override_store) -> None:
    with pytest.raises(HTTPException) as exc:
        app_mod.set_category_override(app_mod.CategoryOverrideRequest(key="  ", category="tv"))
    assert (exc.value.status_code, exc.value.detail) == (400, "Missing item key")


def test_category_overrides_read_the_legacy_root_file(_override_store) -> None:
    (_override_store / "category_overrides.json").write_text(json.dumps({"ext:A:b": "anime"}), encoding="utf-8")

    assert app_mod.get_category_overrides() == {"overrides": {"ext:A:b": "anime"}}


# app-routes-06: Refresh rebuilds the pending snapshot synchronously


class _SyncIndex:
    def __init__(self, snapshot):
        self.state = {"snapshot": snapshot, "ready": True, "refreshing": False}
        self.reasons: list[str] = []

    def get_state(self):
        return dict(self.state)

    def set_snapshot(self, data):
        self.state["snapshot"] = data

    def request_refresh(self, reason: str = "manual"):
        self.reasons.append(reason)


def test_pending_refresh_returns_the_fresh_scan(monkeypatch) -> None:
    stale = _make_pending_snapshot(summary={"total": 1})
    fresh = _make_pending_snapshot(summary={"total": 2})
    fresh["cached_at"] = 999.0
    index = _SyncIndex(stale)
    monkeypatch.setattr(app_mod, "_pending_index", index)
    monkeypatch.setattr(app_mod, "_scan_pending_all", lambda: fresh)
    monkeypatch.setattr(app_mod, "get_config", lambda: SimpleNamespace(enable_anime_checking=False))

    result = app_mod.get_pending_items(refresh=True)

    assert result["cached_at"] == 999.0
    assert index.reasons == []


def test_pending_refresh_waits_for_an_in_flight_rebuild(monkeypatch) -> None:
    fresh = _make_pending_snapshot()
    fresh["cached_at"] = 42.0
    monkeypatch.setattr(app_mod, "_pending_index", _SyncIndex(_make_pending_snapshot()))
    monkeypatch.setattr(app_mod, "_scan_pending_all", lambda: fresh)
    lock = threading.Lock()
    monkeypatch.setattr(app_mod, "_pending_refresh_lock", lock)

    lock.acquire()
    releaser = threading.Timer(0.2, lock.release)
    releaser.start()
    try:
        result = app_mod._refresh_pending_snapshot_now(reason="test")
    finally:
        releaser.join()

    assert result["cached_at"] == 42.0


# app-routes-07: Force Upload skips pre-flight pack expansion


def test_force_upload_passes_force_and_skip_pack_expansion(monkeypatch, tmp_path) -> None:
    captured: list = []

    class _Service:
        def start_processing_job_requests(self, requests, **_kwargs):
            captured.extend(requests)
            return ["job-1"]

    movie = tmp_path / "Movie.2020.mkv"
    movie.write_bytes(b"x")
    items = [{"path": str(movie), "category": "movies", "itype": "Movie"}]

    for force, expected in ((None, None), (True, True), (False, False)):
        captured.clear()
        req = app_mod.ForceUploadRequest(items=items, force=force)
        _run_async(app_mod.force_upload_items(req, service=_Service()))
        assert captured[0].force is expected
        assert captured[0].skip_pack_expansion is True


def test_real_processing_job_request_gets_skip_pack_expansion() -> None:
    assert _RealProcessingJobRequest(category="tv").skip_pack_expansion is False


# app-routes-08: no one-hour cooldown on the anime background check


def test_anime_check_has_no_cooldown_after_an_empty_batch(monkeypatch) -> None:
    started: list[str] = []

    class _Thread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self.name = name

        def start(self):
            started.append(self.name)

        def is_alive(self):
            return False

    data = _make_pending_snapshot()
    monkeypatch.setattr(app_mod, "_pending_index", _SyncIndex(data))
    monkeypatch.setattr(app_mod, "get_config", lambda: SimpleNamespace(enable_anime_checking=True))
    uncached_calls: list[object] = []
    monkeypatch.setattr(
        pending_snapshot_mod,
        "collect_uncached_anime_check_names",
        lambda _data: uncached_calls.append(_data) or ["Some Title"],
    )
    monkeypatch.setattr("logic.classify.anime.check_titles_batch", lambda names: {n: None for n in names})
    monkeypatch.setattr(app_mod.threading, "Thread", _Thread)
    monkeypatch.setattr(app_mod, "_anime_check_thread", None)
    monkeypatch.setattr(app_mod, "_anime_check_inflight", False)

    app_mod._background_anime_check(data)
    app_mod.get_pending_items()

    assert started == ["pending-anime-check"]
    assert uncached_calls, "the patched pending_snapshot owner was never consulted"
    assert not hasattr(app_base, "_ANIME_CHECK_COOLDOWN_S")


# /api/pending/children payload shape used by the queue page


def test_pending_children_returns_children_and_child_count(monkeypatch) -> None:
    monkeypatch.setattr(app_mod, "_pending_index", _SyncIndex(_make_pending_snapshot()))

    assert app_mod.get_pending_children(key="ext:missing:x", path="") == {"children": [], "child_count": 0}


# History/delete routes turn DB errors into a readable 503


def test_history_routes_report_database_errors_as_503(monkeypatch) -> None:
    def _down(*_args, **_kwargs):
        raise DatabaseOperationalError("database is locked")

    for name in (
        "get_recent_uploads",
        "get_grouped_uploads",
        "get_group_upload_items",
        "get_grouped_upload_errors",
        "get_job_history",
        "get_uploads_for_job",
        "delete_job_history",
        "delete_upload_item",
        "bulk_delete_upload_items",
    ):
        monkeypatch.setattr(db, name, _down)

    class _Request:
        async def json(self):
            return {"job_ids": ["job-1"]}

    calls = [
        lambda: app_mod.get_recent(),
        lambda: app_mod.get_grouped(),
        lambda: app_mod.get_grouped_items("show"),
        lambda: app_mod.get_grouped_errors(),
        lambda: _run_async(app_mod.get_history()),
        lambda: _run_async(app_mod.get_job_uploads("job-1")),
        lambda: _run_async(app_mod.delete_job_history(_Request())),
        lambda: _run_async(app_mod.delete_upload_item("Some.Item")),
        lambda: _run_async(app_mod.bulk_delete_upload_items(app_mod.BulkDeleteRequest(item_names=["a"]))),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 503
        assert isinstance(exc.value.detail, str)
        assert "database is locked" in exc.value.detail


# Queue page error beacon


def test_queue_error_beacon_logs_one_line_and_returns_204(monkeypatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(app_mod.logger, "warning", lambda msg, *a, **k: messages.append(msg))

    response = _run_async(app_mod.queue_error_beacon(title="Boom\nforged line", detail="x" * 5000, rev="abc"))

    assert response.status_code == 204
    assert len(messages) == 1
    assert "\n" not in messages[0]
    assert "rev=abc" in messages[0]
    assert len(messages[0]) < 2000
