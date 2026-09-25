# ruff: noqa: F403,F405

"""NZBPostarr queue tests."""

import psutil

from logic.queueing import ProcessingJobRequest
from tests.support import *

def test_job_names_use_category_and_item_count(tmp_path) -> None:
    # The client's server names jobs '<Category> - N items' (handoff D01).
    service = _make_queue_service_stub(tmp_path)
    release_dir = tmp_path / "0-Pokemon Horizon - Singles"
    episode_one = release_dir / "Pokemon.S20E01.1080p.WEBRip.mkv"
    episode_two = release_dir / "Pokemon.S20E02.1080p.WEBRip.mkv"

    assert service._default_job_name("mixed", 2, [episode_one, episode_two]) == "Mixed - 2 items"
    assert service._default_job_name("anime", 1, [release_dir]) == "Anime - 1 item"
    assert service._default_job_name("mixed", 123) == "Mixed - 123 items"
    assert service._default_job_name("tv", 0) == "TV"


def test_upload_service_passes_target_paths_to_processing_run_job():
    """Force/targeted uploads should pass selected paths down to processing.run_job."""
    from unittest.mock import patch

    from logic.services import UploadService

    called = {}

    def fake_run_job(category, **kwargs):
        called["category"] = category
        called["paths"] = kwargs.get("paths")

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    service = UploadService()
    # Keep the singleton clean for this test
    with service._lock:
        service._jobs = {}

    with patch("logic.services.processing.run_job", side_effect=fake_run_job):
        with patch("logic.queueing.threading.Thread", ImmediateThread):
            with patch("logic.queueing.database.save_job_history"):
                service.start_upload_job(
                    category="movies",
                    paths=["/tmp/example.mkv"],
                    reuse_running=False,
                )

    assert called["category"] == "movies"
    assert called["paths"] == ["/tmp/example.mkv"]

def test_run_command_stop_is_responsive_for_quiet_process() -> None:
    from core.utils import run_command

    job = {"stop_requested": False}
    stop_timer = threading.Timer(0.2, lambda: job.__setitem__("stop_requested", True))
    stop_timer.start()
    started = time.monotonic()
    try:
        success, _output = run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            "test-stop",
            job=job,
            quiet=True,
        )
    finally:
        stop_timer.cancel()

    assert success is False
    assert time.monotonic() - started < 2.0

def test_run_job_marks_missing_tools_as_failed(monkeypatch) -> None:
    from types import SimpleNamespace

    from core.utils import set_thread_job
    from logic import processing

    job = {"job_id": "job-tools", "status": "running", "progress": "Starting..."}
    conf = SimpleNamespace(rar_path="missing-rar", parpar_path="missing-parpar", nyuu_path="missing-nyuu")

    monkeypatch.setattr(processing, "get_config", lambda: conf)
    set_thread_job(job)
    processing.run_job("tv", paths=["/tmp/Show.Name.S01E01.mkv"])

    assert job["status"] == "failed"
    assert "Missing required tools" in job["progress"]

def test_validation_stop_request_preserves_current_item_for_resume(tmp_path, monkeypatch) -> None:
    from core import registry
    from core.utils import reset_thread_job, set_thread_job
    from logic import processing

    item_path = _touch(tmp_path / "Current.Movie.2026.mkv")
    checkpoints: list[dict[str, object]] = []
    job: dict[str, object] = {
        "job_id": "job-validation-stop",
        "status": "running",
        "target_paths": [str(item_path)],
    }

    def persist_checkpoint() -> None:
        checkpoints.append(
            {
                "current": job.get("_current_item_path"),
                "remaining": list(job.get("target_paths") or []),
            }
        )

    job["_persist_callback"] = persist_checkpoint
    conf = SimpleNamespace(
        process_tv_episodes=True,
        folder_size_limit_gb=99,
        folder_size_limit_enabled=True,
        file_size_limit_gb=0,
        file_size_limit_enabled=True,
        item_limit_per_category=None,
        skip_files={"enabled": False},
    )
    resolution = SimpleNamespace(
        category="movies",
        source_path=item_path,
        queue_paths=(item_path,),
        content_flags=(),
        detection_method="test",
        override_note="",
        ignored_paths=(),
    )

    def stop_during_validation(path, **_kwargs):
        job["stop_requested"] = True
        return processing.QueueItemValidation(
            "stopped",
            path,
            "movies",
            "Movies",
            message="Validation stopped by request",
        )

    monkeypatch.setattr(processing, "get_config", lambda: conf)
    monkeypatch.setattr(processing, "check_tools", lambda _conf: True)
    monkeypatch.setattr(processing, "get_configured_folders", lambda *_args, **_kwargs: [tmp_path])
    monkeypatch.setattr(processing, "resolve_explicit_path", lambda *_args, **_kwargs: resolution)
    monkeypatch.setattr(processing, "_build_duplicate_prefetch_state", lambda *_args, **_kwargs: ({}, {}, None))
    monkeypatch.setattr(processing, "_run_validation_with_timeout", stop_during_validation)
    monkeypatch.setattr(registry, "get_available_categories", lambda: [{"id": "movies"}])

    token = set_thread_job(job)
    try:
        processing.run_job("movies", paths=[str(item_path)])
    finally:
        reset_thread_job(token)

    assert job["status"] == "stopped"
    assert job["_current_item_path"] == str(item_path)
    assert checkpoints[-1] == {"current": str(item_path), "remaining": []}

def test_upload_service_requeues_stopped_job_after_restart(tmp_path) -> None:
    from logic.services import UploadService

    service = object.__new__(UploadService)
    service._lock = threading.Lock()
    service._jobs = {}
    service._processes = {}
    service._queue_processing_paused = False
    service._jobs_state_path = tmp_path / "job_queue_state.json"
    service._jobs_state_backup_path = tmp_path / "job_queue_state.json.bak"

    payload = {
        "queue_processing_paused": False,
        "jobs": [
            {
                "job_id": "job-123",
                "category": "movies",
                "status": "stopped",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "progress": "Stopped by user",
                "test_mode": False,
                "display_name": "Movies - 2 items",
                "run_after": None,
                "kwargs": {},
                "paths": [
                    str(tmp_path / "Current.Movie.mkv"),
                    str(tmp_path / "Next.Movie.mkv"),
                ],
            }
        ],
    }
    service._jobs_state_path.write_text(json.dumps(payload), encoding="utf-8")

    with service._lock:
        service._restore_jobs_from_disk_locked()

    restored = service._jobs["job-123"]
    assert restored["status"] == "queued"
    assert restored["progress"] == "Recovered after restart - waiting for queue resume."
    assert service._queue_processing_paused is True
    assert service._get_job_target_paths(restored) == payload["jobs"][0]["paths"]

def test_upload_service_serializes_stopped_job_with_current_item_first(tmp_path) -> None:
    from logic.services import UploadService

    service = object.__new__(UploadService)
    job = {
        "job_id": "job-serialize",
        "category": "movies",
        "status": "stopped",
        "job_type": "processing",
        "has_explicit_paths": True,
        "target_paths": [str(tmp_path / "Next.Movie.mkv")],
        "_current_item_path": str(tmp_path / "Current.Movie.mkv"),
        "_kwargs": {},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "progress": "Stopping...",
    }

    payload = service._serialize_job_for_persistence(job)

    assert payload is not None
    assert payload["status"] == "stopped"
    assert payload["paths"] == [
        str(tmp_path / "Current.Movie.mkv"),
        str(tmp_path / "Next.Movie.mkv"),
    ]
    assert job["progress"] == "Stopped by user"

def test_upload_service_serializes_running_job_for_crash_recovery(tmp_path) -> None:
    from logic.services import UploadService

    service = object.__new__(UploadService)
    current_path = str(tmp_path / "Current.Movie.mkv")
    validating_path = str(tmp_path / "Validating.Movie.mkv")
    next_path = str(tmp_path / "Next.Movie.mkv")
    job = {
        "job_id": "job-running",
        "category": "movies",
        "status": "running",
        "job_type": "processing",
        "has_explicit_paths": True,
        "target_paths": [next_path],
        "_current_item_path": current_path,
        "_inflight_item_paths": [current_path, validating_path],
        "_kwargs": {
            "target_indexer_ids": ["nzbgeek"],
            "enable_duplicate_check": False,
            "force": True,
        },
        "started_at": datetime.now(timezone.utc).isoformat(),
        "progress": "Validating current item",
    }

    payload = service._serialize_job_for_persistence(job)

    assert payload is not None
    assert payload["status"] == "running"
    assert payload["paths"] == [current_path, validating_path, next_path]
    assert payload["kwargs"]["target_indexer_ids"] == ["nzbgeek"]
    assert payload["kwargs"]["enable_duplicate_check"] is False
    assert payload["kwargs"]["force"] is True

def test_upload_service_requeues_running_job_after_crash(tmp_path) -> None:
    from logic.services import UploadService

    service = object.__new__(UploadService)
    service._lock = threading.Lock()
    service._jobs = {}
    service._processes = {}
    service._queue_processing_paused = False
    service._jobs_state_path = tmp_path / "job_queue_state.json"
    service._jobs_state_backup_path = tmp_path / "job_queue_state.json.bak"
    paths = [str(tmp_path / "Current.Movie.mkv"), str(tmp_path / "Next.Movie.mkv")]
    payload = {
        "queue_processing_paused": False,
        "jobs": [
            {
                "job_id": "job-crashed",
                "category": "movies",
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "progress": "Validating current item",
                "test_mode": False,
                "kwargs": {"target_indexer_ids": ["nzbgeek"], "force": True},
                "paths": paths,
            }
        ],
    }
    service._jobs_state_path.write_text(json.dumps(payload), encoding="utf-8")

    with service._lock:
        service._restore_jobs_from_disk_locked()

    restored = service._jobs["job-crashed"]
    assert restored["status"] == "queued"
    assert restored["progress"] == "Recovered after restart - re-queued."
    assert service._get_job_target_paths(restored) == paths
    assert restored["_kwargs"]["target_indexer_ids"] == ["nzbgeek"]
    assert restored["_kwargs"]["force"] is True

def test_queue_lifecycle_stop_and_restart_restores_manual_resume_state(tmp_path, monkeypatch) -> None:
    import time as _time

    from logic import queueing

    state_root = tmp_path
    first_path = str(tmp_path / "Current.Movie.mkv")
    second_path = str(tmp_path / "Next.Movie.mkv")

    monkeypatch.setattr(queueing, "get_config", lambda: SimpleNamespace(script_dir=state_root))
    monkeypatch.setattr(queueing.database, "db_load_queue", lambda: [])
    monkeypatch.setattr(queueing.database, "db_clear_queue", lambda: 0)
    monkeypatch.setattr(queueing.database, "save_job_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(queueing.usenet_stream, "record_stream_monitor_job", lambda *args, **kwargs: None)

    class DummyQueueService(queueing.QueueServiceMixin):
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.started = threading.Event()
            self.stopped = threading.Event()
            self._initialize_queue_state()

        def _execute_processing_job(self, job, request):
            runtime_paths = list(request.paths)
            job["items_total"] = len(runtime_paths)
            job["items_processed"] = 0
            if runtime_paths:
                job["has_explicit_paths"] = True
                job["target_paths"] = runtime_paths[1:]
                job["_current_item_path"] = runtime_paths[0]
            self.started.set()
            while not job.get("stop_requested"):
                _time.sleep(0.01)
            job["status"] = "stopped"
            job["progress"] = "Stopped by user"
            self.stopped.set()

        def _execute_usenet_stream_job(self, *_args, **_kwargs):
            raise AssertionError("Stream execution should not be used in this queue lifecycle test")

    service = DummyQueueService()
    try:
        job_id = service.start_upload_job(
            category="movies",
            paths=[first_path, second_path],
            reuse_running=False,
        )
        assert service.started.wait(1.0)
        assert service.stop_job(job_id) is True
        assert service.stopped.wait(1.0)

        deadline = _time.time() + 1.0
        while _time.time() < deadline:
            job = service.get_job(job_id)
            if job and job.get("status") == "stopped":
                break
            _time.sleep(0.01)
        else:
            raise AssertionError("Job did not reach stopped state before persistence check")

        # The background job thread flips the in-memory status to "stopped" (above) and only
        # afterwards acquires the lock to finalize and persist it to disk, on the same thread,
        # in `_launch_job`'s `run()`. Reading the state file right away races that write: the
        # file can still hold the older "stopping" snapshot, and on Windows a concurrent
        # `os.replace()` of the same path can transiently surface as PermissionError. Poll the
        # persisted content itself (tolerating that transient error) instead of trusting that
        # the in-memory flag above already implies the write landed.
        state_file = state_root / "data" / "state" / "job_queue_state.json"
        persisted = None
        deadline = _time.time() + 2.0
        while _time.time() < deadline:
            try:
                candidate = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _time.sleep(0.02)
                continue
            jobs = candidate.get("jobs") or []
            if jobs and jobs[0].get("status") == "stopped":
                persisted = candidate
                break
            _time.sleep(0.02)
        if persisted is None:
            raise AssertionError("Persisted job state did not reach 'stopped' before the deadline")
        assert persisted["jobs"][0]["status"] == "stopped"
        assert persisted["jobs"][0]["paths"] == [first_path, second_path]

        restored = DummyQueueService()
        try:
            restored_job = restored.get_job(job_id)
            assert restored_job is not None
            assert restored_job["status"] == "queued"
            assert restored._queue_processing_paused is True
            assert restored.get_queue_control_state()["paused"] is True
            assert restored._get_job_target_paths(restored_job) == [first_path, second_path]
            assert restored_job["progress"] == "Recovered after restart - waiting for queue resume."
        finally:
            restored._queue_scheduler_stop.set()
    finally:
        service._queue_scheduler_stop.set()

def test_resume_queue_requeues_resumable_stopped_job(tmp_path) -> None:
    service = _make_upload_service_stub(
        jobs={
            "job-stop": {
                "job_id": "job-stop",
                "category": "movies",
                "status": "stopped",
                "job_type": "processing",
                "has_explicit_paths": True,
                "target_paths": [str(tmp_path / "Resume.Movie.mkv")],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "progress": "Stopped by user",
            }
        },
        queue_paused=True,
    )

    started = threading.Event()
    service._try_start_queued = lambda: started.set()

    assert service.resume_queue() is True
    assert started.wait(0.1)
    assert service._queue_processing_paused is False
    assert service._jobs["job-stop"]["status"] == "queued"
    assert service._jobs["job-stop"]["progress"] == "Queued - waiting for current job to finish..."

def test_pausing_one_job_keeps_scheduler_lane(tmp_path, monkeypatch) -> None:
    # DECISIONS: pause never SIGSTOPs, and a paused job keeps the queue lane.
    service = _make_queue_service_stub(tmp_path)
    service._jobs = {
        "active": {
            "job_id": "active",
            "category": "movies",
            "status": "running",
            "started_at": "2026-01-01T00:00:00+00:00",
        },
        "next": {
            "job_id": "next",
            "category": "tv",
            "status": "queued",
            "started_at": "2026-01-01T00:00:01+00:00",
            "priority": 5,
        },
    }
    launched: list[str] = []

    def refuse_suspend(_process):
        raise AssertionError("pause must not suspend processes")

    monkeypatch.setattr(psutil.Process, "suspend", refuse_suspend)
    monkeypatch.setattr(service, "_launch_job", lambda job: launched.append(str(job["job_id"])))
    assert not hasattr(service, "_suspend_job_processes_locked")

    assert service.pause_job("active") is True
    service._try_start_queued()

    assert service._jobs["active"]["status"] == "paused"
    assert service._jobs["active"]["progress"] == "Paused by user"
    assert service._queue_processing_paused is False
    assert launched == []

def test_paused_python_job_holds_at_item_boundary_until_resumed(tmp_path, monkeypatch) -> None:
    from core.utils import wait_for_job_resume

    service = _make_queue_service_stub(tmp_path)
    service._jobs = {
        "active": {
            "job_id": "active",
            "category": "movies",
            "status": "running",
            "started_at": "2026-01-01T00:00:00+00:00",
        },
        "next": {
            "job_id": "next",
            "category": "tv",
            "status": "queued",
            "started_at": "2026-01-01T00:00:01+00:00",
        },
    }
    launched = threading.Event()

    def fake_launch(job):
        job["status"] = "running"
        launched.set()

    monkeypatch.setattr(service, "_launch_job", fake_launch)

    assert service.pause_job("active") is True
    assert service._jobs["active"]["status"] == "paused"
    assert service._jobs["active"]["pause_requested"] is True

    checkpoint_finished = threading.Event()

    def reach_checkpoint() -> None:
        wait_for_job_resume(service._jobs["active"], check_interval=0.01)
        checkpoint_finished.set()

    checkpoint_thread = threading.Thread(target=reach_checkpoint, daemon=True)
    checkpoint_thread.start()

    assert checkpoint_finished.wait(0.2) is False
    assert launched.is_set() is False

    assert service.resume_job("active") is True
    assert checkpoint_finished.wait(1.0)
    assert service._jobs["active"]["status"] == "running"
    assert service._jobs["active"]["pause_requested"] is False
    assert launched.is_set() is False

def test_resume_cancels_unacknowledged_python_pause(tmp_path, monkeypatch) -> None:
    service = _make_queue_service_stub(tmp_path)
    service._jobs = {
        "active": {
            "job_id": "active",
            "category": "movies",
            "status": "running",
            "started_at": "2026-01-01T00:00:00+00:00",
        }
    }

    assert service.pause_job("active") is True
    assert service.resume_job("active") is True
    assert service._jobs["active"]["status"] == "running"
    assert service._jobs["active"]["pause_requested"] is False
    assert "_pause_ack_callback" not in service._jobs["active"]

def test_paused_job_resume_waits_for_busy_lane_then_resumes(tmp_path, monkeypatch) -> None:
    service = _make_queue_service_stub(tmp_path)
    service._jobs = {
        "paused": {
            "job_id": "paused",
            "category": "movies",
            "status": "paused",
            "started_at": "2026-01-01T00:00:00+00:00",
            "pause_requested": True,
            "speed": "Paused",
            "current_stage": "PAUSED",
            "priority": 10,
        },
        "active": {
            "job_id": "active",
            "category": "tv",
            "status": "running",
            "started_at": "2026-01-01T00:00:01+00:00",
        },
    }
    assert service.resume_job("paused") is True
    assert service._jobs["paused"]["status"] == "paused"
    assert service._jobs["paused"]["resume_requested"] is True

    service._jobs["active"]["status"] = "completed"
    service._try_start_queued()

    assert service._jobs["paused"]["status"] == "running"
    assert service._jobs["paused"]["pause_requested"] is False

def test_scheduler_selects_highest_priority_job(tmp_path, monkeypatch) -> None:
    service = _make_queue_service_stub(tmp_path)
    service._jobs = {
        "older": {
            "job_id": "older",
            "category": "movies",
            "status": "queued",
            "started_at": "2026-01-01T00:00:00+00:00",
            "priority": 0,
        },
        "priority": {
            "job_id": "priority",
            "category": "tv",
            "status": "queued",
            "started_at": "2026-01-01T00:00:01+00:00",
            "priority": 20,
        },
    }
    launched: list[str] = []
    monkeypatch.setattr(service, "_launch_job", lambda job: launched.append(str(job["job_id"])))

    service._try_start_queued()

    assert launched == ["priority"]

def test_recently_finished_jobs_survive_service_restart(tmp_path) -> None:
    service = _make_queue_service_stub(tmp_path)
    finished_at = datetime.now(timezone.utc).isoformat()
    service._jobs = {
        "done": {
            "job_id": "done",
            "category": "movies",
            "status": "completed",
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": finished_at,
            "progress": "Finished in 1m",
            "progress_percent": 100,
            "items_processed": 2,
            "items_total": 2,
            "items_skipped": 0,
            "total_bytes": 123,
            "summary": {"duration": "1m", "processed": "2/2"},
        }
    }
    with service._lock:
        service._persist_jobs_locked()

    restored = _make_queue_service_stub(tmp_path)
    with restored._lock:
        restored._restore_jobs_from_disk_locked()

    job = restored._jobs["done"]
    assert job["status"] == "completed"
    assert job["finished_at"] == finished_at
    assert job["items_processed"] == 2
    assert job["summary"]["duration"] == "1m"

def test_failed_job_retry_is_single_shot_and_preserves_request(tmp_path, monkeypatch) -> None:
    service = _make_queue_service_stub(tmp_path)
    source_path = str(tmp_path / "Retry.Movie.mkv")
    service._jobs = {
        "failed-1": {
            "job_id": "failed-1",
            "category": "movies",
            "status": "failed",
            "job_type": "processing",
            "display_name": "Movie batch",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "attempt_count": 2,
            "retry_eligible": True,
            "_retry_request": {
                "kwargs": {
                    "job_type": "processing",
                    "test_mode": True,
                    "enable_duplicate_check": False,
                },
                "paths": [source_path],
            },
            "events": [],
        }
    }
    captured: dict[str, object] = {}

    def capture_start(category: str, **kwargs):
        captured["category"] = category
        captured["kwargs"] = kwargs
        return "retry-3"

    monkeypatch.setattr(service, "start_upload_job", capture_start)

    assert service.retry_job("failed-1") == (True, "retry-3", "queued")
    assert captured["category"] == "movies"
    assert captured["kwargs"] == {
        "job_type": "processing",
        "test_mode": True,
        "enable_duplicate_check": False,
        "reuse_running": False,
        "source": "retry",
        "retry_of": "failed-1",
        "attempt_count_base": 2,
        "job_name": "Movie batch retry",
        "paths": [source_path],
    }
    assert service._jobs["failed-1"]["retry_eligible"] is False
    assert service._jobs["failed-1"]["retried_as"] == "retry-3"
    assert service.retry_job("failed-1") == (False, None, "not-retryable")

def test_failed_job_retry_state_survives_restart(tmp_path) -> None:
    service = _make_queue_service_stub(tmp_path)
    retry_request = {
        "kwargs": {"job_type": "processing", "test_mode": False},
        "paths": [str(tmp_path / "Retry.Show.S01E01.mkv")],
    }
    service._jobs = {
        "failed-restart": {
            "job_id": "failed-restart",
            "category": "tv",
            "status": "failed",
            "job_type": "processing",
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "progress": "Indexer submission failed",
            "attempt_count": 1,
            "retry_eligible": True,
            "last_error": "Indexer submission failed",
            "_retry_request": retry_request,
            "events": [
                {
                    "at": "2026-01-01T00:00:01+00:00",
                    "type": "failed",
                    "message": "Indexer submission failed",
                }
            ],
        }
    }
    with service._lock:
        service._persist_jobs_locked()

    restored = _make_queue_service_stub(tmp_path)
    with restored._lock:
        restored._restore_jobs_from_disk_locked()

    job = restored._jobs["failed-restart"]
    assert job["retry_eligible"] is True
    assert job["attempt_count"] == 1
    assert job["last_error"] == "Indexer submission failed"
    assert job["_retry_request"] == retry_request
    assert job["events"][-1]["type"] == "failed"

def test_recently_finished_retention_prunes_expired_and_overflow_jobs(tmp_path) -> None:
    service = _make_queue_service_stub(tmp_path)
    now = datetime.now(timezone.utc)
    service._jobs = {
        f"recent-{index}": {
            "job_id": f"recent-{index}",
            "category": "movies",
            "status": "completed",
            "started_at": now.isoformat(),
            "finished_at": (now - timedelta(minutes=index)).isoformat(),
        }
        for index in range(105)
    }
    service._jobs["expired"] = {
        "job_id": "expired",
        "category": "movies",
        "status": "failed",
        "started_at": (now - timedelta(days=40)).isoformat(),
        "finished_at": (now - timedelta(days=40)).isoformat(),
    }

    with service._lock:
        service._persist_jobs_locked()

    assert len(service._jobs) == 100
    assert "recent-0" in service._jobs
    assert "recent-104" not in service._jobs
    assert "expired" not in service._jobs

def test_finalize_stopping_job_keeps_partial_job_stopped(tmp_path, monkeypatch) -> None:
    from logic import queueing

    service = _make_upload_service_stub()

    saved: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        queueing.database,
        "save_job_history",
        lambda job_id, **kwargs: saved.append((job_id, kwargs)),
    )
    monkeypatch.setattr(queueing.usenet_stream, "record_stream_monitor_job", lambda *args, **kwargs: None)

    job = {
        "job_id": "job-partial",
        "category": "movies",
        "status": "stopping",
        "stop_requested": True,
        "job_type": "processing",
        "has_explicit_paths": True,
        "target_paths": [str(tmp_path / "Next.Movie.mkv")],
        "_current_item_path": str(tmp_path / "Current.Movie.mkv"),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "items_processed": 1,
        "items_total": 600,
        "items_skipped": 0,
        "total_bytes": 0,
    }

    service._finalize_job_locked(
        job,
        duration_sec=12.0,
        clear_active_fields=True,
        remove_from_active=False,
    )

    assert job["status"] == "stopped"
    assert job["progress"] == "Stopped by user"
    assert service._queue_processing_paused is True
    assert job["target_paths"] == [
        str(tmp_path / "Current.Movie.mkv"),
        str(tmp_path / "Next.Movie.mkv"),
    ]
    assert saved[0][0] == "job-partial"
    assert saved[0][1]["status"] == "stopped"

def test_clear_queued_jobs_marks_stopping_job_for_removal(tmp_path, monkeypatch) -> None:
    from logic import queueing

    service = _make_upload_service_stub(
        jobs={
            "job-stop": {
                "job_id": "job-stop",
                "category": "movies",
                "status": "stopping",
                "stop_requested": True,
                "job_type": "processing",
                "has_explicit_paths": True,
                "target_paths": [str(tmp_path / "Resume.Movie.mkv")],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "items_total": 10,
                "items_processed": 1,
            }
        },
        queue_paused=True,
    )

    monkeypatch.setattr(queueing.database, "save_job_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(queueing.usenet_stream, "record_stream_monitor_job", lambda *args, **kwargs: None)

    assert service.clear_queued_jobs() == 1
    job = service._jobs["job-stop"]
    assert job["_clear_after_stop"] is True
    assert job["progress"] == "Stopping... queued for removal"

    service._finalize_job_locked(
        job,
        duration_sec=2.0,
        clear_active_fields=True,
        remove_from_active=False,
    )

    assert job["status"] == "cancelled"
    assert job["progress"] == "Cancelled (queue cleared)"

def test_clear_completed_jobs_removes_cancelled_jobs(tmp_path) -> None:
    service = _make_upload_service_stub(
        jobs={
            "job-cancelled": {
                "job_id": "job-cancelled",
                "category": "movies",
                "status": "cancelled",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            "job-complete": {
                "job_id": "job-complete",
                "category": "movies",
                "status": "completed",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    )

    assert service.clear_completed_jobs() == 2
    assert service._jobs == {}

def test_queue_endpoint_keeps_resumable_stopped_job_out_of_finished(tmp_path) -> None:
    job = {
        "job_id": "job-stop",
        "category": "movies",
        "status": "stopped",
        "job_type": "processing",
        "target_paths": [str(tmp_path / "Resume.Movie.mkv")],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    service = _make_upload_service_stub(jobs={"job-stop": job}, queue_paused=True)

    payload = jobs_api.get_queue(service=service)

    assert len(payload["queued"]) == 1
    assert payload["queued"][0]["job_id"] == "job-stop"
    assert payload["queued"][0]["has_explicit_paths"] is True
    assert payload["queued"][0]["target_path_count"] == 1
    assert "target_paths" not in payload["queued"][0]
    assert payload["finished"] == []
    assert payload["counts"]["queued"] == 1
    assert payload["counts"]["finished"] == 0

def test_compact_job_polling_omits_large_path_lists_and_bounds_events(tmp_path) -> None:
    paths = [str(tmp_path / f"Release.{index:05d}.mkv") for index in range(5_000)]
    events = [{"at": str(index), "type": "test", "message": f"event {index}"} for index in range(30)]
    job = {
        "job_id": "job-large",
        "category": "movies",
        "status": "running",
        "target_paths": paths,
        "events": events,
    }
    service = _make_upload_service_stub(jobs={"job-large": job})

    compact = service.get_active_jobs(compact=True)[0]

    assert compact["has_explicit_paths"] is True
    assert compact["target_path_count"] == 5_000
    assert compact["events"] == events[-3:]
    assert "target_paths" not in compact
    assert len(json.dumps(compact)) < 2_000

    active_items = service.get_active_job_items("job-large")
    assert active_items is not None
    assert len(active_items) == 5_000
    assert active_items[0]["path"] == paths[0]
    route_payload = jobs_api.get_active_job_items("job-large", service=service)
    assert route_payload["count"] == 5_000


def test_finished_job_items_are_available_for_the_current_session(tmp_path) -> None:
    paths = [str(tmp_path / "One.mkv"), str(tmp_path / "Two.mkv")]
    job = {
        "job_id": "job-finished",
        "category": "movies",
        "status": "completed",
        "_completed_paths": paths,
    }
    service = _make_upload_service_stub(jobs={"job-finished": job})

    items = service.get_finished_job_items("job-finished")

    assert items == [
        {"index": 1, "path": paths[0], "name": "One.mkv"},
        {"index": 2, "path": paths[1], "name": "Two.mkv"},
    ]
    route_payload = jobs_api.get_completed_job_items("job-finished", service=service)
    assert route_payload["count"] == 2
    assert route_payload["items"] == items


def test_resumed_job_keeps_prior_progress_when_processing_restarts(tmp_path) -> None:
    from logic.queueing import ProcessingJobRequest

    service = _make_upload_service_stub()
    job = {
        "job_id": "job-resume",
        "_resume_items_processed": 4,
        "_resume_items_total": 10,
        "_resume_items_skipped": 1,
    }
    request = ProcessingJobRequest(
        category="movies",
        paths=(str(tmp_path / "Five.mkv"), str(tmp_path / "Six.mkv")),
    )

    service._mark_processing_job_started(job, request)

    assert job["items_processed"] == 4
    assert job["items_total"] == 10
    assert job["items_skipped"] == 1
    assert job["progress_percent"] == 40
    assert job["progress"] == "Resuming - validating 2 remaining item(s)..."
    assert "_resume_items_processed" not in job


def test_queue_control_routes_report_confirmed_backend_state() -> None:
    class DummyService:
        def __init__(self) -> None:
            self.job = {"job_id": "job-1", "status": "running", "priority": 0}

        def pause_job(self, _job_id):
            self.job["status"] = "paused"
            return True

        def resume_job(self, _job_id):
            self.job["resume_requested"] = True
            return True

        def get_job(self, _job_id):
            return dict(self.job)

        def set_job_priority(self, _job_id, priority):
            self.job["priority"] = max(-100, min(100, int(priority)))
            return True, self.job["priority"]

    service = DummyService()

    paused = _run_async(jobs_api.pause_upload("job-1", service=service))
    resumed = _run_async(jobs_api.resume_upload("job-1", service=service))
    prioritized = _run_async(
        jobs_api.set_queued_job_priority(
            "job-1",
            jobs_api.QueuePriorityRequest(priority=250),
            service=service,
        )
    )

    assert paused["status"] == "paused"
    assert "scheduler lane" in paused["message"]
    assert resumed["status"] == "paused"
    assert "queued to resume" in resumed["message"]
    assert prioritized == {
        "status": "updated",
        "job_id": "job-1",
        "priority": 100,
    }

def test_retry_route_reports_new_job_and_conflicts() -> None:
    class DummyService:
        def retry_job(self, job_id):
            if job_id == "missing":
                return False, None, "not-found"
            if job_id == "complete":
                return False, None, "not-retryable"
            return True, "retry-2", "queued"

    service = DummyService()

    queued = _run_async(jobs_api.retry_upload("failed-1", service=service))
    assert queued == {
        "status": "queued",
        "job_id": "retry-2",
        "retry_of": "failed-1",
        "message": "Retry queued as retry-2",
    }

    with pytest.raises(HTTPException) as missing:
        _run_async(jobs_api.retry_upload("missing", service=service))
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as conflict:
        _run_async(jobs_api.retry_upload("complete", service=service))
    assert conflict.value.status_code == 409

def test_queue_launch_builds_request(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    cases = [
        (
            "processing",
            "start_upload_job",
            {
                "category": "movies",
                "paths": lambda case_root: [str(case_root / "One.mkv"), str(case_root / "Two.mkv")],
                "limit": 3,
                "skip_packs": True,
                "skip_episodes": True,
                "test_mode": True,
                "enable_duplicate_check": False,
                "indexer_id": "geek",
                "indexer_ids": ["geek", "slug"],
                "reuse_running": False,
            },
            "ProcessingJobRequest",
            lambda request, case_root: (
                request.category == "movies"
                and request.paths == (str(case_root / "One.mkv"), str(case_root / "Two.mkv"))
                and request.limit == 3
                and request.skip_packs is True
                and request.skip_episodes is True
                and request.test_mode is True
                and request.enable_duplicate_check is False
                and request.target_indexer_id == "geek"
                and request.target_indexer_ids == ("geek", "slug")
            ),
        ),
        (
            "stream",
            "start_usenet_stream_job",
            {
                "category": "movies",
                "stream_source_path": lambda case_root: str(case_root / "source.nzb"),
                "stream_source_name": "source.nzb",
                "release_name": "Release.Name",
                "test_mode": True,
                "enable_duplicate_check": False,
                "indexer_id": "geek",
                "posting_server_name": "Primary",
                "submit_mode": "submit_only",
            },
            "StreamJobRequest",
            lambda request, case_root: (
                request.category == "movies"
                and request.source_path == str(case_root / "source.nzb")
                and request.release_name == "Release.Name"
                and request.test_mode is True
                and request.target_indexer_id == "geek"
                and request.posting_server_name == "Primary"
                and request.submit_mode == "submit_only"
                and request.enable_duplicate_check is False
            ),
        ),
    ]

    for case_name, launcher_name, kwargs, request_type_name, request_assertions in cases:
        case_root = tmp_path / case_name
        service = _make_queue_service_stub(case_root)
        captured: dict[str, object] = {}

        def fake_execute_processing_job(job, request):
            captured["job"] = job
            captured["request"] = request

        def fake_execute_usenet_stream_job(job, request):
            captured["job"] = job
            captured["request"] = request

        service._execute_processing_job = fake_execute_processing_job
        service._execute_usenet_stream_job = fake_execute_usenet_stream_job

        launch_kwargs = {key: value(case_root) if callable(value) else value for key, value in kwargs.items()}

        with patch("logic.queueing.threading.Thread", ImmediateThread):
            with patch("logic.queueing.database.save_job_history"):
                job_id = getattr(service, launcher_name)(**launch_kwargs)

        request = captured["request"]
        assert type(request).__name__ == request_type_name, case_name
        assert request_assertions(request, case_root), case_name
        assert captured["job"]["job_id"] == job_id, case_name

def test_queue_mutates_queued_job_items_by_path_identity(tmp_path) -> None:
    def equivalent_path(path: Path) -> str:
        normalized = str(path).replace("\\", "/")
        return normalized.upper() if os.name == "nt" else normalized

    cases = [
        (
            "reorder",
            "reorder_queued_job_items",
            lambda first, second: [equivalent_path(second), equivalent_path(first)],
            lambda first, second: [str(second), str(first)],
        ),
        (
            "remove",
            "remove_queued_job_item",
            lambda first, _second: equivalent_path(first),
            lambda _first, second: [str(second)],
        ),
    ]

    for case_name, operation, value_factory, expected_paths in cases:
        case_root = tmp_path / case_name
        first = case_root / "First.Movie.mkv"
        second = case_root / "Second.Movie.mkv"
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"x")
        second.write_bytes(b"x")

        service = _make_queue_service_stub(case_root)
        service._jobs = {
            "job-1": {
                "job_id": "job-1",
                "status": "queued",
                "target_paths": [str(first), str(second)],
            }
        }
        service._persist_jobs_locked = lambda: None

        value = value_factory(first, second)
        assert getattr(service, operation)("job-1", value) is True, case_name
        assert service._get_job_target_paths(service._jobs["job-1"]) == expected_paths(first, second), case_name


def test_tv_path_rewrites_ignore_forged_client_category_hints(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from logic import pending_scan
    from logic.queueing import ProcessingJobRequest, QueueServiceMixin

    monkeypatch.setattr(pending_scan, "resolve_explicit_path", lambda _path: SimpleNamespace(category="movies"))

    pack = tmp_path / "Forged.Show.S01.1080p.WEB-DL"
    pack.mkdir()
    first = pack / "Movie.One.2024.1080p.BluRay.mkv"
    second = pack / "Movie.Two.2025.1080p.BluRay.mkv"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    forged_hints = tuple(
        {
            "path": str(path),
            "category": "tv",
            "detected_category": "tv",
            "itype": "TV Episode",
        }
        for path in (first, second)
    )
    expanded = QueueServiceMixin._with_inferred_tv_pack_request_paths(
        ProcessingJobRequest(category="tv", paths=(str(first), str(second)), item_hints=forged_hints)
    )

    assert expanded.paths == (str(first), str(second))

    parent = tmp_path / "Forged.Parent.2024.1080p.BluRay"
    child = parent / "Forged.Child.2025.1080p.BluRay"
    child.mkdir(parents=True)
    (child / "Movie.2025.1080p.BluRay.mkv").write_bytes(b"x")
    collapse_hints = tuple(
        {
            "path": str(path),
            "category": "tv",
            "detected_category": "tv",
            "itype": "TV Show",
        }
        for path in (parent, child)
    )
    collapsed = QueueServiceMixin._collapse_overlapping_tv_request_paths(
        ProcessingJobRequest(category="tv", paths=(str(parent), str(child)), item_hints=collapse_hints)
    )

    assert collapsed.paths == (str(parent), str(child))


def test_queue_start_keeps_mixed_categories_in_one_job(tmp_path, monkeypatch) -> None:
    from logic import queueing

    movie = tmp_path / "Movie.Name.2026.mkv"
    episode = tmp_path / "Show.Name.S01E01.mkv"
    movie.write_bytes(b"x")
    episode.write_bytes(b"x")

    service = _make_queue_service_stub(
        tmp_path,
        queue_items=[
            {"id": 1, "path": str(movie), "category": "movies", "itype": "Movie", "name": movie.name},
            {"id": 2, "path": str(episode), "category": "tv", "itype": "TV Episode", "name": episode.name},
        ],
    )

    captured: dict[str, object] = {}

    def fake_start_processing_job_request(request, **kwargs):
        captured["request"] = request
        captured["kwargs"] = kwargs
        return "job-mixed-1"

    service.start_processing_job_request = fake_start_processing_job_request
    monkeypatch.setattr(queueing.database, "db_remove_queue_items", lambda item_ids: len(item_ids))
    service.get_queue_items = lambda: list(service._queue_items)

    result = service.start_queue(source="queue-start", enable_duplicate_check=False, test_mode=True, indexer_id="geek")

    assert result == ["job-mixed-1"]
    request = captured["request"]
    assert isinstance(request, queueing.ProcessingJobRequest)
    assert request.category == "mixed"
    assert request.paths == (str(movie), str(episode))
    assert tuple(item["category"] for item in request.item_hints) == ("movies", "tv")
    assert captured["kwargs"] == {"reuse_running": False, "source": "queue-start"}
    assert service._queue_items == []

def test_queue_start_raises_clear_error_when_no_runnable_items(tmp_path) -> None:
    misc = tmp_path / "Unknown.Release.mkv"
    misc.write_bytes(b"x")

    service = _make_queue_service_stub(
        tmp_path,
        queue_items=[
            {"id": 1, "path": str(misc), "category": "misc", "itype": "Misc", "name": misc.name},
            {"id": 2, "path": "", "category": "", "itype": "", "name": "Broken.Entry"},
        ],
    )

    with pytest.raises(ValueError, match="no runnable staged items"):
        service.start_queue_with_details(source="queue-start")

def test_queue_start_preserves_staged_items_when_job_creation_fails(tmp_path, monkeypatch) -> None:
    from logic import queueing

    movie = tmp_path / "Movie.Title.2026.mkv"
    movie.write_bytes(b"x")

    service = _make_queue_service_stub(
        tmp_path,
        queue_items=[
            {"id": 1, "path": str(movie), "category": "movies", "itype": "Movie", "name": movie.name},
        ],
    )

    removed_ids: list[list[int]] = []

    def fake_remove_queue_items(item_ids):
        removed_ids.append(list(item_ids))
        return len(item_ids)

    def fake_start_processing_job_request(*_args, **_kwargs):
        raise RuntimeError("boom")

    service.start_processing_job_request = fake_start_processing_job_request
    service.get_queue_items = lambda: list(service._queue_items)
    monkeypatch.setattr(queueing.database, "db_remove_queue_items", fake_remove_queue_items)

    with pytest.raises(RuntimeError, match="boom"):
        service.start_queue_with_details(source="queue-start")

    assert service._queue_items == [
        {"id": 1, "path": str(movie), "category": "movies", "itype": "Movie", "name": movie.name},
    ]
    assert removed_ids == []

def test_api_start_upload_builds_processing_request(monkeypatch, tmp_path) -> None:
    cases = [
        (
            "scan-configured-items",
            lambda movie: jobs_api.UploadRequest(
                category="movies",
                test_mode=True,
                enable_duplicate_check=False,
                indexer_ids=["geek", "slug"],
                source="api-test",
            ),
            lambda movie, case_root: [
                type("DummyItem", (), {"category": "movies", "folder": case_root, "path": movie})()
            ],
            "job-123",
            "api-test",
            lambda movie: (str(movie),),
            None,
            False,
            ("geek", "slug"),
        ),
        (
            "explicit-file-path",
            lambda movie: jobs_api.UploadRequest(
                category="movies",
                file_path=str(movie),
                test_mode=True,
                enable_duplicate_check=True,
            ),
            lambda movie, case_root: None,
            "job-123",
            "api-bulk-start",
            lambda movie: (str(movie.resolve()),),
            lambda movie: (
                {
                    "path": str(movie.resolve()),
                    "category": "movies",
                    "itype": "Movie",
                    "name": movie.name,
                },
            ),
            True,
            (),
        ),
    ]

    patch_hit(monkeypatch, jobs_api, "get_config", lambda: SimpleNamespace())

    for (
        case_name,
        request_factory,
        scan_items_factory,
        expected_job_id,
        expected_source,
        expected_paths,
        expected_item_hints,
        expected_dupe_check,
        expected_indexer_ids,
    ) in cases:
        case_root = tmp_path / case_name
        movie = _touch(case_root / "Movie.Name.2026.1080p.mkv", b"x")
        captured, service = _make_upload_request_service_capture()

        scan_items = scan_items_factory(movie, case_root)
        if scan_items is not None:

            def fake_scan_configured_items(*_args, **_kwargs):
                return scan_items

            patch_hit(monkeypatch, jobs_api, "scan_configured_items", fake_scan_configured_items)

        request = request_factory(movie)
        result = _run_async(jobs_api.start_upload(request, service=service))

        assert result == {"job_id": expected_job_id, "job_ids": [expected_job_id], "status": "started"}, case_name
        requests = captured["requests"]
        assert len(requests) == 1, case_name
        assert isinstance(requests[0], ProcessingJobRequest), case_name
        assert requests[0].category == "movies", case_name
        assert requests[0].paths == expected_paths(movie), case_name
        assert requests[0].enable_duplicate_check is expected_dupe_check, case_name
        if expected_indexer_ids:
            assert requests[0].target_indexer_ids == expected_indexer_ids, case_name
        if expected_item_hints is not None:
            assert requests[0].item_hints == expected_item_hints(movie), case_name
        assert captured["kwargs"] == ({} if expected_source is None else {"source": expected_source}), case_name

def test_run_job_uses_runtime_target_path_order(tmp_path, monkeypatch) -> None:
    import logic.processing as processing
    from core.utils import set_thread_job

    cases = [
        (
            "explicit-path-order",
            ("Older.Movie.1080p.mkv", "Newer.Movie.1080p.mkv"),
            False,
            False,
            ("Older.Movie.1080p.mkv", "Newer.Movie.1080p.mkv"),
            None,
        ),
        (
            "reordered-active-target-paths",
            ("First.Movie.1080p.mkv", "Second.Movie.1080p.mkv", "Third.Movie.1080p.mkv"),
            True,
            True,
            ("First.Movie.1080p.mkv", "Third.Movie.1080p.mkv", "Second.Movie.1080p.mkv"),
            [],
        ),
    ]

    for case_name, names, use_job_state, mutate_after_first, expected_seen, expected_remaining in cases:
        movies_dir = tmp_path / case_name / "movies"
        movies_dir.mkdir(parents=True, exist_ok=True)
        paths = [_touch(movies_dir / name, b"x") for name in names]

        _configure_run_job_basics(monkeypatch, processing, movies_dir)
        monkeypatch.setattr(
            processing,
            "_run_validation_with_timeout",
            lambda path, **_kwargs: processing.QueueItemValidation("ready", path, "movies", "Movies"),
        )

        seen: list[str] = []
        job = None
        if use_job_state:
            job = {
                "status": "running",
                "target_paths": [str(path) for path in paths],
                "current_item": None,
            }

        def fake_process_single(path: Path, **_kwargs) -> int:
            seen.append(path.name)
            if mutate_after_first and path == paths[0] and job is not None:
                job["target_paths"] = [str(path) for path in reversed(paths[1:])]
            return 0

        monkeypatch.setattr(processing, "process_single", fake_process_single)

        set_thread_job(job)
        try:
            processing.run_job(category="movies", paths=[str(path) for path in paths])
        finally:
            set_thread_job(None)

        assert tuple(seen) == expected_seen, case_name
        if job is not None:
            assert job["target_paths"] == expected_remaining, case_name

def test_run_job_targeted_validation_uses_prefetched_duplicate_state(tmp_path, monkeypatch) -> None:
    import logic.processing as processing
    from core.utils import set_thread_job

    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    first = movies_dir / "First.Movie.1080p.mkv"
    second = movies_dir / "Second.Movie.1080p.mkv"
    first.write_bytes(b"x")
    second.write_bytes(b"x")

    _configure_run_job_basics(monkeypatch, processing, movies_dir)

    captured: list[tuple[str, dict[str, str | None] | None, Path | None]] = []

    def fake_build_duplicate_prefetch_state(_conf, sorted_items, _configured_folders, **_kwargs):
        keys = {processing._normalize_runtime_path(path): f"db::{path.name}" for path, _cat in sorted_items}
        dupes = {
            f"db::{first.name}": {"geek": "2026-04-10T00:00:00Z"},
            f"db::{second.name}": {"geek": None},
        }

        def source_root_for(_path: Path) -> Path:
            return movies_dir

        return keys, dupes, source_root_for

    def fake_run_validation_with_timeout(path: Path, **kwargs):
        captured.append(
            (
                path.name,
                kwargs.get("prefetched_dest_status"),
                kwargs.get("prefetched_base_folder"),
            )
        )
        return processing.QueueItemValidation(
            "ready",
            path,
            "movies",
            "Movies",
            base_folder=kwargs.get("prefetched_base_folder"),
            prefetched_dest_status=kwargs.get("prefetched_dest_status"),
        )

    monkeypatch.setattr(processing, "_build_duplicate_prefetch_state", fake_build_duplicate_prefetch_state)
    monkeypatch.setattr(processing, "_run_validation_with_timeout", fake_run_validation_with_timeout)
    monkeypatch.setattr(processing, "process_single", lambda _path, **_kwargs: 0)

    set_thread_job(None)
    try:
        processing.run_job(category="movies", paths=[str(first), str(second)], target_indexer_ids=["geek"])
    finally:
        set_thread_job(None)

    # Completed items are resolved directly from the batch-prefetched state,
    # without paying for a validation-worker import or filesystem scan.
    assert captured == [
        (second.name, {"geek": None}, movies_dir),
    ]

def test_run_job_rejects_relative_target_paths(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    item = movies_dir / "Relative.Movie.1080p.mkv"
    item.write_bytes(b"x")

    seen: list[Path] = []

    _configure_run_job_basics(monkeypatch, processing, movies_dir)
    monkeypatch.setattr(registry_mod, "get_enabled_indexers", lambda _conf: [])
    monkeypatch.setattr(processing, "process_single", lambda path, **_kwargs: seen.append(path) or 0)

    with pytest.raises(ValueError, match="No valid items remained after filtering"):
        processing.run_job(category="movies", paths=[item.name])

    assert seen == []

def test_run_job_fails_when_targeted_selection_resolves_to_zero_items(tmp_path, monkeypatch) -> None:
    import logic.pending_scan as pending_scan
    import logic.processing as processing
    from core.utils import set_thread_job

    item = tmp_path / "Unknown.Release.mkv"
    item.write_bytes(b"x")

    job = {"job_id": "job-zero", "status": "running", "progress": "Starting..."}

    _configure_run_job_basics(monkeypatch, processing, tmp_path)
    monkeypatch.setattr(
        processing,
        "_run_validation_with_timeout",
        lambda path, **_kwargs: processing.QueueItemValidation("ready", path, "movies", "Movies"),
    )

    def fake_resolve_explicit_path(*_args, **_kwargs):
        return pending_scan.ExplicitPathResolution(
            source_path=item,
            category="misc",
            itype="Misc",
            detection_method="Folder fallback",
            queue_paths=(),
        )

    monkeypatch.setattr(processing, "resolve_explicit_path", fake_resolve_explicit_path)

    set_thread_job(job)
    try:
        with pytest.raises(ValueError, match="No valid items remained after filtering"):
            processing.run_job(category="movies", paths=[str(item)])
    finally:
        set_thread_job(None)

    assert job["status"] == "failed"
    assert "selected=1 filtered=0 final_queued=0" in job["progress"]
    assert job["items_total"] == 1
    assert job["items_processed"] == 0

def test_process_single_reuses_validated_size_for_preparation(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    movie = tmp_path / "Movie.Name.2026.mkv"
    movie.write_bytes(b"x")
    conf = _make_process_single_conf(tmp_path)
    captured: dict[str, int | None] = {"total_bytes": None}

    def fake_prepare(_path: Path, _name: str, _itype: str, **kwargs) -> bool:
        captured["total_bytes"] = kwargs.get("total_bytes")
        return True

    _configure_process_single_environment(monkeypatch, processing, conf, prepare_item=fake_prepare)
    monkeypatch.setattr(
        processing,
        "submit_api",
        lambda *_args, **_kwargs: SubmitResult(True, "success", "ok"),
    )

    result = processing.process_single(
        movie,
        itype="Movies",
        category="movies",
        base_folder=tmp_path,
        validated_item_size_bytes=987_654_321,
    )

    assert result == 0
    assert captured["total_bytes"] == 987_654_321

def test_process_single_uses_expected_mediainfo_sidecar(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    for case_name, prepare_mode in (
        ("canonical-sidecar", "canonical"),
        ("generated-after-prepare", "generated"),
    ):
        case_root = tmp_path / case_name
        movies_dir = case_root / "movies"
        movies_dir.mkdir(parents=True, exist_ok=True)
        movie = movies_dir / "Movie.Name.2026.1080p.mkv"
        movie.write_bytes(b"x" * 10)

        mediainfo_dir = case_root / "mediainfo"
        mediainfo_dir.mkdir()
        expected_path = mediainfo_dir / f"{movie.name}.mediainfo.nfo"

        if prepare_mode == "canonical":
            expected_path.write_text("General\n", encoding="utf-8")
            prepare_item = None
        else:

            def prepare_item(path: Path, _name: str, _itype: str, **_kwargs) -> bool:
                processing._mediainfo_output_path(path, conf).write_text("General\n", encoding="utf-8")
                return True

        conf = _make_process_single_conf(case_root)
        conf.mediainfo_sub = mediainfo_dir
        _configure_process_single_environment(monkeypatch, processing, conf, prepare_item=prepare_item)

        seen: dict[str, Path | None] = {"mediainfo": None}

        def fake_submit_api(_name, _dest, _conf, **kwargs):
            seen["mediainfo"] = kwargs["mediainfo_path"]
            return SubmitResult(True, "success", "ok")

        monkeypatch.setattr(processing, "submit_api", fake_submit_api)

        result = processing.process_single(
            movie,
            itype="Movies",
            category="movies",
            base_folder=movies_dir,
            test_mode=False,
        )

        assert result == 0, case_name
        assert seen["mediainfo"] == expected_path, case_name

def test_process_single_directory_reuses_scanned_nfo_for_submission(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    release_dir = movies_dir / "Movie.Name.2026"
    release_dir.mkdir()
    (release_dir / "Movie.Name.2026.mkv").write_bytes(b"x" * 10)
    nfo_file = release_dir / "release.nfo"
    nfo_file.write_text("release", encoding="utf-8")

    conf = _make_process_single_conf(tmp_path)
    _configure_process_single_environment(monkeypatch, processing, conf)

    scans = {"count": 0}

    def fake_scan(_path: Path) -> processing.SupportAssetScan:
        scans["count"] += 1
        return processing.SupportAssetScan(nfo_path=nfo_file, mediainfo_source_path=release_dir / "Movie.Name.2026.mkv")

    monkeypatch.setattr(processing, "_scan_item_support_assets", fake_scan)
    monkeypatch.setattr(
        processing, "_find_nfo_path", lambda _path: (_ for _ in ()).throw(AssertionError("unexpected rescan"))
    )

    seen: dict[str, Path | None] = {"nfo": None}

    def fake_submit_api(_name, _dest, _conf, **kwargs):
        seen["nfo"] = kwargs["nfo_path"]
        return SubmitResult(True, "success", "ok")

    monkeypatch.setattr(processing, "submit_api", fake_submit_api)

    result = processing.process_single(
        release_dir,
        itype="Movies",
        category="movies",
        base_folder=movies_dir,
        test_mode=False,
    )

    assert result == 0
    assert scans["count"] == 1
    assert seen["nfo"] == nfo_file

def test_process_single_posts_once_per_shared_server_and_submits_each_priority_group(tmp_path, monkeypatch) -> None:
    # processing-09 (DECISIONS: one post per shared server): priority and normal
    # indexers on the same server share one NNTP post; each group is submitted.
    import logic.processing as processing

    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    movie = movies_dir / "Movie.Name.2026.1080p.mkv"
    movie.write_bytes(b"x" * 10)

    class DummyIndexer:
        def __init__(self, idx_id: str) -> None:
            self.id = idx_id
            self.name = idx_id
            self.backfill = True

    indexers = [DummyIndexer("idx_fail"), DummyIndexer("idx_ok")]
    conf = _make_process_single_conf(tmp_path, max_connections=20)
    _configure_process_single_environment(
        monkeypatch,
        processing,
        conf,
        indexers=indexers,
        duplicate_status={"idx_fail": None, "idx_ok": None},
        priority_resolver=lambda idx, _conf: idx.id == "idx_fail",
    )

    upload_keys: list[str] = []

    def fake_upload_item(_name, _server, **kwargs):
        upload_keys.append(str(kwargs.get("progress_key") or ""))
        return {"duration": 0.1, "speed_bps": 1, "server_name": "Primary"}

    monkeypatch.setattr(processing, "upload_item", fake_upload_item)

    seen: list[tuple[str, str]] = []

    def fake_submit_api(name, dest, _conf, **_kwargs):
        seen.append((dest, name))
        if dest == "idx_fail":
            return SubmitResult(False, "rejected", "boom")
        return SubmitResult(True, "success", "ok")

    monkeypatch.setattr(processing, "submit_api", fake_submit_api)

    result = processing.process_single(
        movie,
        itype="Movies",
        category="movies",
        base_folder=movies_dir,
        test_mode=False,
    )

    assert result == 0
    # One post for both indexers; the set keeps " (P)" so the uploader labels it priority.
    assert upload_keys == ["idx_fail/idx_ok (P)"]
    assert seen == [("idx_fail", "Priority Movie.Name.2026.1080p.mkv"), ("idx_ok", "Movie.Name.2026.1080p.mkv")]

def test_run_job_validates_duplicates_per_item_without_batch_prefetch(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    movies_dir = tmp_path / "movies"
    movies_dir.mkdir()
    (movies_dir / "Movie.One.2026.1080p.mkv").write_bytes(b"a")
    (movies_dir / "Movie.Two.2026.1080p.mkv").write_bytes(b"b")

    class _Idx:
        id = "geek"
        name = "NZBGeek"
        enabled = True
        backfill = False
        priority = False

    _configure_run_job_basics(monkeypatch, processing, movies_dir)
    monkeypatch.setattr(registry_mod, "get_enabled_indexers", lambda _conf: [_Idx()])
    monkeypatch.setattr(db, "get_all_upload_stats", lambda: {})

    prefetched_seen: list[dict[str, str | None] | None] = []

    def fail_batch_prefetch(*_args, **_kwargs):
        raise AssertionError("run_job should not batch-prefetch duplicate state before uploads")

    def fake_check_duplicate_dynamic(item_key: str, _itype: str, indexer_ids: list[str], filesize=None):
        assert filesize == 1
        assert indexer_ids == ["geek"]
        assert item_key in {
            "Movie.One.2026.1080p.mkv",
            "Movie.Two.2026.1080p.mkv",
        }
        return {"geek": None}

    def fake_process_single(path: Path, **kwargs) -> int:
        _ = path
        prefetched_seen.append(kwargs.get("prefetched_dest_status"))
        return 1

    monkeypatch.setattr(db, "get_duplicate_status_batch", fail_batch_prefetch)
    monkeypatch.setattr(db, "check_duplicate_dynamic", fake_check_duplicate_dynamic)
    monkeypatch.setattr(processing, "process_single", fake_process_single)

    processing.run_job(category="movies", test_mode=True)

    assert len(prefetched_seen) == 2
    assert all(isinstance(v, dict) and "geek" in v for v in prefetched_seen)

def test_run_job_starts_upload_before_validating_entire_queue(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    first = tmp_path / "Movie.One.2026.1080p.mkv"
    second = tmp_path / "Movie.Two.2026.1080p.mkv"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    upload_started = threading.Event()
    validation_saw_active_upload: list[bool] = []

    _configure_run_job_basics(monkeypatch, processing, tmp_path)
    monkeypatch.setattr(
        processing,
        "_run_validation_with_timeout",
        lambda path, **_kwargs: processing.QueueItemValidation("ready", path, "movies", "Movies"),
    )
    monkeypatch.setattr(db, "get_all_upload_stats", lambda: {})

    def fake_validate(path: Path, **_kwargs):
        if path == second:
            validation_saw_active_upload.append(upload_started.is_set())
        return processing.QueueItemValidation("ready", path, "movies", "Movies")

    def fake_process_single(path: Path, **_kwargs) -> int:
        if path == first:
            upload_started.set()
            time.sleep(0.1)
        return 0

    monkeypatch.setattr(processing, "_run_validation_with_timeout", fake_validate)
    monkeypatch.setattr(processing, "process_single", fake_process_single)

    processing.run_job(category="movies", paths=[str(first), str(second)])

    assert validation_saw_active_upload == [True]

def test_validation_timeout_marks_item_failed_fast(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    item = tmp_path / "Slow.Movie.2026.1080p.mkv"
    item.write_bytes(b"x")

    def slow_validate(*_args, **_kwargs):
        time.sleep(0.05)
        return processing.QueueItemValidation("ready", item, "movies", "Movies")

    monkeypatch.setattr(processing, "_validate_queue_item", slow_validate)

    started = time.perf_counter()
    result = processing._run_validation_with_timeout(
        item,
        category="movies",
        force=False,
        configured_folders=[],
        target_indexer_id=None,
        target_indexer_ids=None,
        skip_enabled=False,
        skip_config=None,
        timeout_s=0.01,
        isolate_with_process=False,
    )
    elapsed = time.perf_counter() - started

    assert result.outcome == "failed"
    assert "timed out" in result.message
    assert elapsed < 0.05

def test_atomic_config_replace_restores_previous_file_when_validation_fails(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: working\n", encoding="utf-8")

    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_path)
    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda: (_ for _ in ()).throw(ValueError("invalid configuration")),
    )

    with pytest.raises(ValueError, match="invalid configuration"):
        config_mod.replace_config_content("version: broken\n")

    assert config_path.read_text(encoding="utf-8") == "version: working\n"
    assert config_path.with_name("config.yaml.bak").read_text(encoding="utf-8") == "version: working\n"

def test_force_start_queue_item_preserves_staged_item_when_launch_fails(tmp_path) -> None:
    movie = tmp_path / "Movie.Title.2026.mkv"
    movie.write_bytes(b"x")

    class DummyService:
        def __init__(self) -> None:
            self.items = [{"id": 1, "path": str(movie), "category": "movies", "itype": "Movie", "name": movie.name}]
            self.removed = False

        def get_queue_items(self):
            return list(self.items)

        def remove_queue_item(self, item_id):
            _ = item_id
            self.removed = True
            return True

        def start_processing_job_request(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    service = DummyService()

    with pytest.raises(RuntimeError, match="boom"):
        _run_async(staging_api.force_start_queue_item(1, service=service))

    assert service.removed is False
    assert service.items == [{"id": 1, "path": str(movie), "category": "movies", "itype": "Movie", "name": movie.name}]

def test_bulk_selection_filter_excludes_descendants_but_not_manual_actions(tmp_path, monkeypatch) -> None:
    blocked_root = tmp_path / "qbittorrent"
    allowed_root = tmp_path / "ready"
    blocked_root.mkdir()
    allowed_root.mkdir()
    blocked_item = blocked_root / "Blocked.Release"
    allowed_item = allowed_root / "Allowed.Release"
    blocked_item.mkdir()
    allowed_item.mkdir()
    conf = SimpleNamespace(
        folder_paths=[
            {"path": str(blocked_root), "allow_bulk_selection": False},
            {"path": str(allowed_root), "allow_bulk_selection": True},
        ]
    )
    payload = [
        {"path": str(blocked_item), "category": "movies"},
        {"path": str(allowed_item), "category": "movies"},
    ]

    filtered, excluded = deps_api._filter_bulk_selectable_items(payload, conf)

    assert filtered == [payload[1]]
    assert excluded == 1

    class DummyService:
        def add_queue_items(self, items):
            return list(items)

        def get_queue_items(self):
            return []

    # The blocking config is live in the staging module (the pre-split patch on app.get_config never
    # reached app_g1): a manual add must not consult it, so this patch is expected to stay unhit.
    monkeypatch.setattr(staging_api, "get_config", lambda: conf)
    manual_result = _run_async(
        staging_api.add_queue_items(
            staging_api.AddQueueItemsRequest(items=[payload[0]], bulk_selection=False),
            service=DummyService(),
        )
    )

    assert manual_result["added"] == 1
    assert manual_result["bulk_excluded"] == 0

def test_staged_queue_preview_is_non_mutating(monkeypatch) -> None:
    staged = [
        {"id": 1, "path": "/data/Movie.One.mkv", "category": "movies"},
        {"id": 2, "path": "/data/Movie.Two.mkv", "category": "movies"},
    ]
    captured: dict[str, object] = {}

    class DummyService:
        def get_queue_items(self):
            return list(staged)

    async def fake_preview(items, **kwargs):
        captured["items"] = items
        captured["kwargs"] = kwargs
        return {
            "status": "preview",
            "summary": {"selected": len(items), "planned": len(items), "ready": len(items)},
            "destinations": {},
            "items": [],
        }

    monkeypatch.setattr(staging_api, "_preview_selected_items", fake_preview)
    result = _run_async(
        staging_api.preview_queue(
            staging_api.StartQueueRequest(enable_duplicate_check=True, indexer_id="geek"),
            service=DummyService(),
        )
    )

    assert captured["items"] == staged
    assert result["selection"]["matched"] == 2
    assert staged[0]["id"] == 1
    assert captured["kwargs"] == {
        "enable_duplicate_check": True,
        "test_mode": False,
        "indexer_id": "geek",
    }

def test_check_success_duplicate_pattern_detected() -> None:
    from core.registry import _check_success

    idx = _make_success_indexer(["OK"], ["DUPLICATE"])
    ok, is_dup = _check_success(idx, _fake_success_response("DUPLICATE ENTRY"))

    assert ok is True
    assert is_dup is True

def test_preview_processing_items_reports_ready_and_duplicate_destinations(tmp_path, monkeypatch) -> None:
    from logic import processing
    from logic import services

    first = tmp_path / "Already.Uploaded.2025.mkv"
    second = tmp_path / "Ready.To.Upload.2026.mkv"
    first.write_bytes(b"a")
    second.write_bytes(b"bb")
    conf = _make_processing_conf(tmp_path, category="movies", enable_duplicate_checking=True)
    monkeypatch.setattr(processing, "get_config", lambda: conf)
    monkeypatch.setattr(services, "get_config", lambda: conf)
    monkeypatch.setattr(
        registry_mod,
        "get_enabled_indexers",
        lambda _conf: [SimpleNamespace(id="geek", name="NZBGeek", enabled=True)],
    )
    monkeypatch.setattr(registry_mod, "resolve_indexer_enabled", lambda _indexer, _conf: True)
    first_key = processing._normalize_runtime_path(first)
    second_key = processing._normalize_runtime_path(second)
    monkeypatch.setattr(
        processing,
        "_build_duplicate_prefetch_state",
        lambda *_args, **_kwargs: (
            {first_key: "first", second_key: "second"},
            {
                "first": {"geek": "2026-01-01T00:00:00+00:00"},
                "second": {"geek": None},
            },
            lambda _path: tmp_path,
        ),
    )
    monkeypatch.setattr(processing, "_resolve_submission_category", lambda *_args: "Movies")
    # An all-done prefetch is re-verified with the live size-aware check (processing-05).
    monkeypatch.setattr(
        db,
        "check_duplicate_dynamic",
        lambda key, _itype, ids, filesize=None: {
            idx: ("2026-01-01T00:00:00+00:00" if key == first.name and filesize == 1 else None) for idx in ids
        },
    )

    result = processing.preview_processing_items(
        [
            {"path": str(first), "category": "movies"},
            {"path": str(second), "category": "movies"},
        ],
        target_indexer_id="geek",
        enable_duplicate_check=True,
    )

    assert result["summary"]["selected"] == 2
    assert result["summary"]["planned"] == 2
    assert result["summary"]["ready"] == 1
    assert result["summary"]["duplicate"] == 1
    assert result["summary"]["ready_bytes"] == 2
    assert result["destinations"] == {"geek": {"pending": 1, "uploaded": 1}}
    assert [item["outcome"] for item in result["items"]] == ["duplicate", "ready"]

def test_force_upload_request_resolves_force_from_duplicate_check(monkeypatch) -> None:
    from logic.queueing import QueueServiceMixin
    from logic.services import UploadService

    _set_duplicate_checking(monkeypatch, enabled=True)

    for case_name, enable_duplicate_check, expected_force in (
        ("skip-dupe-check-resolves-force-true", False, True),
        ("dupe-check-enabled-resolves-force-false", True, False),
    ):
        kwargs = {"enable_duplicate_check": enable_duplicate_check, "force": None}
        request = QueueServiceMixin._build_processing_request("tv", kwargs, ["/path/ep.mkv"])
        force_value = UploadService._resolve_force_flag(
            enable_duplicate_check=request.enable_duplicate_check,
            force=request.force,
            test_mode=False,
        )

        assert force_value is expected_force, case_name
