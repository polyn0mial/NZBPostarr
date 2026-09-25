# ruff: noqa: F403,F405

"""NZBPostarr system tests."""

from tests.support import *

def test_stats_engine_parsing_and_formatting() -> None:
    """Verify speed parsing and duration formatting edge cases."""
    assert parse_speed_to_bps("1 GiB/s") == 1024**3
    assert parse_speed_to_bps("1 MiB/s") == 1024**2
    assert parse_speed_to_bps("1 KiB/s") == 1024

    assert parse_speed_to_bps("1 GB/s") == 10**9
    assert parse_speed_to_bps("1 MB/s") == 10**6
    assert parse_speed_to_bps("1 KB/s") == 1000

    assert parse_speed_to_bps("500 mib/s") == 500 * 1024**2
    assert parse_speed_to_bps("10MB/s") == 10 * 10**6

    assert parse_speed_to_bps("") == 0.0
    assert parse_speed_to_bps("invalid") == 0.0

    assert format_seconds(30) == "30s"
    assert format_seconds(59) == "59s"
    assert "1 minute" in format_seconds(60)
    assert format_seconds(float("inf")) == "--"
    assert format_seconds(86400 * 400) == "--"
    assert format_seconds(0) == "0s"
    assert format_seconds(-10) == "0s"


def test_process_stats_collector_ranks_rows_and_honors_collapsed_sections(monkeypatch) -> None:
    from logic import process_stats

    class FakeProcess:
        def __init__(self, info):
            self.info = info

    rows = [
        FakeProcess(
            {
                "pid": 10,
                "name": "memory-heavy",
                "cpu_percent": 5.0,
                "memory_percent": 80.0,
                "status": "running",
            }
        ),
        FakeProcess(
            {
                "pid": 11,
                "name": "cpu-heavy",
                "cpu_percent": 90.0,
                "memory_percent": 10.0,
                "status": "sleeping",
            }
        ),
    ]
    monkeypatch.setattr(process_stats.psutil, "process_iter", lambda _attrs: rows)
    monkeypatch.setattr(process_stats, "read_tcp_connection_count", lambda: 3)

    collector = process_stats.ProcessStatsCollector()
    network_speed = {"connections": 0.0}
    snapshot = collector.collect(
        ui_mode="active",
        last_ui_ping=0,
        collapsed_sections={"network", "disk"},
        network_speed=network_speed,
    )

    assert snapshot is not None
    assert snapshot["cpu"][0]["name"] == "cpu-heavy"
    assert snapshot["memory"][0]["name"] == "memory-heavy"
    assert snapshot["network"] == []
    assert snapshot["disk"] == []
    assert snapshot["total"] == 2
    assert network_speed["connections"] == 3


def test_process_stats_collector_skips_idle_quiet_ui(monkeypatch) -> None:
    from logic import process_stats

    monkeypatch.setattr(
        process_stats.psutil,
        "process_iter",
        lambda _attrs: (_ for _ in ()).throw(AssertionError("process scan should be skipped")),
    )
    collector = process_stats.ProcessStatsCollector()

    assert (
        collector.collect(
            ui_mode="quiet",
            last_ui_ping=0,
            collapsed_sections=set(),
            network_speed={},
        )
        is None
    )


def test_headless_stats_command_outputs_json_without_starting_collector(monkeypatch, capsys) -> None:
    from logic import services, stats_engine

    sample = {
        "hostname": "test-host",
        "platform": "TestOS",
        "uptime_seconds": 123,
        "cpu": {"percent": 12.5},
        "memory": {"used_gb": 4.0, "total_gb": 8.0, "percent": 50.0},
        "disk": {"used_gb": 100.0, "total_gb": 200.0, "free_gb": 100.0, "percent": 50.0},
        "network": {
            "upload_mbps": 1.25,
            "download_mbps": 2.5,
            "connections": 7,
            "errors_in": 0,
            "errors_out": 0,
            "drops_in": 0,
            "drops_out": 0,
        },
    }

    monkeypatch.setattr(services, "init_app", lambda: None)
    monkeypatch.setattr(stats_engine, "collect_instant_system_info", lambda interval_seconds=0.25: sample)

    def fail_start_collector(*_args, **_kwargs):
        raise AssertionError("headless stats should not start the background collector")

    monkeypatch.setattr(stats_engine, "start_collector", fail_start_collector)

    rc = cli_run.run_headless(["stats", "--json", "--sample-seconds", "0.05"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hostname"] == "test-host"
    assert payload["network"]["connections"] == 7

def test_headless_stats_watch_stops_cleanly(monkeypatch, capsys) -> None:
    from logic import stats_engine

    monkeypatch.setattr(
        stats_engine,
        "collect_instant_system_info",
        lambda interval_seconds=0.25: {"hostname": "test-host", "platform": "TestOS", "uptime_seconds": 0},
    )
    monkeypatch.setattr(cli_stats, "_render_stats_summary", lambda _info: "stats snapshot")

    def stop_immediately(_seconds: float) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_stats.time, "sleep", stop_immediately)

    rc = cli_stats.cmd_stats(
        SimpleNamespace(
            json=False,
            watch=True,
            interval=2.0,
            sample_seconds=0.05,
        )
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "stats snapshot" in output
    assert "Stopped." in output

def test_arm_process_reaper_schedules_boot_scan_in_background(monkeypatch) -> None:
    from logic import process_reaper

    calls: list[str] = []

    class DummyTask:
        def done(self) -> bool:
            return True

        def cancel(self) -> None:
            return None

    def fake_create_task(coro, name=None):
        try:
            captured_name = name
            captured_code = coro.cr_code.co_name
        finally:
            coro.close()
        calls.append(f"task:{captured_name}:{captured_code}")
        return DummyTask()

    monkeypatch.setattr(process_reaper, "schedule_reaper", lambda: calls.append("schedule_reaper"))
    monkeypatch.setattr(process_reaper, "schedule_wal_checkpoint", lambda: calls.append("schedule_wal_checkpoint"))
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(app_mod, "_boot_reaper_task", None)

    app_mod._arm_process_reaper()

    assert calls == [
        "schedule_reaper",
        "schedule_wal_checkpoint",
        "task:startup-process-reaper:_run_startup_reaper",
    ]

def test_stats_recording() -> None:
    """Test that recording system stats works correctly."""
    init_database()

    # Record a mock stat
    record_system_stats(
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
        errors_in=1,
        errors_out=2,
        drops_in=3,
        drops_out=4,
    )

    # Retrieve via SQLAlchemy
    history = get_system_stats_history(limit=5)
    assert len(history["cpu"]) > 0
    assert history["cpu"][-1] == 10.0

def test_console_buffer_operations() -> None:
    """Verify basic console buffering plus queue-update suppression."""
    console.clear()
    console.log("Test INFO message", "INFO")
    console.log("Test DEBUG message", "DEBUG")
    console.log("Test ERROR message", "ERROR")
    console.log("Test SUCCESS message", "SUCCESS")
    logs, _ = console.get_logs()
    assert len(logs) == 4
    assert all(k in logs[0] for k in ["ts", "level", "msg"])
    buffer = ConsoleBuffer()
    original_conf = getattr(config_mod, "_GLOBAL_CONFIG", None)

    try:
        config_mod._GLOBAL_CONFIG = Config.model_construct(verbose=False)
        buffer.sink(
            SimpleNamespace(
                record={
                    "level": SimpleNamespace(name="INFO", no=20),
                    "message": "[Queue Update] Found 6 Red Indexers. Total pending tasks set to 2717.",
                }
            )
        )
        buffer.sink(
            SimpleNamespace(
                record={
                    "level": SimpleNamespace(name="INFO", no=20),
                    "message": "Useful info message",
                }
            )
        )

        logs, _ = buffer.get_logs()
        assert [entry["msg"] for entry in logs] == ["Useful info message"]
    finally:
        config_mod._GLOBAL_CONFIG = original_conf

def test_oversized_folder_still_processes_inner_files(tmp_path, monkeypatch):
    from unittest.mock import patch

    from logic import processing
    from logic.pending.roots import PendingScanItem

    monkeypatch.setenv("NZBPOSTARR_VALIDATE_ISOLATE", "0")
    monkeypatch.setattr(
        registry_mod,
        "get_enabled_indexers",
        lambda _conf: [
            SimpleNamespace(id="geek", name="NZBGeek", enabled=True, backfill=False, priority=False)
        ],
    )

    movies_root = tmp_path / "movies"
    big_folder = movies_root / "BigFolder"
    big_folder.mkdir(parents=True)
    inner = big_folder / "inner.mkv"
    inner.write_bytes(b"x" * 10)

    processed = []

    def fake_process_single(path, **_kwargs):
        processed.append(path)
        return 0

    ONE_GIB = 1024**3

    def fake_compute_size(p):
        # Mark only the folder as oversized; everything else is tiny.
        if p.name == "BigFolder":
            return 2 * ONE_GIB
        return 123

    fake_scan_items = [
        PendingScanItem(
            category="movies",
            configured_category="movies",
            folder=movies_root,
            path=big_folder,
            name=big_folder.name,
            rel_key=big_folder.name,
            is_dir=True,
        )
    ]

    with patch.object(processing, "check_tools", return_value=True):
        with patch.object(
            processing,
            "get_config",
            return_value=_make_processing_conf(
                movies_root,
                folder_size_limit_gb=1,
                tv_folder=tmp_path / "tv",
                misc_folder=tmp_path / "misc",
            ),
        ):
            with patch.object(processing, "scan_configured_items", return_value=fake_scan_items):
                with patch.object(processing, "process_single", side_effect=fake_process_single):
                    with patch.object(
                        processing,
                        "_live_size_bytes",
                        side_effect=lambda path: int(fake_compute_size(path)),
                    ):
                        processing.run_job("movies")

    assert inner in processed
    assert big_folder not in processed

def test_check_tools_honors_configured_commands(tmp_path) -> None:
    from types import SimpleNamespace

    from logic import processing

    rar = tmp_path / "rar.exe"
    parpar = tmp_path / "parpar.exe"
    nyuu = tmp_path / "nyuu.exe"
    for tool in (rar, parpar, nyuu):
        tool.write_text("", encoding="utf-8")

    conf = SimpleNamespace(rar_path=str(rar), parpar_path=str(parpar), nyuu_path=str(nyuu))

    assert processing.check_tools(conf) is True

def test_run_command_handles_carriage_return_progress() -> None:
    from core.utils import run_command

    parsed: list[str] = []
    script = (
        "import sys,time;"
        "sys.stdout.write('10%\\r');sys.stdout.flush();"
        "time.sleep(0.05);"
        "sys.stdout.write('20%\\nDone\\n');sys.stdout.flush()"
    )

    success, output = run_command(
        [sys.executable, "-c", script],
        "test-progress",
        parser=lambda line: parsed.append(line) or line,
        quiet=True,
    )

    assert success is True
    assert list(output) == ["10%", "20%", "Done"]
    assert parsed == ["10%", "20%", "Done"]

def test_runtime_checkpoint_keeps_uploading_item_when_prefetch_finishes(tmp_path) -> None:
    from logic import processing

    uploading_path = tmp_path / "Uploading.Movie.mkv"
    prefetched_path = tmp_path / "Prefetched.Movie.mkv"
    persisted: list[bool] = []
    job = {
        "_current_item_path": str(uploading_path),
        "_inflight_item_paths": [str(uploading_path), str(prefetched_path)],
        "_persist_callback": lambda: persisted.append(True),
    }

    processing._complete_runtime_item_checkpoint(job, prefetched_path)

    assert job["_current_item_path"] == str(uploading_path)
    assert job["_inflight_item_paths"] == [str(uploading_path)]
    assert persisted == [True]

def test_generate_mediainfo_uses_one_cli_pass(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    source = tmp_path / "Movie.Name.2026.mkv"
    source.write_bytes(b"video")
    output_dir = tmp_path / "mediainfo"
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs["timeout"] == 120
        return SimpleNamespace(returncode=0, stdout=f"Complete name : {source}\nFormat : Matroska\n")

    conf = SimpleNamespace(mediainfo_sub=output_dir, base_folder=tmp_path)
    monkeypatch.setattr(processing.subprocess, "run", fake_run)

    result = processing.generate_mediainfo(source, conf=conf)

    assert result == output_dir / f"{source.name}.mediainfo.nfo"
    assert result.read_text(encoding="utf-8")
    assert calls == [["mediainfo", "--Full", str(source)]]

def test_generate_mediainfo_reuses_current_sidecar(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    source = tmp_path / "Movie.Name.2026.mkv"
    source.write_bytes(b"video")
    output_dir = tmp_path / "mediainfo"
    output_dir.mkdir()
    existing = output_dir / f"{source.name}.mediainfo.nfo"
    existing.write_text("General\nFormat : Matroska\n", encoding="utf-8")
    conf = SimpleNamespace(mediainfo_sub=output_dir, base_folder=tmp_path)
    monkeypatch.setattr(
        processing.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("current sidecar should be reused")),
    )

    result = processing.generate_mediainfo(source, conf=conf)

    assert result == existing

def test_error_handlers_and_stats_flags(monkeypatch) -> None:
    not_found_cases = [
        (
            "api-404-json",
            "/api/does-not-exist",
            "Missing API endpoint",
            404,
            "application/json",
            {"status": "error", "detail": "Missing API endpoint"},
        ),
        (
            "page-404-html",
            "/missing-page",
            "Missing page",
            404,
            "text/html",
            None,
        ),
    ]

    for case_name, path, detail, status_code, content_type_prefix, expected_json in not_found_cases:
        response = _run_async(
            pages_api.not_found_exception_handler(
                _make_request(path),
                HTTPException(status_code=404, detail=detail),
            )
        )
        assert response.status_code == status_code, case_name
        assert response.headers["content-type"].startswith(content_type_prefix), case_name
        if expected_json is not None:
            assert isinstance(response, JSONResponse), case_name
            assert json.loads(response.body) == expected_json, case_name

    response = _run_async(
        pages_api.server_error_exception_handler(
            _make_request("/api/uploads/jobs"),
            RuntimeError("boom"),
        )
    )
    assert isinstance(response, JSONResponse)
    assert response.status_code == 500
    assert json.loads(response.body) == {
        "status": "error",
        "detail": "Internal server error",
    }

    stats_flag_cases = [
        ("stats-page-disabled", False, True, True, True),
        ("all-stats-disabled", False, False, False, False),
    ]
    for case_name, stats_page_enabled, dashboard_stats_enabled, expect_collector, expect_history in stats_flag_cases:
        patch_hit(
            monkeypatch,
            deps_api,
            "get_config",
            lambda stats_page_enabled=stats_page_enabled,
            dashboard_stats_enabled=dashboard_stats_enabled: SimpleNamespace(
                stats_page_enabled=stats_page_enabled,
                dashboard_stats_enabled=dashboard_stats_enabled,
                dashboard_stats_modules=["cpu", "memory"],
            ),
        )
        assert deps_api._stats_collector_required() is expect_collector, case_name
        assert deps_api._stats_history_enabled() is expect_history, case_name

    patch_hit(
        monkeypatch,
        deps_api,
        "get_config",
        lambda: SimpleNamespace(
            stats_page_enabled=False,
            dashboard_stats_enabled=True,
            dashboard_stats_modules=["cpu"],
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        _run_async(pages_api._page_route(pages_api.PAGES["stats"])(_make_request("/stats")))
    assert exc_info.value.status_code == 404
    assert "disabled" in str(exc_info.value.detail).lower()

def test_scan_item_support_assets_prefers_largest_video_and_primary_nfo(tmp_path) -> None:
    import logic.processing as processing

    release_dir = tmp_path / "Release.Dir"
    large_video = _touch(release_dir / "CD1" / "movie.part01.mkv", b"b" * 25)
    _touch(release_dir / "sample.mkv", b"a" * 5)
    nfo_file = _touch(release_dir / "release.nfo", "release info")

    scan = processing._scan_item_support_assets(release_dir)

    assert scan.nfo_path == nfo_file
    assert scan.mediainfo_source_path == large_video

def test_start_watchdog_observer_starts_only_existing_directories(tmp_path) -> None:
    from core import utils as utils_mod

    watched = tmp_path / "watched"
    watched.mkdir()
    missing = tmp_path / "missing"

    class _FakeObserver:
        def __init__(self) -> None:
            self.scheduled: list[tuple[str, bool]] = []
            self.started = False
            self.stopped = False
            self.joined = False

        def schedule(self, _handler, path: str, recursive: bool) -> None:
            self.scheduled.append((path, recursive))

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def join(self, timeout: float = 5.0) -> None:
            _ = timeout
            self.joined = True

    observer, scheduled = utils_mod.start_watchdog_observer(
        [(object(), watched, True), (object(), missing, True)],
        observer_factory=_FakeObserver,
    )

    assert observer is not None
    assert scheduled == 1
    assert observer.started is True
    assert observer.scheduled == [(str(watched), True)]

    utils_mod.stop_watchdog_observer(observer)

    assert observer.stopped is True
    assert observer.joined is True

def test_dynamic_cache_bust_updates_from_watchdog_events(tmp_path, monkeypatch) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    asset_file = assets_dir / "app.js"
    asset_file.write_text("console.log('a');", encoding="utf-8")

    class _FakeObserver:
        def __init__(self) -> None:
            self.handler = None
            self.started = False
            self.stopped = False

        def schedule(self, handler, _path: str, recursive: bool) -> None:
            _ = recursive
            self.handler = handler

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def join(self, timeout: float = 5.0) -> None:
            _ = timeout

    monkeypatch.setattr(assets_api, "ASSETS_DIR", assets_dir)
    monkeypatch.setattr(assets_api, "Observer", _FakeObserver)

    token = assets_api._DynamicCacheBust(ttl_seconds=0.1, reconcile_interval_s=60.0)
    token.start()
    initial = str(token)

    observer = token._observer
    assert observer is not None
    assert observer.started is True
    assert observer.handler is not None

    asset_file.write_text("console.log('b');", encoding="utf-8")
    observer.handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(asset_file)))

    monkeypatch.setattr(token, "_compute_value", lambda: (_ for _ in ()).throw(AssertionError("unexpected rescan")))

    assert str(token) != initial

    token.stop()
    assert observer.stopped is True

def test_update_settings_reports_partial_success_when_monitor_restart_fails(monkeypatch) -> None:
    monkeypatch.setattr(config_mod, "save_config", lambda _updates: True)

    async def _boom() -> None:
        raise RuntimeError("restart failed")

    monkeypatch.setattr(autoupload, "restart_folder_monitor", _boom)

    result = _run_async(settings_api.update_settings("folders", {"base_folder": "X:/"}))

    assert result["status"] == "partial_success"
    assert result["monitor_restart"]["attempted"] is True
    assert result["monitor_restart"]["ok"] is False
    assert "restart failed" in result["monitor_restart"]["error"]

def test_update_settings_reports_monitor_restart_success(monkeypatch) -> None:
    monkeypatch.setattr(config_mod, "save_config", lambda _updates: True)

    async def _ok() -> None:
        return None

    monkeypatch.setattr(autoupload, "restart_folder_monitor", _ok)

    result = _run_async(settings_api.update_settings("folders", {"base_folder": "X:/"}))

    assert result["status"] == "success"
    assert result["monitor_restart"] == {"attempted": True, "ok": True}

def test_reset_settings_route_uses_bundled_defaults_path(monkeypatch, tmp_path) -> None:
    defaults_path = tmp_path / "config.defaults.yaml"
    defaults_path.write_text("reset: true\n", encoding="utf-8")
    config_path = tmp_path / ".config" / "nzbpostarr" / "config.yaml"
    observed: dict[str, object] = {}

    monkeypatch.setattr(config_mod, "get_defaults_config_path", lambda: defaults_path)
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_path)
    monkeypatch.setattr(config_mod, "load_config", lambda: SimpleNamespace(ok=True))
    monkeypatch.setattr(config_mod, "_GLOBAL_CONFIG", None)
    monkeypatch.setattr(config_mod, "_CONFIG_MTIME_NS", None)

    async def _sync(conf=None) -> None:
        observed["synced"] = conf

    patch_hit(monkeypatch, settings_api, "_sync_stats_collector_state", _sync)

    result = _run_async(settings_api.reset_settings_route())

    assert result["status"] == "success"
    assert config_path.parent.is_dir()
    assert config_path.read_text(encoding="utf-8") == "reset: true\n"
    assert observed["synced"].ok is True

def test_atomic_config_replace_keeps_last_known_good_backup(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / ".config" / "nzbpostarr" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("version: old\n", encoding="utf-8")
    loaded = SimpleNamespace(version="new")

    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_path)
    monkeypatch.setattr(config_mod, "load_config", lambda: loaded)
    monkeypatch.setattr(config_mod, "_GLOBAL_CONFIG", None)
    monkeypatch.setattr(config_mod, "_CONFIG_MTIME_NS", None)

    result = config_mod.replace_config_content("version: new\n")

    assert result is loaded
    assert config_path.read_text(encoding="utf-8") == "version: new\n"
    assert config_path.with_name("config.yaml.bak").read_text(encoding="utf-8") == "version: old\n"
    assert list(config_path.parent.glob("config.yaml.*.tmp")) == []

def test_get_raw_config_masks_credentials(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "base_folder: /srv/usenet\n"
        "nntp_servers:\n"
        "  - name: Primary\n"
        "    host: news.example.com\n"
        "    user: real_nntp_user\n"
        "    pass: real_nntp_password\n"
        "api_keys:\n"
        "  geek: real_geek_key\n"
        "usernames:\n"
        "  omg: real_omg_user\n"
        "web_password: real_web_password\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_path)

    result = _run_async(settings_api.get_raw_config())
    content = result["content"]

    assert result["masked"] is True
    # The mask must render as bullets, not an escaped • sequence, or the
    # raw editor shows gibberish where credentials used to be.
    assert redaction.SECRET_MASK in content
    assert "\\u2022" not in content
    for secret in (
        "real_nntp_user",
        "real_nntp_password",
        "real_geek_key",
        "real_omg_user",
        "real_web_password",
    ):
        assert secret not in content, secret
    # Non-secret values must survive so the editor stays usable.
    assert "news.example.com" in content
    assert "/srv/usenet" in content

def test_save_raw_config_restores_untouched_masks(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("placeholder: true\n", encoding="utf-8")
    conf = SimpleNamespace(
        web_password="",
        api_keys={"geek": "real_geek_key"},
        usernames={"omg": "real_omg_user"},
        nntp_servers=[
            SimpleNamespace(name="Primary", user="real_nntp_user", password="real_nntp_password")
        ],
    )
    written: dict[str, str] = {}

    def _replace(content: str) -> SimpleNamespace:
        written["content"] = content
        return SimpleNamespace(ok=True)

    patch_hit(monkeypatch, settings_api, "get_config", lambda: conf)
    monkeypatch.setattr(config_mod, "replace_config_content", _replace)

    async def _sync(new_conf=None) -> None:
        return None

    patch_hit(monkeypatch, settings_api, "_sync_stats_collector_state", _sync)

    submitted = (
        "base_folder: /srv/usenet\n"
        "nntp_servers:\n"
        "  - name: Primary\n"
        "    host: news.example.com\n"
        f"    user: {redaction.SECRET_MASK}\n"
        f"    pass: {redaction.SECRET_MASK}\n"
        "api_keys:\n"
        f"  geek: {redaction.SECRET_MASK}\n"
        "usernames:\n"
        f"  omg: {redaction.SECRET_MASK}\n"
    )
    result = _run_async(settings_api.save_raw_config({"content": submitted}))

    assert result["status"] == "success"
    saved = written["content"]
    assert redaction.SECRET_MASK not in saved
    for secret in ("real_nntp_user", "real_nntp_password", "real_geek_key", "real_omg_user"):
        assert secret in saved, secret
    assert "news.example.com" in saved

def test_save_raw_config_rejects_invalid_yaml(monkeypatch) -> None:
    patch_hit(monkeypatch, settings_api, "get_config", lambda: SimpleNamespace(web_password=""))

    with pytest.raises(HTTPException) as exc_info:
        _run_async(settings_api.save_raw_config({"content": "key: [unclosed\n"}))

    assert exc_info.value.status_code == 400

def test_reset_settings_route_returns_404_when_defaults_missing(monkeypatch, tmp_path) -> None:
    missing_defaults = tmp_path / "missing.defaults.yaml"
    config_path = tmp_path / "config.yaml"

    monkeypatch.setattr(config_mod, "get_defaults_config_path", lambda: missing_defaults)
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_path)

    with pytest.raises(HTTPException) as exc_info:
        _run_async(settings_api.reset_settings_route())

    assert exc_info.value.status_code == 404

def test_get_current_settings_preserves_folder_path_categories(monkeypatch) -> None:
    conf = SimpleNamespace(
        enable_backfill=False,
        enable_duplicate_bypass=False,
        verbose=False,
        enable_duplicate_checking=True,
        enable_anime_checking=False,
        item_limit_per_category=None,
        folder_size_limit_gb=99,
        folder_size_limit_enabled=True,
        file_size_limit_gb=0,
        file_size_limit_enabled=True,
        dynamic_packs=True,
        poster_name="Poster",
        poster_email="poster@example.com",
        rar_size="100m",
        article_size="1M",
        include_readme=True,
        upload_max_retries=3,
        upload_retry_delay_seconds=5,
        alt_bins=[],
        base_folder=Path("D:/base"),
        nntp_servers=[],
        api_keys={},
        usernames={},
        dashboard_stats_enabled=True,
        ui_refresh_seconds=2,
        dashboard_stats_modules=[],
        stats_page_enabled=True,
        skip_files={"enabled": False, "display_mode": "disabled", "patterns": []},
        enable_password=False,
        web_username="admin",
        get_folder_path_entries=lambda: [
            {"path": "D:/watch/external-a", "category": "auto", "monitor": False},
            {"path": "D:/watch/movies", "category": "auto", "monitor": True},
        ],
    )

    patch_hit(monkeypatch, settings_api, "get_config", lambda: conf)
    monkeypatch.setattr("core.indexers.registry.get_all_indexers", lambda: [])
    monkeypatch.setattr("core.indexers.categories.get_available_categories", lambda: [])

    result = _run_async(settings_api.get_current_settings())

    assert result["folders"]["folder_paths"] == [
        {"path": "D:/watch/external-a", "category": "auto", "monitor": False},
        {"path": "D:/watch/movies", "category": "auto", "monitor": True},
    ]
    # Fallbacks for configs that predate these keys (app-routes-01/03/04).
    assert result["processing"]["tv_pack_ignore"] == {
        "enabled": True,
        "ignore_non_episode": True,
        "require_episode": True,
        "require_resolution": False,
        "require_source": True,
    }
    assert result["folders"]["backup_folder"] == str(config_mod.APP_ROOT / "backups")
    assert result["ui"]["category_appearance_profiles"] == {}

def test_normalize_folder_paths_payload_preserves_explicit_categories() -> None:
    normalized = config_mod._normalize_folder_paths_payload(
        {
            "folder_paths": [
                {
                    "path": "/data/0--Movies",
                    "category": "movies",
                    "monitor": False,
                    "allow_bulk_selection": False,
                },
                {"path": "/data/0--TV2", "category": "tv", "monitor": True},
                {"path": "/data/0--Misc", "category": "misc", "monitor": False},
                {"path": "/data/qbittorrent", "monitor": False},
            ]
        }
    )

    assert normalized["folder_paths"] == [
        {
            "path": "/data/0--Movies",
            "category": "movies",
            "monitor": False,
            "allow_bulk_selection": False,
        },
        {
            "path": "/data/0--TV2",
            "category": "tv",
            "monitor": True,
            "allow_bulk_selection": True,
        },
        {
            "path": "/data/0--Misc",
            "category": "misc",
            "monitor": False,
            "allow_bulk_selection": True,
        },
        {
            "path": "/data/qbittorrent",
            "category": "auto",
            "monitor": False,
            "allow_bulk_selection": True,
        },
    ]
    assert normalized["movies_folder"] is None
    assert normalized["tv_folder"] is None
    assert normalized["misc_folder"] is None
    assert normalized["external_folders"] == [
        "/data/qbittorrent",
    ]

def test_ambiguous_dvd_folder_is_movie_across_detectors(tmp_path) -> None:
    release_dir = tmp_path / "Ozzy & Drix (2002-2003) 3xDVD9 NTSC-CultFilms"
    _touch(release_dir / "DISC_1" / "VIDEO_TS" / "VIDEO_TS.IFO", b"a")
    _touch(release_dir / "DISC_1" / "VIDEO_TS" / "VTS_01_1.VOB", b"b")

    assert classify_content.detect_content_itype(release_dir.name, release_dir, "") == "Movie"
    assert classify_content.detect_external_category(release_dir.name, release_dir) == "movies"
    assert classify_content.detect_auto_itype(release_dir) == "Movie"
    assert classify_content.detect_auto_category(release_dir) == "movies"

def test_detect_content_itype_prefers_video_over_audiobook_sidecars(tmp_path) -> None:
    release_dir = tmp_path / "Movie.Name.2026.1080p.WEB-DL"
    _touch(release_dir / "Movie.Name.2026.1080p.WEB-DL.mkv", b"a")
    _touch(release_dir / "Movie.Name.2026.1080p.WEB-DL-commentary.m4b", b"b")

    assert classify_content.detect_content_itype(release_dir.name, release_dir, "") == "Movie"
    assert classify_content.detect_auto_category(release_dir) == "movies"

def test_scan_configured_items_preserves_tv_episode_metadata(tmp_path) -> None:
    tv_dir = tmp_path / "tv"
    season = tv_dir / "Show.S01"
    season.mkdir(parents=True)
    first = season / "Show.S01E01.mkv"
    second = season / "Show.S01E02.mkv"
    first.write_bytes(b"x")
    second.write_bytes(b"y")

    conf = SimpleNamespace(folder_paths=[{"category": "tv", "path": str(tv_dir)}])

    items = pending_roots.scan_configured_items(conf, must_exist=True)

    assert len(items) == 1
    assert items[0].category == "tv"
    assert items[0].rel_key == "Show.S01"
    assert items[0].episode_rel_keys == (
        "Show.S01/Show.S01E01.mkv",
        "Show.S01/Show.S01E02.mkv",
    )

def test_log_pack_completion_state_uses_verbose_level(monkeypatch) -> None:
    from logic.pending import completion as pending_completion

    calls = []

    monkeypatch.setattr(
        pending_completion.logger,
        "log",
        lambda level, message: calls.append((level, message)),
    )

    pending_completion._log_pack_completion_state(
        {
            "name": "Show.Name.S01",
            "completed": False,
            "children": [
                {"name": "Show.Name.S01E01.mkv"},
                {"name": "cover.jpg", "auto_select_ignored": True},
            ],
        }
    )

    assert calls == [
        (
            "VERBOSE",
            "Pack completion status for 'Show.Name.S01': pending (1 valid files, 1 ignored)",
        )
    ]

def test_parse_nzb_structure_collects_files_and_segments(tmp_path) -> None:
    from logic.usenet_stream import parse_nzb_structure

    source = tmp_path / "sample.nzb"
    source.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<nzb xmlns=\"http://www.newzbin.com/DTD/2003/nzb\">
  <file poster=\"poster@example.com\" date=\"123\" subject=\"Example.Release yEnc\">
    <groups><group>alt.binaries.misc</group></groups>
    <segments>
      <segment bytes=\"100\" number=\"2\">part2@example</segment>
      <segment bytes=\"50\" number=\"1\">&lt;part1@example&gt;</segment>
    </segments>
  </file>
</nzb>
""",
        encoding="utf-8",
    )

    manifest = parse_nzb_structure(source)

    assert manifest["source_name"] == "sample.nzb"
    assert manifest["release_name"] == "sample"
    assert len(manifest["files"]) == 1
    file_entry = manifest["files"][0]
    assert file_entry["groups"] == ["alt.binaries.misc"]
    assert [segment["number"] for segment in file_entry["segments"]] == [1, 2]
    assert file_entry["segments"][0]["message_id"] == "<part1@example>"
    assert file_entry["segments"][1]["message_id"] == "<part2@example>"

def test_build_procjson_inputs_points_back_to_helper(tmp_path) -> None:
    from logic.usenet_stream import build_procjson_inputs

    manifest_path = tmp_path / "release.stream.json"
    manifest = {
        "files": [
            {"name": "one.bin", "size": 111},
            {"name": "two.bin", "size": 222},
        ]
    }

    inputs = build_procjson_inputs(manifest_path, manifest)

    assert len(inputs) == 2
    assert inputs[0].startswith("procjson://")
    first = json.loads(inputs[0][len("procjson://") :])
    assert first[0] == "one.bin"
    assert first[1] == 111
    assert "logic.usenet_stream" in first[2]

def test_resolve_source_nzb_paths_supports_file_and_directory(tmp_path) -> None:
    from logic.usenet_stream import resolve_source_nzb_paths

    single = tmp_path / "single.nzb"
    single.write_text("<nzb></nzb>", encoding="utf-8")

    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "a.nzb").write_text("<nzb></nzb>", encoding="utf-8")
    nested = batch_dir / "nested"
    nested.mkdir()
    (nested / "b.nzb").write_text("<nzb></nzb>", encoding="utf-8")
    hidden = batch_dir / ".hidden"
    hidden.mkdir()
    (hidden / "skip.nzb").write_text("<nzb></nzb>", encoding="utf-8")

    assert resolve_source_nzb_paths(single) == [single.resolve()]
    assert resolve_source_nzb_paths(batch_dir) == [
        (batch_dir / "a.nzb").resolve(),
        (nested / "b.nzb").resolve(),
    ]

def test_resolve_force_flag(monkeypatch) -> None:
    from logic.services import UploadService

    cases = [
        ("skip-dupe-check-forces-upload", True, {"enable_duplicate_check": False}, True),
        ("dupe-check-keeps-normal-flow", True, {"enable_duplicate_check": True}, False),
        ("missing-flag-defaults-false", True, {"enable_duplicate_check": None}, False),
        ("explicit-force-true-wins", True, {"force": True, "enable_duplicate_check": True}, True),
        ("explicit-force-false-wins", True, {"force": False, "enable_duplicate_check": False}, False),
        ("test-mode-always-forces", True, {"enable_duplicate_check": True, "test_mode": True}, True),
        ("global-disable-forces-upload", False, {"enable_duplicate_check": True}, True),
    ]

    for case_name, global_duplicate_checking_enabled, kwargs, expected in cases:
        _set_duplicate_checking(monkeypatch, enabled=global_duplicate_checking_enabled)
        assert UploadService._resolve_force_flag(**kwargs) is expected, case_name

def test_check_success_word_boundary() -> None:
    from core.indexers.http_submit import _check_success

    cases = [
        ("short-pattern-standalone-match", ["OK"], "OK", True),
        ("short-pattern-inside-word-does-not-match", ["OK"], "TOKEN EXPIRED", False),
        ("short-pattern-inside-broken-does-not-match", ["OK"], "BROKEN UPLOAD", False),
        ("short-pattern-matches-with-punctuation", ["OK"], "Status: OK.", True),
        ("long-pattern-uses-substring-match", ["SUCCESS"], "upload:SUCCESSFUL response", True),
    ]

    for case_name, text_patterns, response_text, expected_ok in cases:
        idx = _make_success_indexer(text_patterns)
        ok, _ = _check_success(idx, _fake_success_response(response_text))
        assert ok is expected_ok, case_name

def test_should_skip_completed_item_respects_force(monkeypatch) -> None:
    from logic.processing import _should_skip_completed_item

    monkeypatch.setattr(
        "core.indexers.models.resolve_indexer_enabled",
        lambda _idx, _conf: True,
    )

    cases = [
        ("force-true-never-skips", ("omg",), {"omg": "2026-01-01T00:00:00"}, True, False),
        ("all-done-skips", ("omg",), {"omg": "2026-01-01T00:00:00"}, False, True),
        ("partial-done-keeps-running", ("omg", "geek"), {"omg": "2026-01-01T00:00:00", "geek": None}, False, False),
    ]

    for case_name, indexer_ids, dest_status, force, expected in cases:
        indexers = [_make_force_test_indexer(idx_id) for idx_id in indexer_ids]
        assert _should_skip_completed_item(indexers, None, dest_status, force=force, name="test") is expected, case_name

def test_plan_upload_runs_respects_force() -> None:
    from logic.processing import _plan_upload_runs

    upload_sets = [{"id": "omg", "dests": ["omg"], "backbone": "NetNews", "priority": False}]
    indexer_map = {"omg": _make_force_test_indexer_map("omg")}
    server = _make_force_test_server()

    cases = [
        ("force-true-includes-done-destination", {"omg": "2026-01-01T00:00:00"}, True, False, 1),
        ("force-false-skips-done-destination", {"omg": "2026-01-01T00:00:00"}, False, False, 0),
        ("new-item-still-runs", {"omg": None}, False, True, 1),
    ]

    for case_name, dest_status, force, is_new, expected_run_count in cases:
        runs = _plan_upload_runs(
            upload_sets,
            all_servers=[server],
            dest_status=dest_status,
            indexer_map=indexer_map,
            force=force,
            is_new=is_new,
            global_backfill=False,
        )

        assert len(runs) == expected_run_count, case_name
        if expected_run_count:
            assert "omg" in runs[0][0]["dests"], case_name
