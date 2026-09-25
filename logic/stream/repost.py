"""
NZB repost requests and the Usenet-to-Usenet upload itself.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from core.config import NNTPServer, get_config
from core.database import check_duplicate_dynamic, record_nntp_success, update_db_destination
from core.indexers.registry import get_enabled_indexers
from core.utils import get_thread_job, log_info, run_command, update_job_progress
from logic.uploaders import (
    _build_nyuu_command,
    _parse_nyuu_completion_stats,
    build_nyuu_progress_parser,
    submit_api,
)
from logic.stream.manifest import (
    _safe_output_name,
    build_procjson_inputs,
    build_stream_manifest_path,
    prepare_stream_manifest,
    write_stream_manifest,
)
from logic.stream.nntp import StreamError, _enabled_servers
from logic.stream.nzb import read_nzb_head_metadata


@dataclass(frozen=True, slots=True)
class StreamRequestOptions:
    """Normalized, transport-independent stream request fields."""

    source_path: str
    category: str
    submit_mode: str


def normalize_submit_mode(raw: Optional[str]) -> str:
    value = str(raw or "post_and_submit").strip().lower()
    if value in {"post_only", "post-only", "post"}:
        return "post_only"
    return "post_and_submit"


def normalize_stream_request(
    *,
    upload_filename: Optional[str],
    source_path: Optional[str],
    monitor_folder: bool,
    category: Optional[str],
    submit_mode: Optional[str],
) -> StreamRequestOptions:
    """Validate mutually exclusive request sources before route orchestration."""
    normalized_source = str(source_path or "").strip()
    normalized_filename = str(upload_filename or "").strip()
    has_upload = bool(normalized_filename)
    has_source = bool(normalized_source)

    if has_upload and has_source:
        raise StreamError("Choose either an uploaded NZB or a server-side path, not both")
    if not has_upload and not has_source:
        raise StreamError("Provide an NZB upload or a server-side path")
    if monitor_folder and has_upload:
        raise StreamError("Folder monitoring requires a server-side folder path")
    if has_upload and not normalized_filename.lower().endswith(".nzb"):
        raise StreamError("Only .nzb files are supported")

    return StreamRequestOptions(
        source_path=normalized_source,
        category=str(category or "").strip(),
        submit_mode=normalize_submit_mode(submit_mode),
    )


def _path_compare_key(path: str | Path) -> str:
    text = str(path)
    return text.casefold() if os.name == "nt" else text


def normalize_source_path(raw_path: str | Path) -> Path:
    path = Path(str(raw_path)).expanduser()
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def resolve_source_nzb_paths(raw_path: str | Path) -> list[Path]:
    """Resolve a server-side NZB file or directory into concrete NZB file paths."""
    source = normalize_source_path(raw_path)
    if not source.exists():
        raise StreamError(f"Source path does not exist: {source}")

    if source.is_file():
        if source.suffix.lower() != ".nzb":
            raise StreamError(f"Source path is not an NZB file: {source}")
        return [source]

    nzb_files = sorted(
        [
            path
            for path in source.rglob("*.nzb")
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(source).parts)
        ],
        key=lambda item: str(item).lower(),
    )
    if not nzb_files:
        raise StreamError(f"No .nzb files found under: {source}")
    return nzb_files


def resolve_posting_server(
    posting_server_name: Optional[str], servers: Optional[Sequence[NNTPServer]] = None
) -> NNTPServer:
    available = list(servers or _enabled_servers())
    if not available:
        raise StreamError("No enabled NNTP servers are configured")
    if not posting_server_name:
        return available[0]

    wanted = str(posting_server_name).strip().casefold()
    for server in available:
        if str(server.name).strip().casefold() == wanted:
            return server
    raise StreamError(f"Posting server '{posting_server_name}' was not found or is not enabled")


def _normalize_stream_category(raw: Optional[str]) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    primary = re.split(r"\s*(?:>|/|\\|\||:)\s*", text, maxsplit=1)[0].strip()
    slug = re.sub(r"[^a-z0-9]+", "_", primary.lower()).strip("_")

    if "anime" in lowered or slug == "anime":
        return "anime"
    if slug in {"movie", "movies", "film", "films"} or "movie" in lowered:
        return "movies"
    if slug in {"tv", "television", "show", "shows", "series"}:
        return "tv"
    if any(token in lowered for token in ("television", "series", "episode", "season")):
        return "tv"
    return slug or "misc"


def resolve_stream_category(source_path: Path, explicit_category: Optional[str] = None) -> str:
    explicit = _normalize_stream_category(explicit_category)
    if explicit:
        return explicit
    metadata = read_nzb_head_metadata(source_path)
    detected = _normalize_stream_category(metadata.get("category"))
    return detected or "misc"


def _stream_itype(category: str) -> str:
    normalized = str(category or "misc").strip().lower()
    if normalized == "movies":
        return "Movie"
    if normalized == "tv":
        return "TV Show"
    return normalized.replace("_", " ").title() or "Misc"


def _target_indexers(target_indexer_id: Optional[str]) -> list[str]:
    conf = get_config()
    indexers = get_enabled_indexers(conf)
    if target_indexer_id:
        indexers = [idx for idx in indexers if idx.id == target_indexer_id]
    return [idx.id for idx in indexers]


def upload_stream_manifest(
    manifest_path: Path,
    release_name: str,
    server: NNTPServer,
    total_size: int,
    *,
    nzb_path: Optional[Path] = None,
) -> tuple[Optional[dict[str, Any]], Optional[Path]]:
    """Post a prepared stream manifest to Usenet using Nyuu."""
    conf = get_config()
    job = get_thread_job()
    if job:
        job["current_stage"] = f"STREAMING ({server.name})"

    generated_nzb = nzb_path or conf.get_nzb_path(_safe_output_name(release_name))
    generated_nzb.parent.mkdir(parents=True, exist_ok=True)
    if generated_nzb.exists():
        generated_nzb.unlink(missing_ok=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = build_procjson_inputs(manifest_path, manifest)
    if not inputs:
        raise StreamError("Prepared stream manifest does not contain any files")

    cmd = _build_nyuu_command(
        conf,
        server,
        generated_nzb,
        inputs,
        server.max_connections,
        check_connections=3,
        check_tries=5,
        group_pool=conf.alt_bins or ["alt.binaries.misc"],
    )

    update_job_progress(
        msg=f"Streaming {release_name} via {server.name}...",
        item_name=release_name,
        item_percent=0,
        item_size=total_size,
        speed="Starting...",
        eta="Starting...",
    )

    parser = build_nyuu_progress_parser(
        server_name=server.name,
        total_size=total_size,
        article_size=conf.article_size,
        action_label="Streaming",
        completion_label="Stream complete",
        verbose=bool(getattr(conf, "verbose", False)),
    )
    success, output = run_command(cmd, f"Nyuu-{server.name}", job, parser=parser, quiet=True)
    if not success:
        detail = next((line for line in reversed(output) if line), "")
        if detail:
            raise StreamError(detail)
        return None, generated_nzb

    if not generated_nzb.exists() or generated_nzb.stat().st_size < 100:
        actual_size = generated_nzb.stat().st_size if generated_nzb.exists() else 0
        raise StreamError(f"Nyuu finished but produced an invalid NZB ({actual_size} bytes)")

    update_job_progress(item_percent=100, eta="0s", msg=f"[{server.name}] Stream complete")
    return _parse_nyuu_completion_stats(output, total_size, server.name), generated_nzb


def stream_nzb_upload(
    *,
    source_path: Path,
    category: str,
    release_name: Optional[str] = None,
    target_indexer_id: Optional[str] = None,
    posting_server_name: Optional[str] = None,
    submit_mode: str = "post_and_submit",
    force: bool = False,
    test_mode: bool = False,
    manifest_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Stream an NZB payload from Usenet back to Usenet without staging decoded files on disk."""
    conf = get_config()
    servers = _enabled_servers()
    if not servers:
        raise StreamError("No enabled NNTP servers are configured")

    if not source_path.exists():
        raise StreamError(f"Source NZB does not exist: {source_path}")

    submit_mode = normalize_submit_mode(submit_mode)
    primary_server = resolve_posting_server(posting_server_name, servers)
    chosen_release = (release_name or source_path.stem).strip() or source_path.stem
    itype = _stream_itype(category)
    target_ids = [] if submit_mode == "post_only" else _target_indexers(target_indexer_id)
    manifest_path = manifest_path or build_stream_manifest_path(chosen_release)
    generated_nzb: Optional[Path] = None

    manifest = prepare_stream_manifest(source_path, chosen_release)
    write_stream_manifest(manifest, manifest_path)

    total_size = int(manifest.get("total_size") or 0)
    if total_size <= 0:
        raise StreamError("Unable to determine the streamed release size from the NZB")

    update_job_progress(total=1, processed=0, skipped=0, percent=0)

    if target_ids and not force:
        dupes = check_duplicate_dynamic(chosen_release, itype, target_ids)
        if all(dupes.get(dest) is not None for dest in target_ids):
            log_info(f"Skipping stream for {chosen_release}: already present on all target indexers.")
            update_job_progress(processed=0, skipped=1, percent=100, msg="Skipped - already uploaded")
            return {
                "status": "skipped",
                "release_name": chosen_release,
                "total_size": total_size,
                "manifest_path": str(manifest_path),
                "posting_server_name": primary_server.name,
                "submit_mode": submit_mode,
            }

    if test_mode:
        log_info(f"[TEST MODE] Parsed NZB stream manifest for {chosen_release} ({len(manifest['files'])} files)")
        update_job_progress(processed=1, skipped=0, percent=100, msg="Test mode complete")
        return {
            "status": "test",
            "release_name": chosen_release,
            "files": len(manifest["files"]),
            "total_size": total_size,
            "manifest_path": str(manifest_path),
            "posting_server_name": primary_server.name,
            "submit_mode": submit_mode,
        }

    upload_result, generated_nzb = upload_stream_manifest(
        manifest_path,
        chosen_release,
        primary_server,
        total_size,
        nzb_path=conf.get_nzb_path(_safe_output_name(f"{chosen_release}.stream")),
    )
    if not upload_result or generated_nzb is None:
        raise StreamError("Streaming post failed before Nyuu produced a usable NZB")

    record_nntp_success(chosen_release, total_size, itype)

    submission_results: list[tuple[str, bool, str]] = []
    for dest in target_ids:
        result = submit_api(
            chosen_release,
            dest,
            conf,
            nzb_path=generated_nzb,
            cat=category,
        )
        submission_results.append((dest, result.success, result.reason))
        if result.success:
            update_db_destination(dest, chosen_release, total_size, chosen_release, itype=itype, **upload_result)
        else:
            update_db_destination(
                dest,
                chosen_release,
                total_size,
                chosen_release,
                itype=itype,
                status="failed",
                error=result.reason or "Indexer submission rejected or unreachable",
            )

    success_count = sum(1 for _dest, ok, _reason in submission_results if ok)
    log_info(
        f"Streamed {chosen_release}: {len(manifest['files'])} files, {total_size} bytes, "
        f"{success_count}/{len(submission_results)} indexers accepted."
    )
    update_job_progress(processed=1, skipped=0, percent=100, msg="Stream complete")

    return {
        "status": "completed",
        "release_name": chosen_release,
        "files": len(manifest["files"]),
        "total_size": total_size,
        "indexer_results": submission_results,
        "manifest_path": str(manifest_path),
        "generated_nzb": str(generated_nzb),
        "posting_server_name": primary_server.name,
        "submit_mode": submit_mode,
    }
