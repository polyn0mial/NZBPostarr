"""
Stream manifests and the procjson:// helper Nyuu runs (`python -m logic.stream.manifest emit`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from core.config import get_config
from logic.stream.nntp import StreamError, UsenetReader, decode_yenc_article
from logic.stream.nzb import parse_nzb_structure, probe_file_details


def build_stream_manifest_path(release_name: str) -> Path:
    conf = get_config()
    manifest_dir = Path(conf.script_dir) / "data" / "tmp" / "stream-manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    return manifest_dir / f"{_safe_output_name(release_name)}.{uuid.uuid4().hex[:8]}.json"


def prepare_stream_manifest(source_path: Path, release_name: Optional[str] = None) -> dict[str, Any]:
    manifest = parse_nzb_structure(source_path)
    reader = UsenetReader()
    try:
        total_size = 0
        for file_entry in manifest["files"]:
            name, size = probe_file_details(file_entry, reader=reader)
            file_entry["name"] = name
            file_entry["size"] = size
            total_size += size
        manifest["total_size"] = total_size
    finally:
        reader.close()

    if release_name:
        manifest["release_name"] = release_name.strip()
    return manifest


def write_stream_manifest(manifest: dict[str, Any], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return destination


def _build_helper_command(manifest_path: Path, file_index: int) -> str:
    args = [
        sys.executable,
        "-m",
        "logic.stream.manifest",
        "emit",
        "--manifest",
        str(manifest_path),
        "--file-index",
        str(file_index),
    ]
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def build_procjson_inputs(manifest_path: Path, manifest: dict[str, Any]) -> list[str]:
    inputs: list[str] = []
    for idx, file_entry in enumerate(manifest.get("files", [])):
        command = _build_helper_command(manifest_path, idx)
        proc_spec = [file_entry["name"], int(file_entry["size"]), command]
        inputs.append("procjson://" + json.dumps(proc_spec, separators=(",", ":")))
    return inputs


def _safe_output_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return clean or "stream"


def emit_manifest_file(manifest_path: Path, file_index: int) -> int:
    """Helper entrypoint executed by Nyuu procjson:// inputs."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    if file_index < 0 or file_index >= len(files):
        raise StreamError(f"File index out of range: {file_index}")

    file_entry = files[file_index]
    expected_size = int(file_entry.get("size") or 0)
    if expected_size <= 0:
        raise StreamError(f"Manifest entry does not have a valid size: {file_entry.get('name')}")

    reader = UsenetReader()
    bytes_written = 0
    try:
        for segment in file_entry.get("segments", []):
            lines = reader.body(segment["message_id"])
            payload, _meta = decode_yenc_article(lines)
            sys.stdout.buffer.write(payload)
            bytes_written += len(payload)
        sys.stdout.buffer.flush()
    finally:
        reader.close()

    if bytes_written != expected_size:
        raise StreamError(
            f"Decoded size mismatch for {file_entry.get('name')}: expected {expected_size}, wrote {bytes_written}"
        )
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(prog="python -m logic.stream.manifest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    emit_parser = subparsers.add_parser("emit", help="Emit one streamed file to stdout")
    emit_parser.add_argument("--manifest", required=True)
    emit_parser.add_argument("--file-index", required=True, type=int)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect an NZB and print the prepared manifest summary")
    inspect_parser.add_argument("source")
    inspect_parser.add_argument("--release-name")

    args = parser.parse_args()
    if args.command == "emit":
        return emit_manifest_file(Path(args.manifest), int(args.file_index))

    manifest = prepare_stream_manifest(Path(args.source), args.release_name)
    print(
        json.dumps(
            {
                "release_name": manifest["release_name"],
                "files": len(manifest["files"]),
                "total_size": manifest["total_size"],
                "source_name": manifest["source_name"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
