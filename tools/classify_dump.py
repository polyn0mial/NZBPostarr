"""Dump the pending classifier's verdict for every row under one root.

Scans ROOT as the only external watch folder (no indexers, no upload history,
no anime cache, no network) and prints one sorted JSON line per pending row:
relpath, category, itype, confidence and method. Lazily loaded folder children
are expanded, so the dump covers the whole tree.

ROOT may be a directory, or a manifest ROOT.tree listing one relative path per
line (a trailing "/" marks an empty directory); a manifest is built as empty
placeholder files in a temporary directory first. The committed dump for ROOT
is ROOT.jsonl.

Run:
  python tools/classify_dump.py ROOT            print the dump
  python tools/classify_dump.py ROOT --write    rewrite ROOT.jsonl
  python tools/classify_dump.py ROOT --check    exit 1 when the dump differs from ROOT.jsonl
"""

import argparse
import difflib
import json
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NoIndexers:
    def enabled(self, _conf: Any) -> list[Any]:
        return []


def _manifest_path(root: Path) -> Path:
    return root.with_name(root.name + ".tree")


def _dump_path(root: Path) -> Path:
    return root.with_name(root.name + ".jsonl")


def build_tree_from_manifest(manifest: Path, target: Path) -> None:
    """Create the manifest's paths under target as empty placeholder files."""
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        path = target / line.rstrip("/")
        if line.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def _walk_rows(snapshot: dict[str, Any], rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    from logic import pending_snapshot

    for row in rows:
        yield row
        nested = list(row.get("children") or []) + list(row.get("files") or [])
        if not nested and int(row.get("child_count") or 0) > 0:
            lazy = pending_snapshot.build_external_children_for_request(
                snapshot,
                str(row.get("key") or ""),
                str(row.get("path") or ""),
            )
            nested = list(lazy.get("children") or [])
        yield from _walk_rows(snapshot, nested)


def _row_record(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(row.get("path") or ""))
    try:
        relpath = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relpath = str(row.get("rel_path") or row.get("name") or "")
    return {
        "relpath": relpath,
        "category": str(row.get("detected_category") or row.get("category") or ""),
        "itype": str(row.get("itype") or ""),
        "confidence": row.get("detection_confidence"),
        "method": row.get("detection_method"),
    }


def scan_root(root: Path) -> list[str]:
    """Return the sorted JSON lines for every pending row under root."""
    from logic import pending_snapshot

    conf = SimpleNamespace(folder_paths=[{"path": str(root), "category": "external"}], skip_files=None)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(pending_snapshot, "get_config", lambda: conf))
        stack.enter_context(
            mock.patch.object(
                pending_snapshot,
                "get_configured_category_folders",
                lambda *_a, **_kw: [("external", root)],
            )
        )
        stack.enter_context(
            mock.patch.object(
                pending_snapshot.database,
                "get_dashboard_data",
                lambda _ids: (set(), {}, {}, {}),
            )
        )
        stack.enter_context(mock.patch("core.registry.get_registry", lambda: _NoIndexers()))
        stack.enter_context(mock.patch("core.registry.get_available_categories", lambda: []))
        stack.enter_context(mock.patch("core.registry.resolve_indexer_backfill", lambda _idx, _conf: False))
        stack.enter_context(mock.patch("logic.classify.anime.get_cached", lambda _name: None))
        pending_snapshot.invalidate_pending_indexer_context()
        try:
            snapshot = pending_snapshot.scan_pending_snapshot()
            rows: list[dict[str, Any]] = []
            for group in snapshot.get("items", {}).get("external", []):
                rows.extend(group.get("items") or [])
            records = [_row_record(root, row) for row in _walk_rows(snapshot, rows)]
        finally:
            pending_snapshot.invalidate_pending_indexer_context()
    return sorted(json.dumps(record, sort_keys=True) for record in records)


def dump_root(root: Path) -> list[str]:
    """Dump root, building it from its manifest when it is not a directory."""
    if root.is_dir():
        return scan_root(root)
    manifest = _manifest_path(root)
    if not manifest.is_file():
        raise FileNotFoundError(f"Neither a directory {root} nor a manifest {manifest} exists")
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / root.name
        tree.mkdir()
        build_tree_from_manifest(manifest, tree)
        return scan_root(tree)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump the pending classifier verdict for every row under a root.")
    parser.add_argument("root", type=Path, help="Directory to scan, or ROOT for a ROOT.tree manifest")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Compare with ROOT.jsonl and exit 1 on a difference")
    mode.add_argument("--write", action="store_true", help="Rewrite ROOT.jsonl")
    args = parser.parse_args(argv)

    lines = dump_root(args.root)
    dump_file = _dump_path(args.root)
    if args.write:
        dump_file.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")
        print(f"Wrote {len(lines)} rows to {dump_file}")
        return 0
    if args.check:
        expected = dump_file.read_text(encoding="utf-8").splitlines()
        if expected == lines:
            print(f"Classifier dump unchanged ({len(lines)} rows).")
            return 0
        sys.stdout.writelines(
            f"{line}\n" for line in difflib.unified_diff(expected, lines, str(dump_file), "current", lineterm="")
        )
        return 1
    sys.stdout.writelines(f"{line}\n" for line in lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
