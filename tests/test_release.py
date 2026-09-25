"""Public-release and self-update tests."""

from api import auth as auth_api
from logic.system import lifecycle, updater

from tests.conftest import REPO_ROOT, load_repo_script

release = load_repo_script("release")


def test_revision_probe_is_public_without_broadening_other_system_routes() -> None:
    assert "/api/system/revision" in auth_api._AUTH_PUBLIC_PATHS
    assert "/api/system/update/status" not in auth_api._AUTH_PUBLIC_PATHS
    assert not any("/api/system/update/status".startswith(prefix) for prefix in auth_api._AUTH_PUBLIC_PREFIXES)


def test_public_release_tree_has_no_private_runtime_files() -> None:
    assert release.find_violations() == []


def test_removed_paths_list_only_names_files_gone_from_the_tree() -> None:
    assert release.removed_path_violations() == []


def test_removed_paths_check_rejects_live_and_escaping_entries(tmp_path) -> None:
    listing = tmp_path / "removed_paths.txt"
    listing.write_text("# header\n\napp.py\n../outside.py\n/abs.py\nlogic/gone.py\n", encoding="utf-8")

    violations = release.removed_path_violations(listing)

    assert len(violations) == 3
    assert "still exists: app.py" in violations[0]
    assert all("logic/gone.py" not in violation for violation in violations)


def test_release_manifest_lists_archive_files(tmp_path) -> None:
    import json
    import zipfile

    archive = tmp_path / "rel.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("p/app.py", "")
        zf.writestr("p/core/", "")
        zf.writestr("p/core/fs.py", "")
    release._append_release_manifest(archive, "p/")

    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read(f"p/{release.RELEASE_MANIFEST_NAME}"))
    assert manifest == {"files": ["app.py", "core/fs.py"]}


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

    monkeypatch.setattr(lifecycle.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _seconds: None)

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

        monkeypatch.setattr(lifecycle.subprocess, "Popen", wrapped_popen)
        monkeypatch.setattr(lifecycle.os, "_exit", lambda code: exit_codes.append(code))

        lifecycle.schedule_restart(delay_seconds=0)

        assert exit_codes == expected_exit_codes, case_name
        assert len(spawn_calls) == expected_spawn_calls, case_name
