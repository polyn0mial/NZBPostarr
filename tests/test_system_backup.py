"""Backups, release-manifest deletion and the startup removal list (logic/system)."""

import json
import os
import zipfile
from pathlib import Path

from logic.system import backup as system_backup
from logic.system import updater


def _point_at(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(system_backup, "APP_ROOT", root)
    monkeypatch.setattr(system_backup, "BACKUP_DIR", root / "data" / "updater" / "backups")
    monkeypatch.setattr(updater, "APP_ROOT", root)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_snapshot_members_skip_protected_and_archive_is_owner_only(monkeypatch, tmp_path) -> None:
    root = tmp_path / "app"
    _write(root / "app.py")
    _write(root / "logic" / "mod.py")
    _write(root / "data" / "history" / "usenet_uploads.db")
    _write(root / ".config" / "nzbpostarr" / "config.yaml")
    _write(root / "logic" / "__pycache__" / "mod.cpython-312.pyc")
    _point_at(monkeypatch, root)

    meta = system_backup._create_backup_snapshot(target_version="9.9.9", source="test")

    archive = system_backup.BACKUP_DIR / f"{meta['backup_id']}.zip"
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert names == {"app.py", "logic/mod.py", ".nzbpostarr-backup-meta.json"}
    assert meta["file_count"] == 2
    if os.name != "nt":
        assert archive.stat().st_mode & 0o777 == 0o600
    assert [b["backup_id"] for b in system_backup.list_backups()] == [meta["backup_id"]]


def test_manifest_deletion_removes_dropped_files_but_never_protected_paths(monkeypatch, tmp_path) -> None:
    root = tmp_path / "app"
    for rel in ("app.py", "logic/old.py", "data/keep.json", "config.yaml", ".venv/lib.py", "logs/a.log"):
        _write(root / rel)
    _write(
        root / updater.RELEASE_MANIFEST_NAME,
        json.dumps({"files": ["app.py", "logic/old.py", "data/keep.json", "config.yaml", ".venv/lib.py",
                               "logs/a.log", "../outside.txt"]}),
    )
    _write(tmp_path / "outside.txt")
    new_tree = tmp_path / "release"
    _write(new_tree / "app.py", "new")
    _write(new_tree / updater.RELEASE_MANIFEST_NAME, json.dumps({"files": ["app.py"]}))
    _point_at(monkeypatch, root)

    result = updater._apply_project_tree(new_tree)

    assert result["removed"] == 1
    assert not (root / "logic" / "old.py").exists()
    for kept in ("data/keep.json", "config.yaml", ".venv/lib.py", "logs/a.log"):
        assert (root / kept).exists(), kept
    assert (tmp_path / "outside.txt").exists()
    assert (root / "app.py").read_text(encoding="utf-8") == "new"
    assert json.loads((root / updater.RELEASE_MANIFEST_NAME).read_text(encoding="utf-8")) == {"files": ["app.py"]}


def test_apply_without_a_previous_manifest_deletes_nothing(monkeypatch, tmp_path) -> None:
    root = tmp_path / "app"
    _write(root / "logic" / "old.py")
    new_tree = tmp_path / "release"
    _write(new_tree / "app.py")
    _write(new_tree / updater.RELEASE_MANIFEST_NAME, json.dumps({"files": ["app.py"]}))
    _point_at(monkeypatch, root)

    assert updater._apply_project_tree(new_tree)["removed"] == 0
    assert (root / "logic" / "old.py").exists()


def test_startup_cleanup_is_list_bound_and_idempotent(monkeypatch, tmp_path) -> None:
    root = tmp_path / "app"
    for rel in ("logic/gone.py", "logic/stays.py", "data/state.json", "backups/b.zip", ".venv/x.py"):
        _write(root / rel)
    (root / "logic" / "dir_entry").mkdir(parents=True)
    listing = tmp_path / "removed_paths.txt"
    listing.write_text(
        "# dropped modules\nlogic/gone.py\n\ndata/state.json\nbackups/b.zip\n.venv/x.py\nlogic/dir_entry\n"
        "../escape.txt\nlogic/never_existed.py\n",
        encoding="utf-8",
    )
    _write(tmp_path / "escape.txt")
    _point_at(monkeypatch, root)
    monkeypatch.setattr(updater, "REMOVED_PATHS_FILE", listing)

    assert updater.cleanup_removed_paths() == ["logic/gone.py"]
    assert updater.cleanup_removed_paths() == []
    for kept in ("logic/stays.py", "data/state.json", "backups/b.zip", ".venv/x.py"):
        assert (root / kept).exists(), kept
    assert (root / "logic" / "dir_entry").is_dir()
    assert (tmp_path / "escape.txt").exists()


def test_shipped_removed_paths_list_is_present() -> None:
    assert updater.REMOVED_PATHS_FILE.is_file()
