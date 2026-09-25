"""Where NZBPostarr persists state, resolved through the code that uses it.

Later batches may change how this test reaches each location (imports, function
names); they must never change the expected strings.
"""

import pathlib
from pathlib import Path
from types import SimpleNamespace

from api import pending as pending_api
from core import config as config_mod
from core.db import models as db_models
from core.db import queue_items as db_queue_items
from logic.jobs import engine as engine_mod
from logic.system import backup as system_backup
from logic.system import lifecycle, updater
from logic.stream import monitors as stream_monitors
from logic.pending import overrides as pending_overrides
from logic.classify import anime as anime_cache
from tests.support import _run_async

APP_ROOT = config_mod.APP_ROOT


def _rel(path: Path, root: Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def test_job_queue_state_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(engine_mod, "get_config", lambda: SimpleNamespace(script_dir=tmp_path))
    monkeypatch.setattr(db_queue_items, "db_load_queue", lambda: [])
    service = engine_mod.JobEngine()

    assert _rel(service._jobs_state_path, tmp_path) == "data/state/job_queue_state.json"
    assert _rel(service._jobs_state_backup_path, tmp_path) == "data/state/job_queue_state.json.bak"


def test_stream_monitor_state_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stream_monitors, "get_config", lambda: SimpleNamespace(script_dir=tmp_path))

    assert _rel(stream_monitors._stream_monitor_state_path(), tmp_path) == "data/state/stream_monitors.json"


def test_app_root_state_files(monkeypatch) -> None:
    monkeypatch.setattr(anime_cache, "_cache_path", None)
    monkeypatch.setattr(pending_overrides, "_path", None)

    assert _rel(anime_cache._get_cache_path(), APP_ROOT) == "data/cache/anime.json"
    assert _rel(updater.STATE_FILE, APP_ROOT) == "data/updater/state.json"
    assert _rel(system_backup.BACKUP_DIR, APP_ROOT) == "data/updater/backups"
    assert _rel(pending_overrides._get_path(), APP_ROOT) == "data/category_overrides.json"
    assert _rel(pending_overrides._legacy_path(), APP_ROOT) == "category_overrides.json"


def test_deployed_revision_file(monkeypatch) -> None:
    probed: list[Path] = []
    real_exists = pathlib.Path.exists

    def recording_exists(self, *args, **kwargs):
        probed.append(self)
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "exists", recording_exists)
    lifecycle.runtime_revision()
    monkeypatch.undo()

    assert [_rel(path, APP_ROOT) for path in probed if path.name == "deployed_revision.json"] == [
        "data/deployed_revision.json"
    ]


def test_queue_items_table() -> None:
    assert db_models.QueueItem.__tablename__ == "queue_items"


def test_pending_group_order_config_keys(monkeypatch) -> None:
    saved: list[dict[str, object]] = []

    def recording_save(updates):
        saved.append(dict(updates))
        return True

    monkeypatch.setattr(config_mod, "save_config", recording_save)
    _run_async(pending_api.update_pending_group_order(pending_api.PendingGroupOrderRequest(order=["a"])))
    _run_async(pending_api.update_pending_group_order_locked(pending_api.PendingGroupOrderLockedRequest(locked=True)))

    assert [sorted(update) for update in saved] == [
        ["pending_external_group_order"],
        ["pending_external_group_order_locked"],
    ]
    assert {"pending_external_group_order", "pending_external_group_order_locked"} <= set(
        config_mod.Config.model_fields
    )
