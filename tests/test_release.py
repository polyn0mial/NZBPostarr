# ruff: noqa: F403,F405

"""Public-release and self-update tests."""

from tests.support import *

release = load_repo_script("release")


def test_revision_probe_is_public_without_broadening_other_system_routes() -> None:
    assert "/api/system/revision" in auth_api._AUTH_PUBLIC_PATHS
    assert "/api/system/update/status" not in auth_api._AUTH_PUBLIC_PATHS
    assert not any("/api/system/update/status".startswith(prefix) for prefix in auth_api._AUTH_PUBLIC_PREFIXES)


def test_public_release_tree_has_no_private_runtime_files() -> None:
    assert release.find_violations() == []


def test_source_layout_stays_flat() -> None:
    assert not (REPO_ROOT / "nzbpostarr").exists()
    for expected in ("app.py", "main.py", "api", "cli", "core", "logic", "indexers", "webui"):
        assert (REPO_ROOT / expected).exists()


def test_updater_recognizes_flat_release_archives(tmp_path) -> None:
    project = tmp_path / "nzbpostarr-v9.5.0"
    project.mkdir()
    (project / "app.py").write_text("", encoding="utf-8")
    (project / "version.py").write_text('__version__ = "9.5.0"\n', encoding="utf-8")

    assert updater._locate_project_root(tmp_path) == project
    assert updater._read_version_from_tree(project) == "9.5.0"


def test_updater_version_compare_and_restart_behavior(monkeypatch) -> None:
    assert updater._is_newer_version("1.0.0", "1.0.0rc1") is True
    assert updater._is_newer_version("1.0.0.post1", "1.0.0") is True
    assert updater._is_newer_version("v2.0.0", "1.9.9") is True
    assert updater._is_newer_version("1.0.0rc1", "1.0.0") is False

    class ImmediateThread:
        def __init__(self, target=None, daemon=None, name=None):
            self._target = target

        def start(self) -> None:
            if self._target:
                self._target()

    monkeypatch.setattr(updater.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)

    restart_cases = [
        ("spawn-fails", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("spawn failed")), [], 1),
        ("spawn-succeeds", lambda *_args, **_kwargs: object(), [0], 1),
    ]

    for case_name, fake_popen, expected_exit_codes, expected_spawn_calls in restart_cases:
        exit_codes: list[int] = []
        spawn_calls: list[tuple] = []

        def wrapped_popen(*args, **kwargs):
            spawn_calls.append((args, kwargs))
            return fake_popen(*args, **kwargs)

        monkeypatch.setattr(updater.subprocess, "Popen", wrapped_popen)
        monkeypatch.setattr(updater.os, "_exit", lambda code: exit_codes.append(code))

        updater.schedule_restart(delay_seconds=0)

        assert exit_codes == expected_exit_codes, case_name
        assert len(spawn_calls) == expected_spawn_calls, case_name
