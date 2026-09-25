# ruff: noqa: F401,F811

"""
Unified testing suite for NZBPostarr.
Integrates validation, telemetry, and logic tests into a unified Pytest structure.
"""

import asyncio

import importlib.util

import json

import os

import re

import sys

import threading

import time

from concurrent.futures import ThreadPoolExecutor

from datetime import datetime, timedelta, timezone

from pathlib import Path

from types import SimpleNamespace

from typing import Iterator

from urllib.parse import urlparse

import pytest

import yaml

from fastapi import HTTPException, Request

from fastapi.responses import JSONResponse

try:
    from playwright.sync_api import Page, expect, sync_playwright
except ImportError:  # pragma: no cover - optional dependency for local E2E smoke tests
    Page = object
    expect = None
    sync_playwright = None

import app as app_mod

from core import config as config_mod

from core import database as db

from core import redaction

from core import registry as registry_mod

from core.config import Config

from core.database import (
    DatabaseOperationalError,
    JobHistory,
    SystemStat,
    Upload,
    UploadResult,
    get_system_stats_history,
    init_database,
    record_nntp_success,
    record_system_stats,
    session_scope,
    update_db_destination,
)

from core.registry import (
    AuthConfig,
    IndexerDefinition,
    resolve_indexer_api_key,
    resolve_indexer_username,
    submit_to_indexer,
)

from logic import updater
from logic import autoupload as autoupload
from logic.pending import children as pending_children
from logic.pending import completion as pending_completion
from logic.pending import index as pending_index
from logic.pending import overrides as pending_overrides
from logic.pending import rules as pending_rules
from logic.pending import selection as pending_selection
from logic.pending import tree as pending_tree
from logic.pending import view as pending_view
from logic.classify import content as classify_content
from logic.classify import explicit as classify_explicit
from logic.classify import names as classify_names
from logic.classify import tv_packs as classify_tv_packs
from logic.classify.anime import cached_lookup as anime_cached_lookup
from logic.pending import roots as pending_roots

from logic import headless as headless_mod

from logic.services import ConsoleBuffer, console

from logic.stats_engine import format_seconds, parse_speed_to_bps

from logic.uploaders import SubmitResult

from tests.webui._source import _queue_source

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_repo_script(name: str):
    """Load repository maintenance code without adding another package folder."""
    path = REPO_ROOT / ".github" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_nzbpostarr_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load repository script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            error["exc"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if "exc" in error:
        raise error["exc"]
    return result.get("value")

def _playwright_browsers_available() -> bool:
    if sync_playwright is None:
        return False

    try:
        with sync_playwright() as playwright:
            executable = Path(str(playwright.chromium.executable_path or ""))
            return executable.exists()
    except Exception:
        return False

def _path_matches(response_url: str, expected_path: str) -> bool:
    return urlparse(response_url).path == expected_path

def _expect_title(page: Page, title: str) -> None:
    assert expect is not None
    expect(page).to_have_title(re.compile(rf"^{re.escape(title)} - NZBPostarr$"))

def _page_smoke_locator(page: Page, locator_kind: str, locator_value: str):
    if locator_kind == "heading":
        return page.get_by_role("heading", name=locator_value)
    if locator_kind == "id":
        return page.locator(locator_value)
    if locator_kind == "placeholder":
        return page.get_by_placeholder(locator_value).first
    raise ValueError(f"Unsupported locator kind: {locator_kind}")

def _read_repo_text(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _touch(path: Path, content: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path

def _ignored_path_reasons(result) -> list[tuple[Path, str]]:
    return [(item.path, item.reason) for item in result.ignored_paths]

def _make_queue_service_stub(tmp_path: Path, *, queue_items=None):
    from logic import queueing

    service = object.__new__(queueing.QueueServiceMixin)
    service._lock = threading.Lock()
    service._jobs = {}
    service._processes = {}
    service._queue_items = list(queue_items or [])
    service._queue_processing_paused = False
    service._jobs_state_path = tmp_path / "job_queue_state.json"
    service._jobs_state_backup_path = tmp_path / "job_queue_state.json.bak"
    return service

def _make_upload_service_stub(*, jobs=None, queue_paused=False):
    from logic.services import UploadService

    service = object.__new__(UploadService)
    service._lock = threading.Lock()
    service._jobs = dict(jobs or {})
    service._processes = {}
    service._queue_processing_paused = queue_paused
    service._persist_jobs_locked = lambda: None
    return service

class _DummySubmitConfig:
    def __init__(self, api_key: str = "abc123", username: str = "") -> None:
        self._api_key = api_key
        self._username = username

    def get_api_key(self, _key: str) -> str:
        return self._api_key

    def get_username(self, _key: str) -> str:
        return self._username

def _capture_submit_request(monkeypatch, *, text: str = "OK", json_payload: dict | None = None) -> dict[str, object]:
    seen: dict[str, object] = {}

    class DummyResponse:
        status_code = 200

        def __init__(self) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {} if json_payload is None else dict(json_payload)

    def fake_request(*_args, **kwargs):
        for key in ("params", "data", "files"):
            if key in kwargs:
                seen[key] = kwargs[key]
        return DummyResponse()

    monkeypatch.setattr(registry_mod.requests, "request", fake_request)
    return seen

def _make_sample_nzb(tmp_path: Path) -> Path:
    return _touch(tmp_path / "sample.nzb", b"nzb-payload")

def _make_processing_conf(root: Path, category: str = "movies", **overrides):
    folder_attr_map = {
        "movies": "movies_folder",
        "tv": "tv_folder",
        "misc": "misc_folder",
    }

    class DummyConf:
        folder_size_limit_gb = 99
        folder_size_limit_enabled = True
        file_size_limit_gb = 0
        file_size_limit_enabled = True
        skip_files = None
        process_movies = True
        process_tv_episodes = True
        folder_paths = [{"path": str(root), "category": category}]
        movies_folder = root if category == "movies" else None
        tv_folder = root if category == "tv" else None
        misc_folder = root if category == "misc" else None

        def get_folders_for_category(self, cat: str) -> list[Path]:
            return [root] if cat == category else []

    conf = DummyConf()
    for key, value in overrides.items():
        setattr(conf, key, value)
    if folder_attr_map.get(category):
        setattr(conf, folder_attr_map[category], root)
    return conf

def _configure_run_job_basics(monkeypatch, processing_mod, root: Path, *, category: str = "movies") -> None:
    # Unit tests rely on monkeypatched config/indexers; run validation in-process
    # so a spawned interpreter cannot silently fall back to a developer's real
    # config (or to config.defaults.yaml in CI).
    monkeypatch.setenv("NZBPOSTARR_VALIDATE_ISOLATE", "0")
    monkeypatch.setattr(processing_mod, "check_tools", lambda *_a, **_kw: True)
    monkeypatch.setattr(processing_mod, "get_config", lambda: _make_processing_conf(root, category=category))
    monkeypatch.setattr(
        registry_mod,
        "get_enabled_indexers",
        lambda _conf: [
            SimpleNamespace(id="geek", name="NZBGeek", enabled=True, backfill=False, priority=False)
        ],
    )

def _make_process_single_conf(tmp_path: Path, *, max_connections: int = 10, include_script_dir: bool = False):
    nzb_dir = tmp_path / "nzb"
    tmp_sub_dir = tmp_path / "tmp_sub"
    mediainfo_dir = tmp_path / "mediainfo"

    class DummyServer:
        pass

    DummyServer.name = "Primary"
    DummyServer.backbone = ["NetNews"]
    DummyServer.max_connections = max_connections
    DummyServer.enabled = True

    class DummyConf:
        folder_size_limit_gb = 99
        folder_size_limit_enabled = True
        file_size_limit_gb = 0
        file_size_limit_enabled = True
        enable_backfill = True
        nntp_servers = [DummyServer()]
        test_run = False
        tmp_sub = tmp_sub_dir
        mediainfo_sub = mediainfo_dir
        script_dir = tmp_path if include_script_dir else None

        def get_nzb_path(self, name: str) -> Path:
            return nzb_dir / f"{name}.nzb"

    return DummyConf()

def _configure_process_single_environment(
    monkeypatch,
    processing_mod,
    conf,
    *,
    indexers: list[object] | None = None,
    duplicate_status: dict[str, str | None] | None = None,
    prepare_item=None,
    upload_item=None,
    priority_resolver=None,
) -> None:
    if indexers is None:
        indexers = [SimpleNamespace(id="geek", name="NZBGeek", backfill=True)]
    if duplicate_status is None:
        duplicate_status = {getattr(indexers[0], "id", "geek"): None}

    monkeypatch.setattr(processing_mod, "get_config", lambda: conf)
    monkeypatch.setattr(processing_mod, "prepare_item", prepare_item or (lambda *_args, **_kwargs: True))
    monkeypatch.setattr(
        processing_mod,
        "upload_item",
        upload_item or (lambda *_args, **_kwargs: {"duration": 0.1, "speed_bps": 1, "server_name": "Primary"}),
    )
    monkeypatch.setattr(processing_mod, "record_nntp_success", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processing_mod, "update_db_destination", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(processing_mod, "compute_size_uncached", lambda _path: 10)
    monkeypatch.setattr(db, "check_duplicate_dynamic", lambda *_args, **_kwargs: duplicate_status)
    monkeypatch.setattr(registry_mod, "get_enabled_indexers", lambda _conf: indexers)
    monkeypatch.setattr(registry_mod, "resolve_indexer_enabled", lambda _idx, _conf: True)
    monkeypatch.setattr(
        registry_mod,
        "resolve_indexer_priority",
        priority_resolver or (lambda _idx, _conf: False),
    )

def _make_upload_request_service_capture() -> tuple[dict[str, object], object]:
    captured: dict[str, object] = {}

    class DummyService:
        def start_processing_job_requests(self, requests, **kwargs):
            captured["requests"] = requests
            captured["kwargs"] = kwargs
            return ["job-123"]

    return captured, DummyService()

def _make_pending_snapshot(
    *,
    items=None,
    indexers=None,
    summary=None,
    cached_at: float = 123.0,
    **extra,
) -> dict[str, object]:
    return {
        "items": items or {"tv": [], "movies": [], "misc": [], "external": []},
        "indexers": indexers or [],
        "summary": summary
        or {
            "tv_shows": 0,
            "tv_episodes": 0,
            "movies": 0,
            "misc": 0,
            "external": 0,
            "total": 0,
        },
        "cached_at": cached_at,
        "skip_files": {"enabled": False, "display_mode": "disabled"},
        "categories": [],
        **extra,
    }

def _pending_lazy_children(snapshot: dict[str, object], item: dict[str, object]) -> list[dict[str, object]]:
    from logic import autoupload as autoupload
    from logic.pending import children as pending_children
    from logic.pending import completion as pending_completion
    from logic.pending import index as pending_index
    from logic.pending import overrides as pending_overrides
    from logic.pending import rules as pending_rules
    from logic.pending import selection as pending_selection
    from logic.pending import tree as pending_tree
    from logic.pending import view as pending_view

    result = pending_children.build_external_children_for_request(
        snapshot,
        str(item.get("key") or ""),
        str(item.get("path") or ""),
    )
    return result["children"]

def _make_pending_index_manager(states: list[dict[str, object]]):
    class _FakeIndex:
        def __init__(self, state_queue):
            self._state_queue = list(state_queue)
            self.reasons: list[str] = []
            self.calls = 0

        def get_state(self):
            self.calls += 1
            if len(self._state_queue) > 1:
                return self._state_queue.pop(0)
            return self._state_queue[0]

        def request_refresh(self, reason: str = "manual"):
            self.reasons.append(reason)

    return _FakeIndex(states)

def _make_dashboard_summary_service():
    from logic.services import UploadService

    service = object.__new__(UploadService)
    service._lock = threading.Lock()
    service._stats_cache = {}
    service._stats_cache_ts = 0.0
    return service

def _configure_pending_snapshot_environment(
    monkeypatch,
    conf,
    *,
    dashboard_data,
    configured_folders,
    indexers=None,
    available_categories=None,
    compute_size_uncached=None,
    resolve_backfill=None,
):
    class _Registry:
        def enabled(self, _conf):
            return list(indexers or [])

    for owner in (pending_tree, pending_completion, pending_view):
        monkeypatch.setattr(owner, "get_config", lambda: conf)
    if callable(dashboard_data):
        monkeypatch.setattr(
            db,
            "get_dashboard_data",
            lambda ids: _as_dashboard_data(dashboard_data(ids)),
        )
    else:
        monkeypatch.setattr(
            db,
            "get_dashboard_data",
            lambda _ids: _as_dashboard_data(dashboard_data),
        )
    monkeypatch.setattr(
        pending_tree,
        "get_configured_category_folders",
        lambda *_a, **_kw: configured_folders,
    )
    if compute_size_uncached is not None:
        monkeypatch.setattr(pending_tree, "compute_size_uncached", compute_size_uncached)
    monkeypatch.setattr("core.registry.get_registry", lambda: _Registry())
    monkeypatch.setattr("core.registry.get_available_categories", lambda: list(available_categories or []))
    monkeypatch.setattr(
        "core.registry.resolve_indexer_backfill",
        resolve_backfill or (lambda _idx, _conf: False),
    )

def _as_dashboard_data(value):
    """Return get_dashboard_data's 4-tuple (fully_done, upload_map, failed_map, filesize_by_indexer).

    Older fixtures give only the first three values; the missing filesize map
    means "no stored sizes", which keeps name-only completion matching.
    """
    values = tuple(value)
    if len(values) == 3:
        return (*values, {})
    return values

def _configure_pending_scan_all(
    monkeypatch,
    folder_category: str,
    folder_path: Path,
    *,
    dashboard_data=(set(), {}, {}, {}),
    anime_cache_lookup=lambda _name: False,
    indexers: list[object] | None = None,
    available_categories: list[object] | None = None,
):
    class _Conf:
        folder_paths = [{"category": folder_category, "path": str(folder_path)}]
        dynamic_packs = True

    class _Registry:
        def enabled(self, _conf):
            return list(indexers or [])

        def all(self):
            return list(indexers or [])

    for owner in (pending_tree, pending_completion, pending_view):
        monkeypatch.setattr(owner, "get_config", lambda: _Conf())
    monkeypatch.setattr(db, "get_dashboard_data", lambda _ids: _as_dashboard_data(dashboard_data))
    monkeypatch.setattr(
        pending_tree,
        "get_configured_category_folders",
        lambda _conf, include_external=True, must_exist=True: [(folder_category, folder_path)],
    )
    monkeypatch.setattr("logic.classify.anime.get_cached", anime_cache_lookup)
    monkeypatch.setattr(registry_mod, "get_registry", lambda: _Registry())
    monkeypatch.setattr(registry_mod, "get_available_categories", lambda: list(available_categories or []))

    return app_mod._scan_pending_all()

def _get_pending_top_item(result: dict[str, object], location: str) -> dict[str, object]:
    if location == "external":
        return result["items"]["external"][0]["items"][0]
    return result["items"][location][0]

def _write_valid_test_nzb(path: Path) -> None:
    path.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<nzb xmlns=\"http://www.newzbin.com/DTD/2003/nzb\">
  <file poster=\"Anonymous\" date=\"1700000000\" subject=\"sample.bin yEnc (1/1) 10\">
    <groups>
      <group>alt.binaries.misc</group>
    </groups>
    <segments>
      <segment bytes=\"10\" number=\"1\">sample@test</segment>
    </segments>
  </file>
</nzb>
""",
        encoding="utf-8",
    )

def _make_request(path: str) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    return Request(scope)

def _run_pending_items_anime_check(monkeypatch, *, cached_lookup_result) -> tuple[list[tuple], dict[str, object]]:
    snapshot = {
        "items": {
            "tv": [],
            "movies": [{"name": "Akira.1988.1080p.BluRay", "itype": "Movie"}],
            "misc": [],
            "external": [],
        },
        "indexers": [],
        "summary": {
            "tv_shows": 0,
            "tv_episodes": 0,
            "movies": 1,
            "misc": 0,
            "external": 0,
            "total": 1,
        },
        "cached_at": 999.0,
        "skip_files": {"enabled": False, "display_mode": "disabled"},
        "categories": [],
    }

    class _FakeIndex:
        def get_state(self):
            return {
                "snapshot": snapshot,
                "snapshot_ts": 999.0,
                "ready": True,
                "refreshing": False,
                "last_error": None,
            }

        def request_refresh(self, reason: str = "manual"):
            _ = reason

    class _Conf:
        enable_anime_checking = True

    started: list[tuple] = []

    class _DummyThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self._target = target
            self._args = args
            self._daemon = daemon
            self._name = name

        def start(self):
            started.append((self._target, self._args, self._daemon))

    monkeypatch.setattr(app_mod, "_pending_index", _FakeIndex())
    monkeypatch.setattr(app_mod, "get_config", lambda: _Conf())
    monkeypatch.setattr(app_mod.threading, "Thread", _DummyThread)
    monkeypatch.setattr(app_mod, "_anime_check_inflight", False)
    monkeypatch.setattr(app_mod, "_anime_check_thread", None)
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: cached_lookup_result)

    _ = app_mod.get_pending_items()

    return started, snapshot

@pytest.fixture()
def isolated_sqlite_db(tmp_path, monkeypatch) -> Iterator[None]:
    """Run DB tests against an isolated SQLite file and reset the engine cache."""

    def _tmp_log_db(_self) -> Path:
        path = tmp_path / "usenet_uploads.test.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # Make database module use a temp DB file instead of the user's real history DB.
    monkeypatch.setattr(config_mod.Config, "log_db", property(_tmp_log_db))

    # Reset cached engine/session factory so the patched path takes effect.
    engine = getattr(db, "_ENGINE", None)
    if engine is not None:
        engine.dispose()
    db._ENGINE = None
    db._SESSION_FACTORY = None

    assert db.init_database() is True
    yield

    engine = getattr(db, "_ENGINE", None)
    if engine is not None:
        engine.dispose()
    db._ENGINE = None
    db._SESSION_FACTORY = None

def _set_duplicate_checking(monkeypatch, enabled: bool) -> None:
    class _Conf:
        enable_duplicate_checking = enabled

    monkeypatch.setattr(
        "logic.services.get_config",
        lambda: _Conf(),
    )

def _make_force_test_indexer(idx_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=idx_id, name=idx_id)

def _make_force_test_server() -> SimpleNamespace:
    return SimpleNamespace(
        name="TestServer",
        backbone=["NetNews"],
        max_connections=10,
        enabled=True,
    )

def _make_force_test_indexer_map(idx_id: str, backfill: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=idx_id, backfill=backfill)

def _make_success_indexer(text_patterns, duplicate_patterns=None):
    return IndexerDefinition(
        id="t",
        name="Test",
        description="",
        website="",
        method="POST",
        submit_url="https://example.com",
        success=registry_mod.SuccessPatterns(
            text_patterns=text_patterns,
            duplicate_patterns=duplicate_patterns or [],
        ),
    )

def _fake_success_response(text):
    from unittest.mock import MagicMock

    response = MagicMock()
    response.text = text
    response.json.side_effect = ValueError
    return response

__all__ = [name for name in globals() if not name.startswith("__")]
