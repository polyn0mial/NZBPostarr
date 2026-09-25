"""Regression tests for the queue-backend and processing fixes ported from the live server."""

import subprocess
from collections import deque

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from logic.classify import explicit as classify_explicit, tv_packs as classify_tv_packs

from tests.conftest import _make_queue_service_stub, pipeline_facade as processing

from logic.pending import completion as pending_completion
from logic.pending import rules as pending_rules
from logic.pending import tree as pending_tree
from logic.pipeline.plan import _iter_work_items
from logic.jobs import models as job_models
from logic.jobs import requests as job_requests
from logic.jobs import staging as job_staging
from logic.jobs.models import ProcessingJobRequest
from logic.jobs.revalidate import revalidation_targets


def _touch_file(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- processing


def test_mediainfo_names_are_written_literally(tmp_path) -> None:
    # processing-01: no backslash escapes, no group-reference error for names starting with a digit.
    base = tmp_path / "media"
    target = _touch_file(base / "Movies" / "1917 (2019) 1080p-GRP.mkv")
    output = "Complete name : /abs/x.mkv\nFolder name : /abs\nFile name : x.mkv\n"

    sanitized = processing._sanitize_mediainfo_output(output, target, SimpleNamespace(base_folder=base))

    assert "Complete name : Movies/1917 (2019) 1080p-GRP.mkv" in sanitized
    assert "Folder name : Movies" in sanitized
    assert "File name : 1917 (2019) 1080p-GRP.mkv" in sanitized
    assert "\\" not in sanitized


def test_old_escaped_mediainfo_sidecars_are_not_reused(tmp_path) -> None:
    # processing-D04: sidecars written by the old re.escape code are regenerated once.
    old = tmp_path / "old.mediainfo.nfo"
    old.write_text("General\nComplete name : Movies/Movie\\.2020\\ 1080p\\-GRP\\.mkv\n", encoding="utf-8")
    new = tmp_path / "new.mediainfo.nfo"
    new.write_text("General\nComplete name : Movies/Movie.2020 1080p-GRP.mkv\n", encoding="utf-8")

    assert processing._mediainfo_sidecar_has_escaped_names(old) is True
    assert processing._mediainfo_sidecar_has_escaped_names(new) is False


def test_rootless_file_key_includes_pack_folder(tmp_path) -> None:
    # processing-06: same-named episodes of two packs get distinct keys.
    first = _touch_file(tmp_path / "X.S01.playWEB" / "ep.mkv")
    second = _touch_file(tmp_path / "X.S01.heb" / "ep.mkv")

    assert processing._build_item_key(first, None) == "X.S01.playWEB/ep.mkv"
    assert processing._build_item_key(second, None) == "X.S01.heb/ep.mkv"
    assert processing._build_item_key(first, tmp_path) == "X.S01.playWEB/ep.mkv"
    assert processing._build_item_key(tmp_path / "X.S01.heb", None) == "X.S01.heb"


def test_already_exists_message_names_folder_and_size(tmp_path) -> None:
    # processing-07 with DECISIONS' plain ASCII hyphen.
    root = tmp_path / "tv"
    path = root / "Show.S01.1080p-GRP" / "ep.mkv"

    message = processing._already_exists_skip_message(path, root, 1432 * 1024 * 1024)

    assert message == "⏩ 'ep.mkv' [Show.S01.1080p-GRP] (1432 MB) already exists on all selected destinations - skipped"
    assert "—" not in message
    assert processing._already_exists_skip_message(root / "ep.mkv", root, 0).endswith("'ep.mkv' already exists on all selected destinations - skipped")


def test_folder_rows_do_not_bump_and_are_pinned(tmp_path, monkeypatch) -> None:
    # processing-02
    base = tmp_path / "tv"
    episode = _touch_file(base / "Show" / "Season 1" / "Show.S01E01.mkv", b"abc")
    calls: list[tuple] = []
    monkeypatch.setattr(processing, "record_nntp_success", lambda *a, **kw: calls.append(("nntp", a[0], kw)))
    monkeypatch.setattr(processing, "update_db_destination", lambda *a, **kw: calls.append(("dest", a[3], kw)))
    monkeypatch.setattr(processing, "pin_folder_ts_to_children", lambda key: calls.append(("pin", key, {})))

    processing._record_folder_hierarchy_rows(
        episode, base_folder=base, category="tv", dest_id="geek", upload_result={"duration": 1.0}
    )

    assert [(kind, key) for kind, key, _kw in calls] == [
        ("nntp", "Show"), ("dest", "Show"), ("pin", "Show"),
        ("nntp", "Show/Season 1"), ("dest", "Show/Season 1"), ("pin", "Show/Season 1"),
    ]
    assert all(kw.get("bump_timestamp") is False for kind, _key, kw in calls if kind == "nntp")
    assert all(kw.get("_bump_timestamp") is False for kind, _key, kw in calls if kind == "dest")


def test_indexer_duplicate_counts_as_already_posted(tmp_path, monkeypatch) -> None:
    # processing-08
    written: list[tuple] = []
    monkeypatch.setattr(processing, "update_db_destination", lambda *a, **kw: written.append((a[0], kw)))
    monkeypatch.setattr(processing, "_record_folder_hierarchy_rows", lambda *_a, **_kw: None)

    ok = processing._persist_submission_results(
        [("geek", False, "already exists", "duplicate"), ("omg", False, "boom", "error")],
        name="Release",
        item_size=10,
        key="Release",
        itype="Movies",
        item_path=tmp_path / "Release",
        base_folder=None,
        category="movies",
        test_mode=False,
        upload_result={"duration": 1.0},
    )

    assert ok is True
    assert written[0] == ("geek", {"itype": "Movies", "duration": 1.0})
    assert written[1][0] == "omg" and written[1][1]["status"] == "failed"


def test_shared_server_posts_once_with_submission_groups() -> None:
    # processing-09 (DECISIONS: keep " (P)" when a set has priority destinations).
    server = SimpleNamespace(name="Primary", backbone=["NetNews"], max_connections=10)
    upload_sets = [
        {"id": "geek", "dests": ["geek"], "backbone": "NetNews", "priority": True},
        {"id": "omg", "dests": ["omg"], "backbone": "NetNews", "priority": False},
    ]
    indexer_map = {idx: SimpleNamespace(id=idx, backfill=False) for idx in ("geek", "omg")}

    runs = processing._plan_upload_runs(
        upload_sets,
        all_servers=[server],
        dest_status={"geek": None, "omg": None},
        indexer_map=indexer_map,
        force=False,
        is_new=True,
        global_backfill=False,
    )

    assert len(runs) == 1
    upload_set, chosen = runs[0]
    assert chosen is server
    assert upload_set["id"] == "geek/omg (P)"
    assert upload_set["priority"] is True
    assert upload_set["submission_groups"] == [
        {"dests": ["geek"], "priority": True},
        {"dests": ["omg"], "priority": False},
    ]


def test_consecutive_failures_warn_and_reset(monkeypatch) -> None:
    # processing-11: warn from the fifth failure in a row, reset on success; the job continues.
    from logic.pipeline import runner

    warnings: list[str] = []
    monkeypatch.setattr(runner, "log_info", lambda msg, level="INFO": warnings.append(f"{level}:{msg}"))
    state = runner._JobRunState(total=10, effective_limit=None, test_mode=True)

    for _ in range(4):
        state.note_failure()
    assert warnings == []
    state.note_failure()
    assert warnings == ["WARN:5 consecutive item failures - continuing job."]
    assert state.stop_processing is False


def test_named_season_pack_is_not_staged_twice(tmp_path) -> None:
    # processing-04: a selected (or staged) pack with the same name suppresses inference.
    season = tmp_path / "src" / "Show.S01.1080p.WEB-DL"
    ep1 = _touch_file(season / "Show.S01E01.1080p.WEB-DL.mkv")
    ep2 = _touch_file(season / "Show.S01E02.1080p.WEB-DL.mkv")
    staged = tmp_path / "tmp" / "_filtered_packs" / "job" / "Show.S01.1080p.WEB-DL"
    staged.mkdir(parents=True)
    raw_items = [(staged, "tv"), (ep1, "tv"), (ep2, "tv")]

    result = processing._inject_inferred_tv_pack_entries(raw_items, SimpleNamespace(tmp_sub=tmp_path / "tmp"), None)

    assert result == raw_items


def test_stop_during_path_resolution_ends_the_job(tmp_path, monkeypatch) -> None:
    # processing-03
    progress: list[dict] = []
    monkeypatch.setattr(processing, "update_job_progress", lambda **kw: progress.append(kw))
    job = {"stop_requested": True}

    result = processing._collect_targeted_job_items(
        paths=[str(tmp_path)],
        item_hints=None,
        category="movies",
        conf=SimpleNamespace(),
        runtime_job=job,
        process_tv_episodes=True,
    )

    assert result is None
    assert progress == [{"status": "stopped"}]


def test_run_command_does_not_kill_a_running_tool_while_paused() -> None:
    # queue-backend-07: pause lets the current tool finish.
    from core import proc

    process = subprocess.Popen(
        [sys.executable, "-c", "print('one'); print('two')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    job = {"pause_requested": True, "status": "paused"}
    lines: deque = deque(maxlen=10)

    reader, stopped = proc._run_command_stream_output(process, job, lines, "test", None, True)
    process.wait(timeout=10)
    if reader is not None:
        reader.join(timeout=5)

    assert stopped is None
    assert process.returncode == 0
    assert list(lines) == ["one", "two"]


# ---------------------------------------------------------------- queue backend


def test_pause_keeps_lane_and_restored_pause_requeues_on_resume(tmp_path, monkeypatch) -> None:
    # queue-backend-07/08/09 and DECISIONS pause semantics.
    service = _make_queue_service_stub(tmp_path)
    service._jobs_state_path.write_text(
        json.dumps(
            {
                "queue_processing_paused": False,
                "jobs": [
                    {"job_id": "held", "category": "tv", "status": "paused", "started_at": "2026-01-01T00:00:00+00:00",
                     "paths": [str(tmp_path / "a.mkv")], "kwargs": {}},
                    {"job_id": "waiting", "category": "tv", "status": "queued", "started_at": "2026-01-01T00:00:01+00:00",
                     "paths": [str(tmp_path / "b.mkv")], "kwargs": {}},
                ],
            }
        ),
        encoding="utf-8",
    )
    launched: list[str] = []
    monkeypatch.setattr(service, "_launch_job", lambda job: launched.append(str(job["job_id"])))

    with service._lock:
        service._restore_jobs_from_disk_locked()
    held = service._jobs["held"]
    assert held["status"] == "paused"
    assert held["progress"] == "Recovered after restart - paused."
    assert revalidation_targets(service._jobs.values(), True) == [
        snap for snap in revalidation_targets(service._jobs.values(), True) if snap["job_id"] == "waiting"
    ]

    service._try_start_queued()
    assert launched == []  # the paused job keeps the lane

    assert service.resume_job("held") is True
    assert launched == ["held"]
    assert held["status"] == "queued"
    assert held["progress"] == "Re-queued after resume"


def test_remove_active_item_is_honoured_by_the_processing_loop(tmp_path) -> None:
    # queue-backend-10: removal changes what is uploaded, not only the row.
    service = _make_queue_service_stub(tmp_path)
    items = [_touch_file(tmp_path / f"Movie.{idx}.2026.mkv") for idx in range(3)]
    job = {"job_id": "active", "category": "movies", "status": "running", "started_at": "2026-01-01T00:00:00+00:00"}
    job_models.set_job_target_paths(job, [str(path) for path in items])
    job.pop("_paths", None)  # launched jobs read target_paths
    service._jobs = {"active": job}

    seen: list[Path] = []
    iterator = _iter_work_items(
        [(path, "movies") for path in items],
        preserve_explicit_order=True,
        runtime_job=job,
        paths=[str(path) for path in items],
    )
    seen.append(next(iterator)[1][0])
    assert service.remove_active_job_item("active", str(items[1])) is True
    assert service.remove_active_job_item("active", str(items[1])) is False
    seen.extend(entry[1][0] for entry in iterator)

    assert seen == [items[0], items[2]]
    assert job["events"][-1]["type"] == "item-removed"


def test_remove_last_active_item_does_not_resurrect_the_original_list(tmp_path) -> None:
    service = _make_queue_service_stub(tmp_path)
    items = [_touch_file(tmp_path / f"Movie.{idx}.2026.mkv") for idx in range(2)]
    job = {"job_id": "active", "category": "movies", "status": "paused", "started_at": "2026-01-01T00:00:00+00:00"}
    job_models.set_job_target_paths(job, [str(path) for path in items])
    job.pop("_paths", None)
    service._jobs = {"active": job}

    iterator = _iter_work_items(
        [(path, "movies") for path in items],
        preserve_explicit_order=True,
        runtime_job=job,
        paths=[str(path) for path in items],
    )
    first = next(iterator)[1][0]
    assert service.remove_active_job_item("active", str(items[1])) is True

    assert first == items[0]
    assert list(iterator) == []


def test_legacy_finished_jobs_file_is_imported_once(tmp_path) -> None:
    # queue-backend-06: the server's job_finished_state.json joins the main state file.
    service = _make_queue_service_stub(tmp_path)
    service._jobs_state_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    legacy = tmp_path / "job_finished_state.json"
    legacy.write_text(
        json.dumps(
            {
                "jobs": [
                    {"job_id": "old1", "category": "tv", "status": "completed",
                     "started_at": datetime.now(timezone.utc).isoformat(), "display_name": "TV - 3 items"},
                    {"job_id": "old2", "category": "tv", "status": "running", "started_at": "2026-01-01T00:00:00+00:00"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with service._lock:
        service._restore_jobs_from_disk_locked()

    assert service._jobs["old1"]["status"] == "completed"
    assert "old2" not in service._jobs
    assert not legacy.exists()
    assert (tmp_path / "job_finished_state.json.migrated").exists()
    persisted = json.loads(service._jobs_state_path.read_text(encoding="utf-8"))
    assert [row["job_id"] for row in persisted["jobs"]] == ["old1"]


def test_force_upload_request_skips_pack_expansion(tmp_path, monkeypatch) -> None:
    # queue-backend-02
    service = _make_queue_service_stub(tmp_path)
    season = tmp_path / "Show.S01.1080p.WEB-DL"
    _touch_file(season / "Show.S01E01.1080p.WEB-DL.mkv")
    captured: dict = {}
    monkeypatch.setattr(service, "start_upload_job", lambda **kw: captured.update(kw) or "job")

    def refuse(*_a, **_kw):
        raise AssertionError("force upload must not walk packs")

    monkeypatch.setattr(job_requests, "expand_explicit_pack_request_paths", refuse)
    request = ProcessingJobRequest(category="tv", paths=(str(season),), skip_pack_expansion=True)

    assert service.start_processing_job_request(request) == "job"
    assert captured["paths"] == [str(season)]


def test_selected_season_folder_expands_into_pack_and_episodes(tmp_path) -> None:
    # queue-backend-01: pack row plus exactly the valid episodes; extras excluded.
    season = tmp_path / "Show.Name.S01.1080p.WEB-DL"
    ep2 = _touch_file(season / "Show.Name.S01E02.1080p.WEB-DL.mkv")
    ep1 = _touch_file(season / "Show.Name.S01E01.1080p.WEB-DL.mkv")
    _touch_file(season / "readme.txt")
    request = ProcessingJobRequest(
        category="tv",
        paths=(str(season),),
        item_hints=({"path": str(season), "category": "tv", "itype": "TV Show"},),
    )

    expanded = job_requests.expand_explicit_pack_request_paths(request)

    assert list(expanded.paths) == [str(season), str(ep1), str(ep2)]
    assert [hint["queue_category_source"] for hint in expanded.item_hints] == [
        "resolved-pack-selection",
        "resolved-pack-selection-child",
        "resolved-pack-selection-child",
    ]


def test_queue_start_collapses_selected_pack_children_without_injecting_episodes(tmp_path) -> None:
    # queue-backend-03/04
    season = tmp_path / "Show.Name.S01.1080p.WEB-DL"
    ep1 = _touch_file(season / "Show.Name.S01E01.1080p.WEB-DL.mkv")
    _touch_file(season / "Show.Name.S01E02.1080p.WEB-DL.mkv")
    items = [
        {"path": str(season), "category": "tv", "itype": "TV Show", "is_dir": True},
        {"path": str(ep1), "category": "tv", "itype": "TV Episode"},
    ]

    summary = job_staging.prepare_start_items(items)

    assert [item["path"] for item in summary.runnable_items] == [str(season)]


# ---------------------------------------------------------------- pending


def test_tv_pack_rules_use_new_keys_and_map_legacy_ones(monkeypatch) -> None:
    # queue-backend-11/12
    video_exts = {".mkv"}
    config = SimpleNamespace(tv_pack_ignore={"enabled": True})
    monkeypatch.setattr("core.config.get_config", lambda: config)

    def reason(name: str) -> str:
        return classify_tv_packs._tv_pack_episode_rejection_reason(Path(name), video_exts)

    assert reason("Show.S01E01.1080p.AMZN.mkv") == "TV episode missing media source token"
    assert reason("Show.S01E01.1080p.AMZN.WEB-DL.mkv") == ""
    assert reason("Show.Name.Teaser.1080p.WEB-DL.mkv") == "TV season pack extra/sample"
    assert reason("Show.Name.Behind.The.Scenes.1080p.WEB-DL.mkv") == "No recognized episode pattern"

    config.tv_pack_ignore = {"enabled": True, "require_sxxexx": False}
    assert reason("Show.Name.Behind.The.Scenes.1080p.WEB-DL.mkv") == ""
    config.tv_pack_ignore = {"enabled": True, "ignore_extras": False, "ignore_non_video": False}
    assert reason("Show.Name.S01E03.Teaser.1080p.WEB-DL.mkv") == ""


def test_year_named_folder_with_episodes_is_not_a_movie(tmp_path) -> None:
    # queue-backend-13
    show = tmp_path / "Show (2019)"
    _touch_file(show / "Show.S01E01.1080p.WEB-DL.mkv")
    _touch_file(show / "Show.S01E02.1080p.WEB-DL.mkv")

    result = classify_explicit.resolve_explicit_path(show, anime_lookup=lambda _name: False)

    assert result.category != "movies"


def test_filepart_flags_mark_transfers_and_propagate(tmp_path) -> None:
    # queue-backend-14
    folder = tmp_path / "Release"
    done = _touch_file(folder / "a.mkv")
    _touch_file(folder / "nested" / "b.mkv.filepart")
    loose = _touch_file(tmp_path / "c.mkv")
    _touch_file(tmp_path / "c.mkv.filepart")
    other = _touch_file(tmp_path / "d.mkv")
    result = {
        "external": [
            {"items": [{"name": folder.name, "path": str(folder), "children": [{"name": done.name, "path": str(done)}]}]}
        ],
        "movies": [{"name": loose.name, "path": str(loose)}, {"name": other.name, "path": str(other)}],
    }

    pending_tree.stamp_filepart_flags(result)

    folder_item = result["external"][0]["items"][0]
    assert folder_item["has_filepart"] is True
    assert folder_item["children"][0]["has_filepart"] is True
    assert [item["has_filepart"] for item in result["movies"]] == [True, False]


def test_completion_requires_matching_stored_size() -> None:
    # queue-backend-15
    upload_map = {"a.mkv": {"geek", "omg"}}
    sizes = {"a.mkv": {"geek": 100}}

    assert pending_completion._lookup_upload_map_indexers(
        upload_map, "a.mkv", filesize_by_indexer=sizes, current_size=100
    ) == {"geek", "omg"}
    assert pending_completion._lookup_upload_map_indexers(
        upload_map, "a.mkv", filesize_by_indexer=sizes, current_size=200
    ) == {"omg"}


def test_indexer_context_serves_stale_cache_while_refreshing(monkeypatch) -> None:
    # queue-backend-17
    stale = ([], ["geek"], True, {}, set(), None, {}, {})
    fresh = ([], ["geek"], True, {"a": {"geek"}}, set(), None, {}, {})
    refreshed = threading.Event()

    def fake_fresh():
        refreshed.set()
        return fresh

    monkeypatch.setattr(pending_completion, "_get_pending_indexer_context_fresh", fake_fresh)
    monkeypatch.setattr(pending_completion, "_INDEXER_CTX_CACHE", stale)
    monkeypatch.setattr(pending_completion, "_INDEXER_CTX_CACHE_TS", time.monotonic() - 3600)
    monkeypatch.setattr(pending_completion, "_INDEXER_CTX_REFRESH_RUNNING", False)

    assert pending_completion._get_pending_indexer_context() is stale
    assert refreshed.wait(5)
    deadline = time.monotonic() + 5
    while pending_completion._INDEXER_CTX_CACHE is not fresh and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pending_completion._get_pending_indexer_context() is fresh

    pending_completion.invalidate_pending_indexer_context()
    assert pending_completion._INDEXER_CTX_CACHE is None


def test_season_markers_and_homogeneous_book_folders() -> None:
    # queue-backend-20
    assert pending_rules._SEASON_MARKER_RE.search("Show.S2023.1080p.WEB-DL")
    node = {"name": "Collection"}
    children = [{"name": "a.mp3"}, {"name": "b.mp3"}]
    assert pending_rules._decide_child_promoted_category(node, "misc", children, ["music", "music"]) == "music"
    tree = {"name": "Collection", "children": [{"name": "a.m4b", "category": "audiobooks"}, {"name": "b.m4b", "category": "audiobooks"}]}
    pending_rules._inherit_category_from_children(tree)
    assert tree["category"] == "audiobooks"


def test_pending_watchdog_ignores_created_files_and_debounces() -> None:
    # queue-backend-21
    from watchdog.events import DirModifiedEvent, FileCreatedEvent, FileDeletedEvent, FileMovedEvent

    from logic.pending.index import PendingIndexManager, _PendingIndexEventHandler

    manager = PendingIndexManager(watchdog_debounce_s=60)
    handler = _PendingIndexEventHandler(manager)

    handler.on_any_event(FileCreatedEvent("/data/new.mkv"))
    handler.on_any_event(DirModifiedEvent("/data"))
    handler.on_any_event(FileDeletedEvent("/data/.hidden/x"))
    assert manager._dirty_event.is_set() is False

    handler.on_any_event(FileMovedEvent("/data/a.mkv", "/data/b.mkv"))
    assert manager._dirty_event.is_set() is True
    assert manager._pending_reason == "watchdog"

    manager._dirty_event.clear()
    handler.on_any_event(FileDeletedEvent("/data/b.mkv"))
    assert manager._dirty_event.is_set() is False  # debounced


def test_anime_lookup_runs_outside_the_cache_lock(monkeypatch) -> None:
    # queue-backend-23
    from logic.classify import anime as anime_cache

    lock_states: list[bool] = []

    def fake_query(_title, *, release_year=None):
        lock_states.append(anime_cache._lock.locked())
        return True

    monkeypatch.setattr(anime_cache, "_query_jikan", fake_query)
    monkeypatch.setattr(anime_cache, "_load_cache", lambda: None)
    monkeypatch.setattr(anime_cache, "_save_cache", lambda: None)
    monkeypatch.setattr(anime_cache, "_cache", {})

    assert anime_cache.is_anime("Frieren S01E01 1080p") is True
    assert anime_cache.check_titles_batch(["Some Other Show 1080p"]) == {"Some Other Show 1080p": True}
    assert lock_states == [False, False]
