"""
📦 NZBPostarr - Uploaders Module
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NNTP (Nyuu) and Indexer API submission logic.
Now uses the dynamic indexer plugin system.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import math
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

import humanfriendly  # type: ignore[import-untyped]
from loguru import logger

from core.config import Config, NNTPServer, get_config
from core.registry import get_indexer, submit_to_indexer
from core.utils import (
    extract_percentage,
    extract_speed,
    get_priority_label,
    get_thread_job,
    is_priority_key,
    log_info,
    run_command,
    update_job_progress,
)


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """Canonical outcome returned by every indexer submission path."""

    success: bool
    status: str
    reason: str


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


# Error statuses that should NOT be retried (permanent failures)
_PERMANENT_FAILURE_STATUSES = frozenset({"duplicate", "misconfigured"})

# Error statuses that SHOULD be retried (transient failures)
_RETRYABLE_FAILURE_STATUSES = frozenset({"network_error", "rejected"})


def is_retryable_error(status: str) -> bool:
    """Return True if the error status indicates a transient/retryable failure."""
    return status in _RETRYABLE_FAILURE_STATUSES


def submit_api(
    name: str,
    dest: str,
    config: Config,
    nzb_path: Optional[Path] = None,
    cat: str = "tv",
    nfo_path: Optional[Path] = None,
    mediainfo_path: Optional[Path] = None,
) -> SubmitResult:
    """Submit NZB to indexer API via dynamic plugin system.

    Retries transient failures (network errors, 5xx, timeouts) up to
    ``upload_max_retries`` times with exponential backoff.  Permanent
    failures (invalid API key, banned) are returned immediately.
    """
    nzb = nzb_path or config.get_nzb_path(name)
    if not nzb.exists():
        reason = f"NZB not found at {nzb}"
        logger.error(f"Submission failed: {reason}")
        return SubmitResult(False, "error", reason)

    # Guard against empty/truncated NZB files (e.g. Nyuu partial failure)
    nzb_size = nzb.stat().st_size
    if nzb_size == 0:
        reason = f"NZB is 0 bytes (empty file) at {nzb}"
        logger.error(f"Submission blocked: {reason}")
        return SubmitResult(False, "error", reason)
    if nzb_size < 100:
        reason = f"NZB is suspiciously small ({nzb_size} bytes) at {nzb}"
        logger.error(f"Submission blocked: {reason}")
        return SubmitResult(False, "error", reason)

    # Get the indexer definition
    indexer = get_indexer(dest)
    if not indexer:
        reason = f"Unknown indexer identifier: {dest}"
        logger.error(reason)
        return SubmitResult(False, "error", reason)

    try:
        job = get_thread_job()
        if config.test_run or (job and job.get("test_mode")):
            log_info(f"[TEST MODE] Skipping indexer submission to {indexer.log_name}")
            return SubmitResult(True, "success", "Test mode: indexer submission skipped")

        # Clean "Priority " from release name for submission
        clean_name = name.replace("Priority ", "")

        # Sanitize release name
        rls_name = re.sub(r"\.(nzb|mkv|mp4|avi|ts|m4v|wmv)$", "", clean_name, flags=re.I)
        rls_name = rls_name.replace(" & ", ".and.").replace("&", ".and.")
        rls_name = rls_name.replace("'", "")
        rls_name = re.sub(r"[^a-zA-Z0-9.\-_]", ".", rls_name)
        rls_name = re.sub(r"\.{2,}", ".", rls_name).strip(".")

        max_retries = getattr(config, "upload_max_retries", 3)
        retry_delay = getattr(config, "upload_retry_delay_seconds", 5)
        last_reason = ""

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                # Check if job was stopped between retries
                if job and job.get("stop_requested"):
                    return SubmitResult(False, "error", last_reason or "Job stopped during retry")
                wait = retry_delay * attempt  # linear backoff: 10s, 15s, 20s, ...
                logger.info(f"{indexer.log_name} Retry {attempt}/{max_retries} in {wait}s...")
                time.sleep(wait)

            success, status, reason = submit_to_indexer(
                indexer=indexer,
                rls_name=rls_name,
                nzb_path=nzb,
                config=config,
                cat=cat,
                nfo_path=nfo_path,
                mediainfo_path=mediainfo_path,
            )
            last_reason = reason

            if success:
                return SubmitResult(True, "success", reason)

            # Permanent failure - no point retrying
            if status in _PERMANENT_FAILURE_STATUSES:
                logger.warning(f"{indexer.log_name} Permanent failure ({status}): {reason}")
                return SubmitResult(False, status, reason)

            # Retryable failure - try again unless this was the last attempt
            if attempt < max_retries:
                logger.warning(f"{indexer.log_name} Attempt {attempt}/{max_retries} failed ({status}): {reason}")
            else:
                logger.error(f"{indexer.log_name} All {max_retries} attempts failed: {reason}")

        return SubmitResult(False, "error", last_reason)
    except Exception as e:
        reason = str(e)
        logger.error(f"{indexer.log_name} Submission Error: {reason}")
        return SubmitResult(False, "error", reason)
