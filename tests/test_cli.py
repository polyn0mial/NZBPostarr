# ruff: noqa: F403,F405

"""NZBPostarr headless CLI tests."""

import shutil

from tests.support import *


class _FakeQueueService:
    """Minimal stand-in for UploadService exposing only the queue methods the CLI calls."""

    def __init__(self, jobs=None, control=None):
        self.jobs = list(jobs or [])
        self.control = control or {"paused": False, "active": None}
        self.calls: list[tuple] = []

    def get_active_jobs(self, compact=True):
        self.calls.append(("get_active_jobs", compact))
        return self.jobs

    def get_queue_control_state(self):
        return self.control

    def pause_queue(self, pause_active=True):
        self.calls.append(("pause_queue", pause_active))
        return True

    def resume_queue(self):
        self.calls.append(("resume_queue",))
        return True

    def stop_queue(self):
        self.calls.append(("stop_queue",))
        return True

    def stop_queue_and_clear(self):
        self.calls.append(("stop_queue_and_clear",))
        return {"cleared_jobs": 2, "control": self.control}

    def clear_queued_jobs(self):
        self.calls.append(("clear_queued_jobs",))
        return 4

    def revalidate_queued_jobs(self, include_paused=True):
        self.calls.append(("revalidate_queued_jobs", include_paused))
        return {"inspected": 3, "updated": 1, "cancelled": 1, "jobs": []}

    def pause_job(self, job_id):
        self.calls.append(("pause_job", job_id))
        return job_id == "job-ok"

    def resume_job(self, job_id):
        self.calls.append(("resume_job", job_id))
        return job_id == "job-ok"

    def stop_job(self, job_id):
        self.calls.append(("stop_job", job_id))
        return job_id == "job-ok"

    def stop_and_clear_job(self, job_id):
        self.calls.append(("stop_and_clear_job", job_id))
        return job_id == "job-ok"

    def retry_job(self, job_id):
        self.calls.append(("retry_job", job_id))
        if job_id == "job-ok":
            return True, "new-job-1", "queued"
        return False, None, "not-found"

    def promote_job(self, job_id):
        self.calls.append(("promote_job", job_id))
        return job_id == "job-ok"

    def get_job(self, job_id):
        self.calls.append(("get_job", job_id))
        return {"status": "paused", "pause_requested": False, "resume_requested": False}


# ============================================================
#  ARGPARSE STRUCTURE
# ============================================================


def test_headless_parser_has_json_on_core_commands() -> None:
    parser = headless_mod.build_headless_parser()

    assert parser.parse_args(["upload", "tv", "--json"]).json is True
    assert parser.parse_args(["status", "--json"]).json is True
    assert parser.parse_args(["pending", "--json"]).json is True
    assert parser.parse_args(["history", "--json"]).json is True
    assert parser.parse_args(["indexers", "--json"]).json is True


def test_headless_parser_queue_and_job_subcommands() -> None:
    parser = headless_mod.build_headless_parser()

    status_args = parser.parse_args(["queue", "status", "--json"])
    assert status_args.command == "queue"
    assert status_args.queue_command == "status"
    assert status_args.json is True

    stop_args = parser.parse_args(["queue", "stop", "--clear"])
    assert stop_args.queue_command == "stop"
    assert stop_args.clear is True

    revalidate_args = parser.parse_args(["queue", "revalidate", "--skip-paused"])
    assert revalidate_args.queue_command == "revalidate"
    assert revalidate_args.skip_paused is True

    job_args = parser.parse_args(["queue", "job", "stop", "job-123", "--clear", "--json"])
    assert job_args.queue_command == "job"
    assert job_args.job_command == "stop"
    assert job_args.job_id == "job-123"
    assert job_args.clear is True
    assert job_args.json is True


def test_headless_parser_logs_subcommand() -> None:
    parser = headless_mod.build_headless_parser()

    logs_args = parser.parse_args(["logs", "--lines", "50", "--json"])
    assert logs_args.command == "logs"
    assert logs_args.lines == 50
    assert logs_args.json is True


def test_headless_version_flag_exits_zero(capsys) -> None:
    from version import __version__

    parser = headless_mod.build_headless_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])

    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


# ============================================================
#  cmd_upload
# ============================================================


def test_cmd_upload_invalid_category_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(registry_mod, "get_available_categories", lambda: [{"id": "tv"}])

    rc = headless_mod.cmd_upload(
        SimpleNamespace(
            category="bogus",
            limit=None,
            test=False,
            force=False,
            indexer=None,
            skip_packs=False,
            skip_episodes=False,
            path=None,
            json=True,
        )
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "bogus" in payload["message"]


def test_cmd_upload_json_success(monkeypatch, capsys) -> None:
    from logic import services

    monkeypatch.setattr(registry_mod, "get_available_categories", lambda: [{"id": "tv"}])

    class _FakeUploadService:
        def run_job_sync(self, **_kwargs):
            return {
                "status": "completed",
                "job_id": "cli-abc123",
                "summary": {"duration": "1m 2s", "processed": "2/2", "skipped": 0},
            }

    monkeypatch.setattr(services, "get_upload_service", lambda: _FakeUploadService())

    rc = headless_mod.cmd_upload(
        SimpleNamespace(
            category="tv",
            limit=None,
            test=False,
            force=False,
            indexer=None,
            skip_packs=False,
            skip_episodes=False,
            path=None,
            json=True,
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert payload["job_id"] == "cli-abc123"
    assert payload["summary"]["processed"] == "2/2"


# ============================================================
#  cmd_status
# ============================================================


def test_cmd_status_json_all_tools_present(monkeypatch, capsys) -> None:
    server = SimpleNamespace(name="Primary", host="news.example.com", port=563, enabled=True, max_connections=20)
    conf = SimpleNamespace(nntp_servers=[server], folder_paths=[{"path": "", "monitor": False}])
    idx = SimpleNamespace(id="geek", name="NZBGeek")

    class _FakeRegistry:
        def enabled(self, _conf):
            return [idx]

        def all(self):
            return [idx]

    monkeypatch.setattr(config_mod, "get_config", lambda: conf)
    monkeypatch.setattr(registry_mod, "get_registry", lambda: _FakeRegistry())
    monkeypatch.setattr(registry_mod, "resolve_indexer_enabled", lambda _idx, _conf: True)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    rc = headless_mod.cmd_status(SimpleNamespace(json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing_required_tools"] == []
    assert payload["tools"]["mediainfo"] == "/usr/bin/mediainfo"
    assert payload["indexers"]["enabled"] == 1


def test_cmd_status_missing_required_tool_sets_exit_code(monkeypatch, capsys) -> None:
    conf = SimpleNamespace(nntp_servers=[], folder_paths=[])

    monkeypatch.setattr(config_mod, "get_config", lambda: conf)
    monkeypatch.setattr(registry_mod, "get_registry", lambda: SimpleNamespace(enabled=lambda _c: [], all=lambda: []))
    monkeypatch.setattr(registry_mod, "resolve_indexer_enabled", lambda _idx, _conf: True)

    def fake_which(name):
        return None if name == "parpar" else f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which)

    rc = headless_mod.cmd_status(SimpleNamespace(json=True))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing_required_tools"] == ["parpar"]


def test_cmd_status_missing_mediainfo_only_does_not_fail(monkeypatch, capsys) -> None:
    conf = SimpleNamespace(nntp_servers=[], folder_paths=[])

    monkeypatch.setattr(config_mod, "get_config", lambda: conf)
    monkeypatch.setattr(registry_mod, "get_registry", lambda: SimpleNamespace(enabled=lambda _c: [], all=lambda: []))
    monkeypatch.setattr(registry_mod, "resolve_indexer_enabled", lambda _idx, _conf: True)

    def fake_which(name):
        return None if name == "mediainfo" else f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which)

    rc = headless_mod.cmd_status(SimpleNamespace(json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing_required_tools"] == []
    assert payload["tools"]["mediainfo"] is None


# ============================================================
#  cmd_pending
# ============================================================


def test_cmd_pending_json_output(monkeypatch, capsys, tmp_path) -> None:
    conf = SimpleNamespace(folder_paths=[])
    monkeypatch.setattr(config_mod, "get_config", lambda: conf)
    monkeypatch.setattr(registry_mod, "get_registry", lambda: SimpleNamespace(enabled=lambda _c: [SimpleNamespace(id="geek")]))
    monkeypatch.setattr(db, "get_dashboard_data", lambda _ids: (set(), {}, {}))

    tv_folder = tmp_path / "tv"
    movie_folder = tmp_path / "movies"
    item_tv = tv_folder / "Show.S01"
    item_movie = movie_folder / "Movie.2026"

    monkeypatch.setattr(
        pending_scan,
        "collect_configured_scan_items",
        lambda _conf: [("tv", tv_folder, item_tv), ("movies", movie_folder, item_movie)],
    )
    monkeypatch.setattr(pending_scan, "relative_key", lambda item, _folder: item.name)

    rc = headless_mod.cmd_pending(SimpleNamespace(category=None, verbose=True, json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 2
    assert payload["pending"] == 2
    assert payload["categories"]["tv"]["pending"] == 1
    assert payload["categories"]["tv"]["items"] == ["tv/Show.S01"]


def test_cmd_pending_json_empty(monkeypatch, capsys) -> None:
    conf = SimpleNamespace(folder_paths=[])
    monkeypatch.setattr(config_mod, "get_config", lambda: conf)
    monkeypatch.setattr(registry_mod, "get_registry", lambda: SimpleNamespace(enabled=lambda _c: []))
    monkeypatch.setattr(db, "get_dashboard_data", lambda _ids: (set(), {}, {}))
    monkeypatch.setattr(pending_scan, "collect_configured_scan_items", lambda _conf: [])

    rc = headless_mod.cmd_pending(SimpleNamespace(category=None, verbose=False, json=True))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "empty"


# ============================================================
#  cmd_history
# ============================================================


def test_cmd_history_json_output(monkeypatch, capsys) -> None:
    jobs = [{"job_id": "abc", "category": "tv", "status": "completed"}]
    monkeypatch.setattr(db, "get_job_history", lambda limit=20: jobs)

    rc = headless_mod.cmd_history(SimpleNamespace(limit=20, json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["jobs"] == jobs


# ============================================================
#  cmd_indexers
# ============================================================


def test_cmd_indexers_json_and_exit_code(monkeypatch, capsys) -> None:
    conf = SimpleNamespace()
    monkeypatch.setattr(config_mod, "get_config", lambda: conf)
    monkeypatch.setattr(db, "get_detailed_stats", lambda: {"uploads": {"by_destination": {"geek": {"success": 3, "failed": 1}}}})

    idx_enabled = SimpleNamespace(id="geek", name="NZBGeek", enabled=True, website="https://geek", submit_url="https://geek/api", categories=None)
    idx_disabled = SimpleNamespace(id="omg", name="OMG", enabled=False, website=None, submit_url=None, categories=None)

    monkeypatch.setattr(registry_mod, "get_registry", lambda: SimpleNamespace(all=lambda: [idx_enabled, idx_disabled]))
    monkeypatch.setattr(registry_mod, "resolve_indexer_enabled", lambda idx, _conf: idx.enabled)

    rc = headless_mod.cmd_indexers(SimpleNamespace(json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled_count"] == 1
    assert payload["indexers"][0]["success"] == 3

    monkeypatch.setattr(registry_mod, "resolve_indexer_enabled", lambda _idx, _conf: False)

    rc = headless_mod.cmd_indexers(SimpleNamespace(json=True))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled_count"] == 0


# ============================================================
#  cmd_queue - queue-level actions
# ============================================================


def test_cmd_queue_status_json(monkeypatch, capsys) -> None:
    from logic import services

    jobs = [
        {"job_id": "j1", "status": "running", "category": "tv", "progress": "Uploading"},
        {"job_id": "j2", "status": "queued", "category": "movies", "priority": 0, "started_at": "2026-01-01"},
        {"job_id": "j3", "status": "completed", "category": "misc"},
    ]
    fake_service = _FakeQueueService(
        jobs=jobs,
        control={"paused": False, "active": {"job_id": "j1", "status": "running", "category": "tv"}},
    )
    monkeypatch.setattr(services, "get_upload_service", lambda: fake_service)

    rc = headless_mod.cmd_queue(SimpleNamespace(json=True))  # no queue_command -> defaults to status

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"] == {"running": 1, "queued": 1, "finished": 1}
    assert payload["running"][0]["job_id"] == "j1"
    assert payload["queued"][0]["job_id"] == "j2"
    assert payload["finished"][0]["job_id"] == "j3"
    assert payload["control"]["paused"] is False


def test_cmd_queue_pause_and_resume(monkeypatch, capsys) -> None:
    from logic import services

    fake_service = _FakeQueueService()
    monkeypatch.setattr(services, "get_upload_service", lambda: fake_service)

    rc = headless_mod.cmd_queue(SimpleNamespace(queue_command="pause", json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "paused"

    rc = headless_mod.cmd_queue(SimpleNamespace(queue_command="resume", json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "running"

    assert ("pause_queue", True) in fake_service.calls
    assert ("resume_queue",) in fake_service.calls


def test_cmd_queue_stop_and_stop_clear(monkeypatch, capsys) -> None:
    from logic import services

    fake_service = _FakeQueueService()
    monkeypatch.setattr(services, "get_upload_service", lambda: fake_service)

    rc = headless_mod.cmd_queue(SimpleNamespace(queue_command="stop", clear=False, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "stopped"

    rc = headless_mod.cmd_queue(SimpleNamespace(queue_command="stop", clear=True, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "clearing"
    assert payload["cleared_jobs"] == 2

    assert ("stop_queue",) in fake_service.calls
    assert ("stop_queue_and_clear",) in fake_service.calls


def test_cmd_queue_clear_and_revalidate(monkeypatch, capsys) -> None:
    from logic import services

    fake_service = _FakeQueueService()
    monkeypatch.setattr(services, "get_upload_service", lambda: fake_service)

    rc = headless_mod.cmd_queue(SimpleNamespace(queue_command="clear", json=True))
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"cleared": 4}

    rc = headless_mod.cmd_queue(SimpleNamespace(queue_command="revalidate", skip_paused=True, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["inspected"] == 3
    assert ("revalidate_queued_jobs", False) in fake_service.calls


# ============================================================
#  cmd_queue - per-job actions
# ============================================================


def test_cmd_queue_job_pause_resume_not_found(monkeypatch, capsys) -> None:
    from logic import services

    fake_service = _FakeQueueService()
    monkeypatch.setattr(services, "get_upload_service", lambda: fake_service)

    rc = headless_mod.cmd_queue(SimpleNamespace(queue_command="job", job_command="pause", job_id="job-ok", json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == "job-ok"

    rc = headless_mod.cmd_queue(SimpleNamespace(queue_command="job", job_command="resume", job_id="job-missing", json=True))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"


def test_cmd_queue_job_stop_retry_promote(monkeypatch, capsys) -> None:
    from logic import services

    fake_service = _FakeQueueService()
    monkeypatch.setattr(services, "get_upload_service", lambda: fake_service)

    rc = headless_mod.cmd_queue(
        SimpleNamespace(queue_command="job", job_command="stop", job_id="job-ok", clear=False, json=True)
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "stopping"

    rc = headless_mod.cmd_queue(
        SimpleNamespace(queue_command="job", job_command="retry", job_id="job-ok", json=True)
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == "new-job-1"
    assert payload["retry_of"] == "job-ok"

    rc = headless_mod.cmd_queue(
        SimpleNamespace(queue_command="job", job_command="retry", job_id="job-bad", json=True)
    )
    assert rc == 1
    capsys.readouterr()  # discard the error payload before the next assertion

    rc = headless_mod.cmd_queue(
        SimpleNamespace(queue_command="job", job_command="promote", job_id="job-ok", json=True)
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "promoted"

    rc = headless_mod.cmd_queue(
        SimpleNamespace(queue_command="job", job_command="promote", job_id="job-missing", json=True)
    )
    assert rc == 1


# ============================================================
#  cmd_logs
# ============================================================


def test_cmd_logs_json(capsys) -> None:
    console.clear()
    console.log("First message", "INFO")
    console.log("Second message", "ERROR")

    rc = headless_mod.cmd_logs(SimpleNamespace(lines=100, json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["msg"] for entry in payload["logs"]] == ["First message", "Second message"]
    assert payload["count"] == 2


def test_cmd_logs_empty_buffer_human_output(capsys) -> None:
    console.clear()

    rc = headless_mod.cmd_logs(SimpleNamespace(lines=100, json=False))

    assert rc == 0
    assert "No log entries yet." in capsys.readouterr().out


# ============================================================
#  run_headless dispatch wiring
# ============================================================


def test_run_headless_dispatches_queue_and_logs(monkeypatch, capsys) -> None:
    from logic import services

    monkeypatch.setattr(services, "init_app", lambda: None)
    fake_service = _FakeQueueService()
    monkeypatch.setattr(services, "get_upload_service", lambda: fake_service)

    rc = headless_mod.run_headless(["queue", "status", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "counts" in payload

    console.clear()
    console.log("Hello", "INFO")

    rc = headless_mod.run_headless(["logs", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["logs"][0]["msg"] == "Hello"
