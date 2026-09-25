"""job_queue_state.json survives a restart: restore then persist, and nothing auto-resumes."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tests.characterization._snapshot import HERE, assert_json_snapshot
from tests.conftest import _make_queue_service_stub

FIXTURE = HERE / "job_queue_state.json"
EXPECTED = HERE / "job_queue_state.restored.json"


def _placeholders(tmp_path: Path) -> dict[str, str]:
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat()
    return {"{ROOT}": tmp_path.as_posix(), "{RECENT}": recent}


def _fill(value: Any, placeholders: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _fill(item, placeholders) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, placeholders) for item in value]
    if isinstance(value, str):
        for placeholder, text in placeholders.items():
            value = value.replace(placeholder, text)
    return value


def _unfill(value: Any, placeholders: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _unfill(item, placeholders) for key, item in value.items()}
    if isinstance(value, list):
        return [_unfill(item, placeholders) for item in value]
    if isinstance(value, str):
        value = value.replace("\\", "/")
        for placeholder, text in placeholders.items():
            value = value.replace(text, placeholder)
    return value


def _restore_and_persist(tmp_path: Path, state: dict[str, Any], launched: list[str]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    service = _make_queue_service_stub(tmp_path)
    service._jobs_state_path.write_text(json.dumps(state), encoding="utf-8")
    service._launch_job = lambda job: launched.append(str(job["job_id"]))
    with service._lock:
        service._restore_jobs_from_disk_locked()
    persisted = json.loads(service._jobs_state_path.read_text(encoding="utf-8"))
    return service, persisted


def test_job_queue_state_round_trips_without_auto_resume(tmp_path) -> None:
    placeholders = _placeholders(tmp_path)
    state = _fill(json.loads(FIXTURE.read_text(encoding="utf-8")), placeholders)
    launched: list[str] = []

    service, persisted = _restore_and_persist(tmp_path, state, launched)

    assert_json_snapshot(EXPECTED, _unfill(persisted, placeholders))
    backup = json.loads(service._jobs_state_backup_path.read_text(encoding="utf-8"))
    assert backup == persisted

    statuses = {job_id: job["status"] for job_id, job in service._jobs.items()}
    assert statuses["paused01"] == "paused"
    assert "running" not in statuses.values()
    assert "stopping" not in statuses.values()
    # A resumable stopped job holds the whole queue until the user resumes it.
    assert service._queue_processing_paused is True
    service._try_start_queued()
    assert launched == []

    again, persisted_again = _restore_and_persist(tmp_path / "again", persisted, launched)
    assert persisted_again == persisted
    assert again._jobs["paused01"]["status"] == "paused"
    again._try_start_queued()
    assert launched == []
