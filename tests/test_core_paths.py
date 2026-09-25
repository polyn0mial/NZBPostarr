import os

import pytest

from core import paths


def test_path_key_resolves_symlinks(tmp_path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this host")

    assert paths.path_key(link) == paths.path_key(target)
    assert paths.is_at_or_below(link / "child", target)


def test_path_key_ignores_a_trailing_separator(tmp_path) -> None:
    folder = tmp_path / "Show"
    folder.mkdir()

    assert paths.path_key(f"{folder}{os.sep}") == paths.path_key(folder)
    assert paths.path_key("") == ""
    assert paths.path_key(None) == ""


def test_path_key_casefolds_only_on_windows(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "MixedCase"

    monkeypatch.setattr(paths.os, "name", "posix")
    assert paths.path_key(folder) == str(paths.resolve_path(folder))

    monkeypatch.setattr(paths.os, "name", "nt")
    assert paths.path_key(folder) == str(paths.resolve_path(folder)).casefold()


def test_is_at_or_below(tmp_path) -> None:
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)

    assert paths.is_at_or_below(root, root)
    assert paths.is_at_or_below(root / "a" / "file.mkv", root)
    assert not paths.is_at_or_below(tmp_path / "root2" / "file.mkv", root)
    assert not paths.is_at_or_below(tmp_path, root)
    assert not paths.is_at_or_below("", root)


def test_checkpoint_key_matches_persisted_strings_without_resolving(tmp_path) -> None:
    # A checkpoint persisted by an earlier run: normalized text, forward slashes, never resolved.
    persisted = "/data/media/TV/Show.S01/../Show.S01/Show.S01E01.mkv"

    assert paths.checkpoint_key(persisted) == "/data/media/TV/Show.S01/Show.S01E01.mkv"
    assert paths.checkpoint_key(" /data/x/ ") == os.path.normpath("/data/x").replace("\\", "/")
    assert paths.checkpoint_key("") == ""
    missing = tmp_path / "gone" / "item.mkv"
    assert paths.checkpoint_key(missing) == os.path.normpath(str(missing)).replace("\\", "/")


def test_resolve_path_expands_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert paths.resolve_path("~/x") == (tmp_path / "x").resolve()
