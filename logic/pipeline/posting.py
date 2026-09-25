"""Posting: nyuu upload plans, commands and progress, upload sets per server, and the single-item upload flow."""

from __future__ import annotations

import copy
import math
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

import humanfriendly  # type: ignore[import-untyped]
from loguru import logger

from core.config import Config, NNTPServer, get_config
from core.database import record_nntp_success
from core.registry import get_indexer
from core.utils import (
    extract_percentage,
    extract_speed,
    get_priority_label,
    get_thread_job,
    is_priority_key,
    log_info,
    run_command,
    set_thread_job,
    update_job_progress,
)
from logic.pipeline.record import _record_folder_hierarchy_rows
from logic.pipeline.submit import submit_and_record


@dataclass(frozen=True, slots=True)
class NyuuUploadPlan:
    """Immutable inputs and estimates for one Nyuu posting attempt."""

    files: tuple[Path, ...]
    total_size: int
    estimated_articles: int
    connections: int


def _parse_nyuu_article_bytes(article_size_str: str) -> int:
    """Parse Nyuu `-a` article size to bytes.

    Nyuu (and many CLIs) typically interpret K/M/G as decimal unless explicitly binary.
    We use humanfriendly's decimal parsing for consistency with config strings like "1M".
    """
    try:
        return int(humanfriendly.parse_size(article_size_str, binary=False))
    except (humanfriendly.InvalidSize, TypeError, ValueError):
        return int(humanfriendly.parse_size("700K", binary=False))


def _estimate_nyuu_post_percent(est_total_articles: int, articles_read: int, articles_posted: int) -> int:
    """Estimate percent progress from Nyuu log:2s counters.

    We blend read (70%) and posted (30%) to give a smoother curve.
    'read' advances as Nyuu processes data from disk (fast, steady).
    'posted' advances as the server acknowledges each article (slower, bursty).
    Using only 'posted' causes the progress to stall at 90%+ while Nyuu
    finishes check/verify cycles.  We cap at 99% until a completion signal.
    """
    denom = max(int(est_total_articles or 0), int(articles_read or 0), 1)
    read_pct = int(articles_read or 0) / denom
    post_pct = int(articles_posted or 0) / denom
    blended = read_pct * 0.7 + post_pct * 0.3
    pct = int(blended * 100)
    return max(0, min(99, pct))


def _choose_nyuu_connection_count(max_connections: int, est_total_articles: int) -> int:
    """Scale Nyuu connections to the size of the upload.

    Small uploads with only a few hundred articles suffer when we use the full
    connection pool because connection churn and post-check overhead dominate.
    Larger uploads still benefit from higher parallelism.
    """
    max_conn = max(1, int(max_connections or 1))
    articles = max(1, int(est_total_articles or 1))

    if articles <= 256:
        target = max(8, math.ceil(articles / 24))
    elif articles <= 1024:
        target = max(12, math.ceil(articles / 24))
    elif articles <= 4096:
        target = max(24, math.ceil(articles / 20))
    else:
        target = max_conn

    return max(4, min(max_conn, target))


def _build_nyuu_upload_plan(conf: Config, server: NNTPServer, source_dir: Path) -> NyuuUploadPlan:
    files = sorted(path for path in source_dir.glob("*") if path.is_file())
    readme_dir = conf.script_dir / "indexers" / "readme"
    readme = readme_dir / "readme.txt"
    if not readme.exists():
        readme = readme_dir / "readme.example.txt"
    if conf.include_readme and readme.exists():
        files.append(readme)
    if not files:
        raise FileNotFoundError(f"No files found for upload in {source_dir}")

    total_size = sum(path.stat().st_size for path in files)
    article_bytes = _parse_nyuu_article_bytes(conf.article_size or "700K")
    estimated_articles = max(1, total_size // max(1, article_bytes) + 1)
    connections = _choose_nyuu_connection_count(server.max_connections, estimated_articles)
    return NyuuUploadPlan(tuple(files), total_size, estimated_articles, connections)


def _build_nyuu_command(
    conf: Config,
    server: NNTPServer,
    nzb_path: Path,
    inputs: Sequence[Path | str],
    connections: int,
    *,
    check_connections: int = 5,
    check_tries: int = 10,
    group_pool: Optional[Sequence[str]] = None,
) -> list[str]:
    command = [
        conf.nyuu_path or "nyuu",
        "-h",
        server.host,
        "-P",
        str(server.port),
        "-u",
        server.user,
        "-p",
        server.password,
        "-n",
        str(connections),
        "--connection-threads",
        "4",
        "--check-connections",
        str(check_connections),
        "--check-tries",
        str(check_tries),
        "-S" if server.ssl else "--no-ssl",
        "-a",
        conf.article_size,
        "-f",
        conf.poster,
        "-o",
        str(nzb_path),
        "-O",
        "--nzb-cork",
        "--use-post-pool",
        "--disk-req-size",
        "1M",
        "--progress",
        "log:2s",
    ]
    command.extend(str(path) for path in inputs)
    groups = list(group_pool or conf.alt_bins or [
        "alt.binaries.boneless",
        "alt.binaries.mom",
        "alt.binaries.moovee",
        "alt.binaries.teevee",
    ])
    selected_groups = random.sample(groups, min(len(groups), 3))
    command.extend(["-g", ",".join(selected_groups)])
    return command


def build_nyuu_progress_parser(
    *,
    server_name: str,
    total_size: int,
    article_size: str,
    action_label: str,
    completion_label: str,
    progress_key: Optional[str] = None,
    verbose: bool = False,
) -> Callable[[str], Optional[str]]:
    """Create the shared Nyuu log parser used by staged and streaming uploads."""
    from logic.stats_engine import ProgressTracker

    tracker = ProgressTracker(total_size)
    article_bytes = _parse_nyuu_article_bytes(article_size or "700K")
    estimated_articles = max(1, total_size // max(1, article_bytes) + 1)

    def parser(line: str) -> Optional[str]:
        clean_line = re.sub(r"[\x08\x0d]", "", line).strip()
        if not clean_line:
            return None

        log_match = re.search(
            r"Article posting progress:\s*(\d+)\s*read,\s*(\d+)\s*posted",
            clean_line,
        )
        if log_match:
            pct = _estimate_nyuu_post_percent(
                estimated_articles,
                int(log_match.group(1)),
                int(log_match.group(2)),
            )
            stats = tracker.update(pct, None)
            speed = stats.get("speed", "Calculating...")
            message = f"[{server_name}] {pct}% | {speed}"
            if verbose:
                logger.log("PROGRESS", message)
            update_job_progress(
                item_percent=pct,
                speed=speed,
                eta=stats.get("eta", "--"),
                key=progress_key,
                msg=message,
            )
            return f"{action_label}: {pct}% | {stats.get('speed', '0 B/s')}"

        # Distinct name from the integer `pct` above: this one is a float or None.
        parsed_pct = extract_percentage(clean_line)
        if parsed_pct is not None:
            try:
                speed_text = extract_speed(clean_line)
                stats = tracker.update(parsed_pct, speed_text)
                message = f"[{server_name}] {int(parsed_pct)}% | {speed_text or 'Starting...'}"
                if verbose:
                    logger.log("PROGRESS", message)
                update_job_progress(
                    item_percent=int(parsed_pct),
                    speed=stats.get("speed", speed_text or "0 B/s"),
                    eta=stats.get("eta", "--"),
                    key=progress_key,
                    msg=message,
                )
                return f"{action_label}: {int(parsed_pct)}% | {stats.get('speed', '0 B/s')}"
            except (TypeError, ValueError) as exc:
                logger.debug(f"Parser error on line '{clean_line}': {exc}")
                return None

        if "Finished uploading" in clean_line:
            update_job_progress(
                item_percent=100,
                eta="0s",
                key=progress_key,
                msg=f"[{server_name}] {completion_label}",
            )
            return f"{action_label} Finished"

        if clean_line.lower().startswith("[warn]"):
            logger.warning(f"[Nyuu-{server_name}] {clean_line}")
            return f"[WARN] {clean_line[6:].strip()}"

        return None

    return parser


def _log_nzb_diagnostics(nzb_path: Path) -> None:
    """Inspect a generated NZB iteratively without retaining its XML tree."""
    try:
        from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
        from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
    except ImportError as exc:
        logger.debug(f"NZB diagnostic parser unavailable (non-fatal): {exc}")
        return

    try:
        nzb_xml_size = nzb_path.stat().st_size
        nzb_file_count = 0
        total_segments = 0
        total_segment_bytes = 0
        for _event, elem in ET.iterparse(str(nzb_path), events=("end",)):
            local_name = str(elem.tag).rsplit("}", 1)[-1]
            if local_name == "file":
                nzb_file_count += 1
            elif local_name == "segment":
                total_segments += 1
                total_segment_bytes += int(elem.get("bytes", "0"))
            elem.clear()
        nzb_size_mb = total_segment_bytes / (1024 * 1024)
        logger.info(
            f"NZB Diagnostic: {nzb_path.name} | "
            f"XML={nzb_xml_size} bytes | "
            f"Files={nzb_file_count} | "
            f"Segments={total_segments} | "
            f"TotalBytes={total_segment_bytes} ({nzb_size_mb:.2f} MB)"
        )
        if total_segments == 0 or total_segment_bytes == 0:
            logger.error(
                f"NZB PROBLEM: NZB has {nzb_file_count} files but "
                f"{total_segments} segments / {total_segment_bytes} bytes - "
                "this will show as 0.00 MB on indexers!"
            )
    except (OSError, TypeError, ValueError, ET.ParseError, DefusedXmlException) as exc:
        logger.debug(f"NZB diagnostic parse failed (non-fatal): {exc}")


def _parse_nyuu_completion_stats(
    output: Iterable[str],
    total_size: int,
    server_name: str,
) -> Dict[str, Any]:
    output_text = "\n".join(output)
    duration = 0.0
    parsed_speed_bps = 0.0

    finish_match = re.search(r"in\s+(\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)", output_text)
    if finish_match:
        hours, minutes, seconds = finish_match.group(1).split(":")
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    speed_match = re.search(r"\((\d+\.?\d*)\s*([KMG]?[iI]?[bB]/s)\)", output_text, re.I)
    if speed_match:
        from logic.stats_engine import parse_speed_to_bps

        parsed_speed_bps = parse_speed_to_bps(f"{speed_match.group(1)} {speed_match.group(2)}")

    speed_bps = total_size / duration if duration > 0 and total_size > 0 else parsed_speed_bps
    return {
        "duration": duration,
        "speed_bps": speed_bps,
        "server_name": server_name,
    }


def upload_item(
    name: str,
    server: NNTPServer,
    nzb_path: Optional[Path] = None,
    progress_key: Optional[str] = None,
    prepared_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """NNTP Upload using Nyuu. Returns stats dict on success, None on failure."""
    conf = get_config()
    tmp = prepared_dir or conf.tmp_sub / name
    nzb = nzb_path or conf.get_nzb_path(name)
    nzb.parent.mkdir(parents=True, exist_ok=True)
    if nzb.exists():
        try:
            nzb.unlink()
        except OSError:
            pass

    # Determine priority label for logging
    is_priority = is_priority_key(progress_key)
    label = get_priority_label(is_priority)

    try:
        try:
            plan = _build_nyuu_upload_plan(conf, server, tmp)
        except OSError as exc:
            logger.error(str(exc))
            return None

        update_job_progress(
            msg=f"Uploading {label}{name}...",
            item_name=name,
            item_percent=0,
            item_size=plan.total_size,
            speed="Starting...",
            eta="Starting...",
            key=progress_key,
        )

        job = get_thread_job()
        is_test = conf.test_run or (job and job.get("test_mode"))

        if is_test:
            log_info(f"[TEST MODE] Skipping NNTP upload for {name}")
            nzb.touch()
            return {
                "duration": 0.1,
                "speed_bps": 1024 * 1024 * 10,
                "server_name": server.name,
            }

        if job:
            job["current_stage"] = f"UPLOADING ({server.name})"

        current_connections = plan.connections

        log_info(
            f"Uploading {label}{name} via {server.name} "
            f"({current_connections}/{server.max_connections} conns, ~{plan.estimated_articles} articles)..."
        )

        nyuu_parser = build_nyuu_progress_parser(
            server_name=server.name,
            total_size=plan.total_size,
            article_size=conf.article_size,
            action_label="Uploading",
            completion_label="Upload complete",
            progress_key=progress_key,
            verbose=conf.verbose,
        )

        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                logger.info(f"Retrying upload ({attempt}/{max_attempts})...")

            missing_files = [path for path in plan.files if not path.exists()]
            if missing_files:
                missing_sample = ", ".join(path.name for path in missing_files[:3])
                logger.error(
                    f"Upload source files disappeared before Nyuu could finish for {name}: "
                    f"{missing_sample}{' ...' if len(missing_files) > 3 else ''}"
                )
                return None

            cmd = _build_nyuu_command(conf, server, nzb, plan.files, current_connections)
            success, output = run_command(cmd, f"Nyuu-{server.name}", job, parser=nyuu_parser, quiet=True)
            if success:
                # Validate that Nyuu actually produced a non-empty NZB
                if not nzb.exists() or nzb.stat().st_size < 100:
                    actual_size = nzb.stat().st_size if nzb.exists() else 0
                    logger.error(f"Nyuu reported success but NZB is empty/invalid ({actual_size} bytes): {nzb}")
                    # Don't retry - Nyuu returned success, something is fundamentally wrong
                    return None

                _log_nzb_diagnostics(nzb)

                # Ensure the UI hits 100% even if Nyuu never emitted an explicit "100%" line.
                update_job_progress(
                    item_percent=100,
                    eta="0s",
                    key=progress_key,
                    msg=f"[{server.name}] Upload complete",
                )
                logger.success(f"Uploaded: {label}{name}")

                # NOTE: Do NOT purge tmp data here - parallel upload threads
                # share the same tmp folder. Cleanup happens in the finally
                # block of process_single() after ALL threads complete.

                return _parse_nyuu_completion_stats(output, plan.total_size, server.name)

            # Handle errors / retries
            output_str = "\n".join(output).lower()
            if any(x in output_str for x in ["482", "connection limit", "too many connections"]):
                if attempt < max_attempts:
                    wait_time = 15 * attempt
                    logger.warning(f"Connection limit hit. Backing off {wait_time}s and lowering connections.")
                    current_connections = max(5, current_connections - 10)
                    time.sleep(wait_time)
                    continue

            if job and job.get("stop_requested"):
                return None

            time.sleep(3)

        return None
    except Exception as e:
        logger.error(f"Upload process exception: {e}")
        return None


def _select_upload_server(upload_backbone: str, servers: list[Any], selected_backbones: list[str]) -> Any:
    """Choose a server deterministically for a pending upload set."""
    matching = [
        server for server in servers if any(upload_backbone.lower() == backbone.lower() for backbone in server.backbone)
    ]
    if matching:
        return matching[0]

    unused = [
        server for server in servers if not any(backbone.lower() in selected_backbones for backbone in server.backbone)
    ]
    if unused:
        return unused[0]

    return sorted(servers, key=lambda server: server.name.lower())[0]


def _build_upload_sets(indexers: list[Any], conf: Any) -> list[dict[str, Any]]:
    """Construct logical upload sets for the selected indexers."""
    from core.registry import resolve_indexer_priority

    upload_sets: list[dict[str, Any]] = []
    for indexer in indexers:
        upload_sets.append(
            {
                "id": indexer.id,
                "dests": [indexer.id],
                "backbone": "NetNews",
                "priority": resolve_indexer_priority(indexer, conf),
            }
        )
    return upload_sets


def _split_parallel_server_connections(raw_sets: list[tuple[dict[str, Any], Any]]) -> list[tuple[dict[str, Any], Any]]:
    """Split server connection budgets when multiple uploads share the same server."""
    server_counts: dict[str, int] = {}
    for _, server in raw_sets:
        server_counts[server.name] = server_counts.get(server.name, 0) + 1

    planned_sets: list[tuple[dict[str, Any], Any]] = []
    for upload_set, server in raw_sets:
        if server_counts.get(server.name, 0) > 1:
            split_server = server.model_copy() if hasattr(server, "model_copy") else copy.copy(server)
            split_server.max_connections = max(5, int(server.max_connections / server_counts[server.name]))
            planned_sets.append((upload_set, split_server))
        else:
            planned_sets.append((upload_set, server))
    return planned_sets


def _upload_target_display(upload_set: dict[str, Any]) -> str:
    """Return the formatted display label for an upload set."""
    from core.registry import get_indexer

    ids_part = upload_set["id"].split(" (")[0]
    resolved: list[str] = []
    for indexer_id in ids_part.split("/"):
        clean_id = indexer_id.strip()
        indexer = get_indexer(clean_id)
        if indexer and indexer.color:
            resolved.append(f"<fg {indexer.color}>{clean_id}</fg>")
        else:
            resolved.append(clean_id)
    return "/".join(resolved) + (" (P)" if upload_set.get("priority") else " (NP)")


@dataclass(frozen=True)
class _SingleUploadContext:
    conf: Any
    name: str
    path: Path
    category: str
    itype: str
    base_folder: Optional[Path]
    test_mode: bool
    item_size: int
    job: Optional[dict[str, Any]]
    submission_category: str
    nfo_path: Optional[Path]
    mediainfo_path: Optional[Path]
    key: str


@dataclass
class _SingleUploadState:
    lock: Lock = field(default_factory=Lock)
    any_success: bool = False
    errors: list[str] = field(default_factory=list)


def _plan_upload_runs(
    upload_sets: list[dict[str, Any]],
    *,
    all_servers: list[Any],
    dest_status: Dict[str, Optional[str]],
    indexer_map: Dict[str, Any],
    force: bool,
    is_new: bool,
    global_backfill: bool,
) -> list[tuple[dict[str, Any], Any]]:
    """Map logical upload sets to concrete server executions.

    Same-server destinations are merged into ONE physical upload regardless of
    priority, so an item is posted to Usenet once per server. The priority
    split is kept only as ``submission_groups``, so each indexer group still
    gets its own API submission (and its own "Priority " name prefix) without
    a second NNTP post. A set that includes priority destinations keeps the
    " (P)" suffix in its id so the uploader still labels the post as priority.
    """
    server_to_group: dict[str, dict[str, Any]] = {}
    selected_backbones: list[str] = []

    for upload_set in upload_sets:
        needed_dests: list[str] = []
        is_priority = upload_set["priority"]

        for dest_id in upload_set["dests"]:
            is_done = dest_status.get(dest_id) is not None
            if not force and is_done:
                logger.debug("[PLAN] dest=%s skipped (already done, force=False)", dest_id)
                continue
            indexer = indexer_map.get(dest_id)
            can_backfill = indexer.backfill if indexer else False
            if force or is_new or (global_backfill and can_backfill):
                needed_dests.append(dest_id)
                logger.debug(
                    "[PLAN] dest=%s ADDED (force=%r  is_new=%r  backfill=%r)",
                    dest_id,
                    force,
                    is_new,
                    can_backfill,
                )
            else:
                logger.debug(
                    "[PLAN] dest=%s skipped (force=%r  is_new=%r  backfill=%r/%r)",
                    dest_id,
                    force,
                    is_new,
                    global_backfill,
                    can_backfill,
                )

        if not needed_dests:
            continue

        server = _select_upload_server(upload_set["backbone"], all_servers, selected_backbones)
        selected_backbones.extend(backbone.lower() for backbone in server.backbone)

        bucket = server_to_group.setdefault(
            server.name,
            {"server": server, "priority_dests": [], "priority_ids": [], "normal_dests": [], "normal_ids": []},
        )
        if is_priority:
            bucket["priority_dests"].extend(needed_dests)
            bucket["priority_ids"].append(upload_set["id"])
        else:
            bucket["normal_dests"].extend(needed_dests)
            bucket["normal_ids"].append(upload_set["id"])

    raw_sets: list[tuple[dict[str, Any], Any]] = []
    for bucket in server_to_group.values():
        server = bucket["server"]
        submission_groups: list[dict[str, Any]] = []
        all_ids: list[str] = []
        all_dests: list[str] = []

        if bucket["priority_dests"]:
            submission_groups.append({"dests": list(dict.fromkeys(bucket["priority_dests"])), "priority": True})
            all_ids.extend(bucket["priority_ids"])
            all_dests.extend(bucket["priority_dests"])
        if bucket["normal_dests"]:
            submission_groups.append({"dests": list(dict.fromkeys(bucket["normal_dests"])), "priority": False})
            all_ids.extend(bucket["normal_ids"])
            all_dests.extend(bucket["normal_dests"])

        has_priority = bool(bucket["priority_dests"])
        raw_sets.append(
            (
                {
                    "id": "/".join(dict.fromkeys(all_ids)) + (" (P)" if has_priority else ""),
                    "dests": list(dict.fromkeys(all_dests)),
                    "backbone": server.backbone[0] if server.backbone else "Unknown",
                    "priority": has_priority,
                    "submission_groups": submission_groups,
                },
                server,
            )
        )

    raw_sets.sort(key=lambda value: value[0].get("priority", False), reverse=True)
    return raw_sets


def _run_single_upload_flow(
    upload_set: dict[str, Any],
    upload_server: Any,
    *,
    context: _SingleUploadContext,
    state: _SingleUploadState,
) -> bool:
    """Run and isolate one posting/indexer destination flow."""
    if context.job:
        set_thread_job(context.job)

    # A single upload_set may cover several indexer priority groups that share
    # this server (see _plan_upload_runs). Only ONE physical NNTP upload happens
    # below; submission_groups gives each priority group its own API submission
    # (and its own "Priority " name prefix) against that same posted NZB.
    submission_groups = upload_set.get("submission_groups") or [
        {"dests": upload_set["dests"], "priority": upload_set.get("priority", False)}
    ]
    for group in submission_groups:
        group_display = _upload_target_display({"id": "/".join(group["dests"]), "priority": group["priority"]})
        log_info(
            f"--- [UPLOAD] Targeting: {group_display} "
            f"({upload_server.name} @ {upload_server.max_connections} conn) ---"
        )

    set_id_safe = upload_set["id"].replace("/", "_").replace(" ", "_")
    unique_nzb = context.conf.get_nzb_path(
        f"{context.name}.{set_id_safe}.{upload_server.name}"
    )
    try:
        prepared_dir = (
            Path(str(context.job.get("_current_prepare_tmp")))
            if context.job and context.job.get("_current_prepare_tmp")
            else None
        )
        upload_result = upload_item(
            context.name,
            upload_server,
            nzb_path=unique_nzb,
            progress_key=upload_set["id"],
            prepared_dir=prepared_dir,
        )
        if not upload_result:
            return False

        if not context.test_mode and context.item_size > 0:
            record_nntp_success(context.key, context.item_size, context.itype)
            _record_folder_hierarchy_rows(
                context.path,
                base_folder=context.base_folder,
                category=context.category,
            )

        _api_results, run_success = submit_and_record(
            submission_groups,
            conf=context.conf,
            name=context.name,
            nzb_path=unique_nzb,
            submission_category=context.submission_category,
            item_size=context.item_size,
            key=context.key,
            itype=context.itype,
            item_path=context.path,
            base_folder=context.base_folder,
            category=context.category,
            test_mode=context.test_mode,
            upload_result=upload_result,
            nfo_path=context.nfo_path,
            mediainfo_path=context.mediainfo_path,
        )

        with state.lock:
            state.any_success = run_success or state.any_success
            if context.job:
                context.job["total_bytes"] = context.job.get("total_bytes", 0) + context.item_size
        return run_success
    except Exception as exc:
        logger.exception(
            f"Upload target '{upload_set['id']}' failed for {context.name}: {exc}"
        )
        with state.lock:
            state.errors.append(f"{upload_set['id']}: {exc}")
        return False
    finally:
        if unique_nzb.exists():
            try:
                unique_nzb.unlink()
            except OSError:
                pass
