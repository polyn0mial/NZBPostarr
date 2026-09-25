# ruff: noqa: F403,F405

"""The runtime owns the job engine: building it starts nothing, first use starts it once."""

from tests.support import *

from logic import runtime
from logic.jobs import engine as engine_mod


class _RecordingThread:
    created: list["_RecordingThread"] = []

    def __init__(self, target=None, daemon=None):
        self._target = target
        self._alive = False
        _RecordingThread.created.append(self)

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive


def _refuse(*_args, **_kwargs):
    raise AssertionError("a read-only CLI command must not build or start the job engine")


def test_building_the_engine_starts_no_thread_and_first_use_starts_it_once(monkeypatch, tmp_path) -> None:
    _RecordingThread.created = []
    monkeypatch.setattr(engine_mod, "get_config", lambda: SimpleNamespace(script_dir=tmp_path))
    monkeypatch.setattr(engine_mod.database, "db_load_queue", lambda: [])
    monkeypatch.setattr(engine_mod.threading, "Thread", _RecordingThread)
    monkeypatch.setattr(runtime, "_engine", None)

    engine = runtime.get_engine()

    assert _RecordingThread.created == []
    assert engine.started is False
    assert runtime.get_engine() is engine

    assert runtime.ensure_engine_started() is engine
    assert runtime.ensure_engine_started() is engine

    assert len(_RecordingThread.created) == 1
    assert engine.started is True


def test_cli_queue_status_reads_the_state_file_without_the_scheduler(monkeypatch, capsys, tmp_path) -> None:
    state_file = tmp_path / "data" / "state" / "job_queue_state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        json.dumps(
            {
                "queue_processing_paused": True,
                "jobs": [
                    {
                        "job_id": "waiting",
                        "category": "movies",
                        "status": "stopped",
                        "progress": "Stopped by user",
                        "paths": [str(tmp_path / "Next.Movie.mkv")],
                        "kwargs": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = state_file.read_text(encoding="utf-8")
    monkeypatch.setattr(config_mod, "get_config", lambda: SimpleNamespace(script_dir=tmp_path))
    monkeypatch.setattr(runtime, "init_core", lambda: None)
    monkeypatch.setattr(engine_mod.JobEngine, "__init__", _refuse)
    monkeypatch.setattr(engine_mod.JobEngine, "start", _refuse)

    assert cli_run.run_headless(["queue", "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [job["job_id"] for job in payload["queued"]] == ["waiting"]
    assert payload["control"]["paused"] is True
    assert state_file.read_text(encoding="utf-8") == before


def test_cli_status_never_builds_the_engine(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runtime, "init_core", lambda: None)
    monkeypatch.setattr(engine_mod.JobEngine, "__init__", _refuse)
    monkeypatch.setattr(engine_mod.JobEngine, "start", _refuse)

    assert cli_run.run_headless(["status", "--json"]) in (0, 1)
    assert json.loads(capsys.readouterr().out)
