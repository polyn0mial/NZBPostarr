"""
📦 NZBPostarr - Orchestrator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Unified module for batch processing and orchestration.
Includes RAR/PAR2 pre-processing and media info extraction.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import copy
import multiprocessing as mp
import os
import queue as stdlib_queue
import re
import shutil
import subprocess
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Iterator, List, Optional, cast

import humanfriendly  # type: ignore[import-untyped]
from loguru import logger
from core.config import get_config
from core.database import (
    record_nntp_success,
    update_db_destination,
)
from core.utils import (
    AUDIOBOOK_EXTENSIONS,
    EBOOK_EXTENSIONS,
    MUSIC_EXTENSIONS,
    VIDEO_EXTENSIONS,
    compute_size_uncached,
    extract_percentage,
    extract_speed,
    get_thread_job,
    has_multi_file_episode_pattern,
    log_completed,
    log_info,
    log_success,
    log_verbose,
    normalize_submission_category,
    purge_item_data,
    run_command,
    set_thread_job,
    should_skip_file,
    update_job_progress,
    wait_for_job_resume,
)
from logic.pending_scan import (
    _tv_pack_episode_rejection_reason,
    find_configured_root,
    get_configured_folders,
    has_clear_movie_year,
    resolve_explicit_path,
    scan_configured_items,
)
from logic.uploaders import submit_api, upload_item

_ONE_GIB: int = humanfriendly.parse_size("1 GiB")
_APP_EXTENSIONS = {
    ".7z",
    ".apk",
    ".bat",
    ".bin",
    ".deb",
    ".dmg",
    ".exe",
    ".img",
    ".ipa",
    ".iso",
    ".msi",
    ".pkg",
    ".rar",
    ".rpm",
    ".tar",
    ".tbz2",
    ".tgz",
    ".xz",
    ".zip",
}
_ITEM_VALIDATION_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class QueueItemValidation:
    """Validation outcome for a single queued item before upload starts."""

    outcome: str
    path: Path
    category: str
    db_type: str
    message: str = ""
    base_folder: Optional[Path] = None
    item_size_bytes: int = 0
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None
    submission_category: Optional[str] = None


def _validation_worker_entry(
    result_queue: Any,
    *,
    path_text: str,
    category: str,
    force: bool,
    configured_folder_paths: list[str],
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[list[str]],
    skip_enabled: bool,
    skip_config: Optional[Dict[Any, Any]],
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None,
    prefetched_base_folder_path: Optional[str] = None,
) -> None:
    """Run queue-item validation in an isolated subprocess."""
    try:
        result = _validate_queue_item(
            Path(path_text),
            category=category,
            force=force,
            configured_folders=[Path(folder) for folder in configured_folder_paths],
            target_indexer_id=target_indexer_id,
            target_indexer_ids=list(target_indexer_ids) if target_indexer_ids else None,
            skip_enabled=skip_enabled,
            skip_config=skip_config,
            prefetched_dest_status=prefetched_dest_status,
            prefetched_base_folder=Path(prefetched_base_folder_path) if prefetched_base_folder_path else None,
        )
        result_queue.put(("ok", result))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result_queue.put(("error", f"Validation crashed for {Path(path_text).name}: {exc}"))


@dataclass(frozen=True)
class SupportAssetScan:
    """Single-pass scan results reused across mediainfo and upload sidecars."""

    nfo_path: Optional[Path] = None
    mediainfo_source_path: Optional[Path] = None


def _tool_exists(tool_cmd: str) -> bool:
    """Return True when a configured executable is directly present or resolvable on PATH."""
    text = str(tool_cmd or "").strip()
    if not text:
        return False
    candidate = Path(text)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate.exists()
    return shutil.which(text) is not None


def _processing_tool_commands(conf: Any) -> dict[str, str]:
    """Resolve the command strings used for prep/upload tooling."""
    return {
        "rar": str(getattr(conf, "rar_path", "") or "rar"),
        "parpar": str(getattr(conf, "parpar_path", "") or "parpar"),
        "nyuu": str(getattr(conf, "nyuu_path", "") or "nyuu"),
    }


def _mediainfo_output_path(path: Path, conf: Any) -> Path:
    """Return the canonical mediainfo sidecar path for an item."""
    return conf.mediainfo_sub / f"{path.name}.mediainfo.nfo"


def _sanitize_mediainfo_output(output: str, target: Path, conf: Any) -> str:
    """Strip absolute host paths from the mediainfo text artifact."""
    try:
        rel_target = target.relative_to(conf.base_folder)
        rel_folder = target.parent.relative_to(conf.base_folder)
    except ValueError:
        rel_target = Path(target.name)
        rel_folder = Path(".")

    sanitized_output = output
    patterns = [
        (
            r"^(Complete name\s+:\s+).*",
            r"\g<1>" + re.escape(str(rel_target).replace("\\", "/")),
        ),
        (
            r"^(Folder name\s+:\s+).*",
            r"\g<1>" + re.escape(str(rel_folder).replace("\\", "/")),
        ),
        (r"^(File name\s+:\s+).*", r"\g<1>" + re.escape(target.name)),
    ]
    for pattern, replacement in patterns:
        sanitized_output = re.sub(pattern, replacement, sanitized_output, flags=re.MULTILINE)
    return sanitized_output


def _find_nfo_path(path: Path) -> Optional[Path]:
    """Locate the primary NFO sidecar associated with an item."""
    if path.is_dir():
        matches = [candidate for candidate in path.rglob("*.nfo") if "mediainfo" not in candidate.name.lower()]
        return matches[0] if matches else None

    direct_match = path.parent / f"{path.stem}.nfo"
    if direct_match.exists():
        return direct_match

    generic_matches = [
        candidate for candidate in path.parent.glob("*.nfo") if "mediainfo" not in candidate.name.lower()
    ]
    return generic_matches[0] if generic_matches else None


def _find_mediainfo_path(path: Path, conf: Any) -> Optional[Path]:
    """Return the generated mediainfo sidecar for an item when present."""
    candidate = _mediainfo_output_path(path, conf)
    return candidate if candidate.exists() else None


def _scan_item_support_assets(path: Path) -> SupportAssetScan:
    """Scan a release tree once to find the primary NFO and best mediainfo source."""
    if not path.is_dir():
        return SupportAssetScan(nfo_path=_find_nfo_path(path), mediainfo_source_path=path)

    nfo_path: Optional[Path] = None
    mediainfo_source_path: Optional[Path] = None
    largest_video_size = -1

    for root, dirs, files in os.walk(path):
        dirs[:] = [dirname for dirname in dirs if not dirname.startswith(".")]
        for filename in files:
            if filename.startswith("."):
                continue
            candidate = Path(root) / filename
            suffix = candidate.suffix.lower()

            if nfo_path is None and suffix == ".nfo" and "mediainfo" not in candidate.name.lower():
                nfo_path = candidate

            if suffix not in VIDEO_EXTENSIONS:
                continue

            try:
                size = candidate.stat().st_size
            except OSError:
                continue

            if size > largest_video_size:
                largest_video_size = size
                mediainfo_source_path = candidate

    return SupportAssetScan(nfo_path=nfo_path, mediainfo_source_path=mediainfo_source_path)


def _resolve_targeted_path(raw_path: str) -> Optional[Path]:
    """Targeted jobs only accept explicit absolute paths."""
    if not raw_path:
        return None
    path = Path(str(raw_path))
    return path if path.is_absolute() else None


def _normalize_runtime_path(path: Path) -> str:
    """Normalize a filesystem path for stable comparisons."""
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    return resolved.casefold() if os.name == "nt" else resolved


def _safe_fs_component(value: str, *, fallback: str = "item", max_length: int = 120) -> str:
    """Return a stable path component safe for temporary workspace names."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length]


def _new_prepare_tmp_path(conf: Any, name: str, job: Optional[dict[str, Any]]) -> Path:
    """Create a unique temp path for this item and expose it to upload workers."""
    job_part = _safe_fs_component(str(job.get("job_id") or "job") if job else "job", max_length=40)
    item_part = _safe_fs_component(name)
    tmp = conf.tmp_sub / f"{job_part}-{item_part}-{uuid.uuid4().hex[:10]}"
    if job is not None:
        job["_current_prepare_tmp"] = str(tmp)
    return tmp


def _has_enough_temp_space(conf: Any, item_name: str, total_bytes: int) -> bool:
    """Fail before RAR starts when the temp filesystem is clearly too full."""
    try:
        conf.tmp_sub.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(conf.tmp_sub).free
    except OSError as exc:
        logger.warning(f"Unable to check temp disk space for {item_name}: {exc}")
        return True

    required_bytes = int(total_bytes * 1.20) + _ONE_GIB
    if free_bytes >= required_bytes:
        return True

    free_label = humanfriendly.format_size(free_bytes, binary=True)
    required_label = humanfriendly.format_size(required_bytes, binary=True)
    message = f"Not enough temp disk space for {item_name}: need {required_label}, free {free_label}"
    logger.error(message)
    update_job_progress(msg=message, status="failed")
    return False


def _link_or_copy_filtered_file(source: Path, target: Path) -> None:
    """Stage a filtered pack file without duplicating data when possible."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        try:
            shutil.copy2(source, target)
        except shutil.SameFileError:
            pass  # already staged from a prior attempt; treat as success


def _append_cleanup_path(job: Optional[dict[str, Any]], path: Path) -> None:
    """Register a temporary file or directory to be removed when the job finishes."""
    if job is None:
        return
    cleanup_paths = job.setdefault("cleanup_paths", [])
    path_text = str(path)
    if path_text not in cleanup_paths:
        cleanup_paths.append(path_text)


def _create_filtered_tv_pack_staging(
    source_dir: Path,
    allowed_paths: list[Path],
    conf: Any,
    job: Optional[dict[str, Any]],
) -> Optional[Path]:
    """Build a temporary season-pack folder containing only validated episode files."""
    allowed_files = [path for path in allowed_paths if path.is_file()]
    if not source_dir.is_dir() or not allowed_files:
        return None

    job_part = _safe_fs_component(str(job.get("job_id") or "job") if job else "job", max_length=40)
    staging_root = conf.tmp_sub / "_filtered_packs" / f"{job_part}-{uuid.uuid4().hex[:10]}"
    staged_pack = staging_root / source_dir.name

    try:
        for source in allowed_files:
            try:
                relative = source.relative_to(source_dir)
            except ValueError:
                relative = Path(source.name)
            _link_or_copy_filtered_file(source, staged_pack / relative)
    except OSError as exc:
        logger.error(f"Unable to stage filtered TV pack {source_dir.name}: {exc}")
        shutil.rmtree(staging_root, ignore_errors=True)
        return None

    _append_cleanup_path(job, staging_root)
    log_info(
        f"Staged filtered TV pack {source_dir.name}: "
        f"{len(allowed_files)} episode file(s), ignored extras excluded"
    )
    return staged_pack


def _has_tv_season_pack_name(path: Path) -> bool:
    """Return True when a folder name itself looks like a season pack."""
    folder_name = path.name.lower()
    return bool(re.search(r"(?:^|[^a-z0-9])s\d{1,2}(?:[^a-z0-9]|$)", folder_name)) or any(
        token in folder_name for token in ("season", "complete")
    )


def _has_direct_child_season_pack_dirs(path: Path) -> bool:
    """Return True when a selected TV folder is a parent that contains season-pack folders."""
    if not path.is_dir():
        return False
    try:
        for child in path.iterdir():
            if child.is_dir() and _has_tv_season_pack_name(child):
                return True
    except OSError:
        return False
    return False


def _season_pack_dir_for_file(source_dir: Path, file_path: Path) -> Optional[Path]:
    """Find the nearest season-pack folder between a selected folder and an episode file."""
    try:
        file_path.relative_to(source_dir)
    except ValueError:
        return None

    current = file_path.parent
    while True:
        if current == source_dir:
            return current if is_season_pack(current) else None
        if _has_tv_season_pack_name(current) and not _has_direct_child_season_pack_dirs(current):
            return current
        if current.parent == current:
            return None
        try:
            current.relative_to(source_dir)
        except ValueError:
            return None
        current = current.parent


def _create_filtered_tv_pack_entries_for_selection(
    source_dir: Path,
    allowed_paths: list[Path],
    conf: Any,
    job: Optional[dict[str, Any]],
) -> tuple[list[tuple[Path, list[Path]]], set[str]]:
    """Stage filtered TV packs and return each pack with the episodes it contains."""
    allowed_files: list[Path] = []
    rejected_count = 0
    for path in allowed_paths:
        if not path.is_file():
            continue
        if _tv_pack_episode_rejection_reason(path, VIDEO_EXTENSIONS):
            rejected_count += 1
            continue
        allowed_files.append(path)
    if not source_dir.is_dir() or not allowed_files:
        if rejected_count:
            log_info(
                f"TV/anime pack selection {source_dir.name}: "
                f"{rejected_count} non-S##E##/invalid file(s) ignored"
            )
        return [], set()
    if rejected_count:
        log_info(
            f"TV/anime pack selection {source_dir.name}: "
            f"{rejected_count} non-S##E##/invalid file(s) ignored"
        )

    def pack_is_over_limit(pack_dir: Path) -> bool:
        folder_limit = getattr(conf, "folder_size_limit_gb", 99) or 0
        if not folder_limit or not getattr(conf, "folder_size_limit_enabled", True):
            return False
        size_gb = _live_size_bytes(pack_dir) / _ONE_GIB
        if size_gb <= folder_limit:
            return False
        log_info(
            f"TV/anime pack {pack_dir.name} is {size_gb:.1f} GB and exceeds "
            f"{folder_limit} GB; pack upload skipped, episodes still queued"
        )
        return True

    child_pack_files: dict[Path, list[Path]] = defaultdict(list)
    loose_files: list[Path] = []
    for path in allowed_files:
        pack_dir = _season_pack_dir_for_file(source_dir, path)
        if pack_dir is not None:
            child_pack_files[pack_dir].append(path)
        else:
            loose_files.append(path)

    if not child_pack_files:
        if _has_direct_child_season_pack_dirs(source_dir):
            log_info(
                f"TV parent selection {source_dir.name}: child season folders exist; "
                "parent folder itself will not be uploaded as a pack"
            )
            return [], set()
        if not _has_tv_season_pack_name(source_dir):
            log_info(
                f"TV parent selection {source_dir.name}: "
                f"{len(loose_files) or len(allowed_files)} loose episode file(s) queued as singles"
            )
            return [], set()
        if pack_is_over_limit(source_dir):
            return [], set()
        staged_pack = _create_filtered_tv_pack_staging(source_dir, allowed_paths, conf, job)
        if staged_pack is None:
            return [], set()
        episode_keys = {_normalize_runtime_path(path) for path in allowed_files}
        return [(staged_pack, sorted(allowed_files, key=lambda path: path.name.lower()))], episode_keys

    entries: list[tuple[Path, list[Path]]] = []
    episode_keys: set[str] = set()
    for child_dir in sorted(child_pack_files, key=lambda p: p.name.lower()):
        child_files = sorted(child_pack_files[child_dir], key=lambda path: path.name.lower())
        if pack_is_over_limit(child_dir):
            continue
        staged_pack = _create_filtered_tv_pack_staging(child_dir, child_files, conf, job)
        if staged_pack is not None:
            entries.append((staged_pack, child_files))
            episode_keys.update(_normalize_runtime_path(path) for path in child_files)

    if loose_files:
        log_info(f"TV parent selection {source_dir.name}: {len(loose_files)} loose episode file(s) queued as singles")

    return entries, episode_keys


def _looks_like_tv_season_pack_folder(path: Path, episode_paths: list[Path]) -> bool:
    """Return True for first-level season pack folders inferred from episode paths."""
    if not path.is_dir() or len(episode_paths) < 2:
        return False
    if _has_tv_season_pack_name(path):
        return True
    return False


def _inject_inferred_tv_pack_entries(
    raw_items: list[tuple[Path, str]],
    conf: Any,
    job: Optional[dict[str, Any]],
    already_staged_source_dirs: Optional[set[str]] = None,
) -> list[tuple[Path, str]]:
    """Infer selected season-pack folders when the UI sent only child episode paths."""
    existing_dirs = {
        _normalize_runtime_path(path)
        for path, cat in raw_items
        if cat in {"tv", "anime"} and path.is_dir()
    }
    episodes_by_parent: dict[Path, list[Path]] = defaultdict(list)
    for path, cat in raw_items:
        if cat in {"tv", "anime"} and path.is_file():
            episodes_by_parent[path.parent].append(path)

    pack_parents = {
        parent
        for parent, episode_paths in episodes_by_parent.items()
        if _normalize_runtime_path(parent) not in existing_dirs
        and (
            already_staged_source_dirs is None
            or _normalize_runtime_path(parent) not in already_staged_source_dirs
        )
        and _looks_like_tv_season_pack_folder(parent, episode_paths)
    }
    if not pack_parents:
        return raw_items

    staged_by_parent: dict[Path, Path] = {}
    for parent in sorted(pack_parents, key=lambda p: p.name.lower()):
        staged_pack = _create_filtered_tv_pack_staging(
            parent,
            sorted(episodes_by_parent[parent], key=lambda p: p.name.lower()),
            conf,
            job,
        )
        if staged_pack is not None:
            staged_by_parent[parent] = staged_pack

    if not staged_by_parent:
        return raw_items

    injected: list[tuple[Path, str]] = []
    inserted_parents: set[Path] = set()
    for path, cat in raw_items:
        parent = path.parent if cat in {"tv", "anime"} and path.is_file() else None
        staged_pack = staged_by_parent.get(parent) if parent is not None else None
        if staged_pack is not None and parent not in inserted_parents:
            injected.append((staged_pack, cat))
            inserted_parents.add(parent)
        injected.append((path, cat))

    logger.info(
        f"[QUEUE-RUN] inferred {len(inserted_parents)} TV season pack(s) from episode-only selection"
    )
    return injected


def _processing_anime_lookup(name: str) -> Optional[bool]:
    """Use cached Jikan state first, then live lookup when anime checking is enabled."""
    from logic.anime_cache import get_cached, is_anime

    cached = get_cached(name)
    if cached is not None:
        return cached

    conf = get_config()
    if not getattr(conf, "enable_anime_checking", False):
        return None
    return is_anime(name)


def _processing_cached_anime_lookup(name: str) -> Optional[bool]:
    """Use cached anime state only; queue validation should not wait on Jikan."""
    from logic.anime_cache import get_cached

    return get_cached(name)


def _should_skip_live_anime_lookup_for_targeted_item(category_hint: str, itype_hint: str) -> bool:
    hint_text = f"{category_hint} {itype_hint}".lower()
    if "anime" in hint_text:
        return False
    return any(token in hint_text for token in ("tv", "show", "episode", "season", "pack"))


def _log_explicit_resolution(resolution: Any) -> None:
    """Emit user-facing logs for explicit-path classification and ignored extras."""
    type_label = _submission_category_label(str(getattr(resolution, "category", "")))
    source_name = getattr(getattr(resolution, "source_path", None), "name", "") or "item"
    flags = [str(flag).upper() for flag in getattr(resolution, "content_flags", ()) if str(flag).strip()]
    flag_suffix = f" [{' + '.join(flags)}]" if flags else ""
    log_info(f"Detected Type: {type_label}{flag_suffix} [{resolution.detection_method}] - {source_name}")
    override_note = str(getattr(resolution, "override_note", "") or "").strip()
    if override_note:
        log_info(f"↪ Override: {override_note}")
    for ignored in getattr(resolution, "ignored_paths", ()):
        log_info(f"⏩ Ignored '{ignored.path.name}' - {ignored.reason}")


# ============================================================
#  PRE-PROCESSING (RAR, PAR2, Mediainfo)
# ============================================================


def generate_mediainfo(
    path: Path,
    *,
    conf: Optional[Any] = None,
    support_scan: Optional[SupportAssetScan] = None,
) -> Optional[Path]:
    """Generate Mediainfo for the item."""
    conf = conf or get_config()

    # Find the largest video file if it's a directory
    target = path
    if path.is_dir():
        scan = support_scan or _scan_item_support_assets(path)
        if not scan.mediainfo_source_path:
            return None
        target = scan.mediainfo_source_path

    info_path = _mediainfo_output_path(path, conf)
    info_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Reprocessing or retrying an unchanged item should not parse the same
        # media stream again. The canonical sidecar is valid while it is
        # non-empty and at least as new as the source selected for inspection.
        if (
            info_path.is_file()
            and info_path.stat().st_size > 0
            and info_path.stat().st_mtime >= target.stat().st_mtime
        ):
            log_verbose(f"Reusing current Mediainfo for {path.name}")
            return info_path

        # One CLI pass both validates the media and produces the exact text
        # artifact submitted alongside uploads.  PyMediaInfo previously caused
        # the same (potentially very large) source to be analyzed twice.
        cmd = ["mediainfo", "--Full", str(target)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)

        if result.returncode == 0 and result.stdout.strip():
            info_path.write_text(_sanitize_mediainfo_output(result.stdout, target, conf), encoding="utf-8")
            return info_path

    except Exception as e:
        logger.debug(f"Mediainfo generation failed for {target}: {e}")

    return None


def prepare_item(
    path: Path,
    name: str,
    _itype: str,
    *,
    support_scan: Optional[SupportAssetScan] = None,
    total_bytes: Optional[int] = None,
) -> bool:
    """RAR and PAR2 preparation using modern subprocess management."""
    conf = get_config()
    job = get_thread_job()
    tmp = _new_prepare_tmp_path(conf, name, job)
    tmp.mkdir(parents=True, exist_ok=False)

    logger.info(f"Preparing {name}...")
    # Validation already measured every upload item.  Reuse that value instead
    # of walking large release directories a second time.
    total_bytes = int(total_bytes) if total_bytes is not None else compute_size_uncached(path)
    update_job_progress(
        msg=f"Preparing {name}...",
        item_name=name,
        item_percent=0,
        item_size=total_bytes,
        speed="Starting...",
        eta="Starting...",
    )

    if not _has_enough_temp_space(conf, name, total_bytes):
        return False

    # Generate Mediainfo
    generate_mediainfo(path, conf=conf, support_scan=support_scan)

    if conf.test_run or (job and job.get("test_mode")):
        log_info(f"[TEST MODE] Skipping RAR/PAR2 for {name}")
        (tmp / f"{name}.rar").touch()
        return True

    if job:
        job["current_stage"] = "PREPARING"

    from logic.stats_engine import ProgressTracker

    tracker = ProgressTracker(total_bytes)

    def rar_parser(line: str) -> Optional[str]:
        line = line.replace("\x08", "").strip()  # strip_ansi already ran in run_command
        if not line:
            return None

        pct = extract_percentage(line)
        if pct is not None:
            stats = tracker.update(pct)
            update_job_progress(item_percent=int(pct), speed=stats["speed"], eta=stats["eta"])
            return f"RARing: {int(pct)}%"

        if "Creating archive" in line:
            return f"Archive: {Path(line.split()[-1]).name}"

        return None

    def par2_parser(line: str) -> Optional[str]:
        line = line.strip()
        if not line:
            return None

        pct = extract_percentage(line)
        if pct is not None:
            speed = extract_speed(line)
            eta_match = re.search(r"ETA:\s*([\w:]+)", line, re.I)
            eta = eta_match.group(1) if eta_match else None

            if not speed or not eta:
                stats = tracker.update(pct)
                speed = speed or stats["speed"]
                eta = eta or stats["eta"]

            update_job_progress(item_percent=int(pct), speed=speed, eta=eta)
            return f"PAR2ing: {int(pct)}%"
        return None

    try:
        # Step 1: RAR
        rar_cmd = [
            getattr(conf, "rar_path", None) or "rar",
            "a",
            "-y",
            "-o+",
            "-r",
            "-ep1",
            f"-v{conf.rar_size}",
            "-ma5",
            "-m0",
            str(tmp / f"{name}.rar"),
            str(path),
        ]

        success, _ = run_command(rar_cmd, "RAR", job, parser=rar_parser, quiet=True)
        if not success:
            return False

        # Step 2: PAR2
        rar_files = sorted(list(tmp.glob("*.rar")))
        if not rar_files:
            logger.error(f"No RAR files found in {tmp}")
            return False

        tracker = ProgressTracker(total_bytes)

        par_cmd = [
            conf.parpar_path or "parpar",
            "-q",
            "--auto-slice-size",
            "-r10%",
            f"-s{conf.article_size}",
            "-o",
            str(tmp / name),
        ] + [str(f) for f in rar_files]

        success, _ = run_command(par_cmd, "PAR2", job, parser=par2_parser, quiet=True)
        if not success:
            return False

        logger.info(f"Preparation completed for {name}")
        return True

    except Exception as e:
        logger.error(f"Preparation failed for {name}: {e}")
        update_job_progress(msg=f"Prep Error: {e}")
        return False


# ============================================================
#  HELPERS
# ============================================================


def check_tools(conf: Optional[Any] = None) -> bool:
    """Verify that required external tools are available."""
    conf = conf or get_config()
    commands = _processing_tool_commands(conf)
    missing = []
    for label, command in commands.items():
        if not _tool_exists(command):
            missing.append(f"{label} ({command})")
    if missing:
        msg = f"Missing required tools: {', '.join(missing)}"
        log_info(msg, "ERROR")
        update_job_progress(msg=msg, status="failed")
        return False
    return True


# ============================================================
#  SIMPLE LOGIC FOR SEASON PACKS (ANNOTATION: DEPLOY)
#  Handles conventional Usenet TV-library layouts.
#  - Any directory in TV root is a Season Pack.
#  - Any video file inside that directory is an Episode.
#  - Season packs are uploaded first, then their episode files.
# ============================================================


def is_season_pack(path: Path) -> bool:
    """
    Check if a path represents a season pack (directory).
    Parent TV collection folders are not season packs; only leaf-ish folders
    with season naming and no direct child season-pack folders should upload as packs.
    """
    return path.is_dir() and _has_tv_season_pack_name(path) and not _has_direct_child_season_pack_dirs(path)


def _live_size_bytes(path: Path) -> int:
    """Use an uncached size for upload-time guards; pending scans may cache stale growth."""
    return compute_size_uncached(path)


def get_tv_sort_key(path: Path) -> tuple[str, int, str]:
    """
    Simple Sort key for TV uploads.
    Groups by Pack Name (folder name) and ensures the pack uploads before its episode files.
    """
    if path.is_dir():
        # It's a Season Pack
        pack_name = path.name.lower()
        is_pack = 0
        sort_name = ""  # Pack sorts first within its group
    else:
        # It's an Episode File
        pack_name = path.parent.name.lower()
        is_pack = 1
        sort_name = path.name.lower()

    return (pack_name, is_pack, sort_name)


def _build_item_key(path: Path, base_folder: Optional[Path]) -> str:
    """Build the canonical database key for a queued item path."""
    try:
        if base_folder:
            return str(path.relative_to(base_folder)).replace("\\", "/")
    except ValueError:
        pass
    return path.name


def _folder_log_itype(category: str) -> str:
    normalized = str(category or "").strip().lower()
    if normalized == "tv":
        return "TV Folder"
    if normalized == "movies":
        return "Movie Folder"
    if normalized == "anime":
        return "Anime Folder"
    return "Folder"


def _iter_folder_ancestor_entries(path: Path, base_folder: Optional[Path]) -> list[tuple[str, Path]]:
    if base_folder is None:
        return []
    try:
        rel_path = path.relative_to(base_folder)
    except ValueError:
        return []

    rel_parent = rel_path.parent
    if str(rel_parent) in {"", "."}:
        return []

    entries: list[tuple[str, Path]] = []
    parts = rel_parent.parts
    for index in range(1, len(parts) + 1):
        rel_key = "/".join(parts[:index])
        folder_path = base_folder.joinpath(*parts[:index])
        entries.append((rel_key, folder_path))
    return entries


def _folder_size_cached(folder_path: Path) -> int:
    cache_key = _normalize_runtime_path(folder_path)
    job = get_thread_job()
    if job is not None:
        cache = job.setdefault("_folder_size_cache", {})
        if cache_key in cache:
            return int(cache[cache_key])
    # This cache is scoped to the active job.  The former process-wide LRU could
    # return stale sizes for mutable folders and retained path objects forever.
    size = compute_size_uncached(folder_path)
    if job is not None:
        cache = job.setdefault("_folder_size_cache", {})
        cache[cache_key] = int(size)
    return int(size)


def _record_folder_hierarchy_rows(
    path: Path,
    *,
    base_folder: Optional[Path],
    category: str,
    dest_id: Optional[str] = None,
    upload_result: Optional[dict[str, Any]] = None,
) -> None:
    folder_itype = _folder_log_itype(category)
    for folder_key, folder_path in _iter_folder_ancestor_entries(path, base_folder):
        folder_size = _folder_size_cached(folder_path)
        if folder_size <= 0:
            continue
        record_nntp_success(folder_key, folder_size, folder_itype)
        if dest_id and upload_result is not None:
            update_db_destination(
                dest_id,
                folder_path.name,
                folder_size,
                folder_key,
                itype=folder_itype,
                **upload_result,
            )


def _normalize_processing_category(raw: str) -> str:
    """Normalize queue/display categories into canonical submission categories."""
    return normalize_submission_category(raw)


def _normalize_processing_type(raw: str) -> str:
    """Normalize DB/display item types for strict submission-category decisions."""
    key = str(raw or "").strip().lower()
    special_types = {
        "tv episode": "tv_episode",
        "tv show": "tv",
        "tv pack": "tv",
        "season pack": "tv",
        "movie pack": "movie",
    }
    if key in special_types:
        return special_types[key]
    normalized = normalize_submission_category(key)
    return "movie" if normalized == "movies" else normalized


def _scan_release_media(path: Path) -> tuple[dict[str, int], list[str]]:
    """Return media-extension counts and discovered video filenames for a release."""
    counts = {"video": 0, "music": 0, "book": 0, "app": 0, "audiobook": 0}
    video_names: list[str] = []

    def _count_file(candidate: Path) -> None:
        suffix = candidate.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            counts["video"] += 1
            video_names.append(candidate.name)
        elif suffix in MUSIC_EXTENSIONS:
            counts["music"] += 1
        elif suffix in AUDIOBOOK_EXTENSIONS:
            counts["audiobook"] += 1
        elif suffix in EBOOK_EXTENSIONS:
            counts["book"] += 1
        elif suffix in _APP_EXTENSIONS:
            counts["app"] += 1

    if path.is_file():
        _count_file(path)
        return counts, video_names

    try:
        for root, _dirs, files in os.walk(path):
            for file_name in files:
                if file_name.startswith("."):
                    continue
                _count_file(Path(root) / file_name)
    except OSError:
        return counts, video_names

    return counts, video_names


def _collect_release_keywords(path: Path, itype: str) -> set[str]:
    """Extract lowercase release keywords from the path and item type."""
    values = [path.name, path.stem if path.suffix else "", itype]
    if path.parent and path.parent.name:
        values.append(path.parent.name)

    keywords: set[str] = set()
    for value in values:
        for token in re.split(r"[^a-zA-Z0-9]+", str(value or "").lower()):
            if token:
                keywords.add(token)
    return keywords


def _is_tv_pack_release(
    path: Path,
    normalized_category: str,
    normalized_type: str,
    keywords: set[str],
    video_names: list[str],
) -> bool:
    """Return True when the release should use the TV-pack submission category."""
    if normalized_type == "tv_pack":
        return True
    if normalized_category == "tv" and path.is_dir():
        return True
    if "season" in keywords or "complete" in keywords:
        return path.is_dir() or len(video_names) > 1
    return len(video_names) > 1 and has_multi_file_episode_pattern(video_names)


def _is_movie_pack_release(
    path: Path,
    normalized_category: str,
    normalized_type: str,
    keywords: set[str],
    video_names: list[str],
) -> bool:
    """Return True when the release should use the movie-pack submission category."""
    if normalized_type == "movie_pack":
        return True
    if normalized_category not in {"movies", "misc"} and normalized_type not in {"movie", "movie_pack"}:
        return False
    if not path.is_dir():
        return False

    pack_keywords = {"anthology", "boxset", "collection", "complete", "duology", "pack", "tetralogy", "trilogy"}
    if keywords & pack_keywords:
        return True

    movie_like_videos = [name for name in video_names if has_clear_movie_year(name)]
    return len(movie_like_videos) >= 2 and not has_multi_file_episode_pattern(movie_like_videos)


def _submission_category_label(category: str) -> str:
    """Return the human-readable category label used in logs."""
    return {
        "movies": "Movie",
        "tv": "TV",
        "movie": "Movie",
        "anime": "Anime",
        "music": "Music",
        "books": "Books",
        "apps": "Apps",
        "misc": "Misc",
    }.get(category, category or "Unknown")


def _resolve_submission_category(path: Path, category: str, itype: str) -> str:
    """Validate and preserve the detected category used for indexer submission."""
    normalized_category = _normalize_processing_category(category)
    normalized_itype = _normalize_processing_type(itype)
    media_counts, _video_names = _scan_release_media(path)

    allowed_categories = {"movies", "tv", "anime", "music", "books", "apps", "misc"}
    if normalized_category not in allowed_categories:
        if normalized_itype in allowed_categories:
            raise ValueError(
                f"explicit category '{category}' is invalid; detected type '{itype}' cannot override the configured category"
            )
        raise ValueError(f"explicit category '{category}' is invalid")

    resolved_category = normalized_category
    if normalized_category in {"tv", "movies", "misc"}:
        if normalized_itype in {"anime", "music", "books", "apps"}:
            resolved_category = normalized_itype
        else:
            from logic.anime_cache import get_cached

            candidates = [path.name]
            if path.suffix:
                candidates.append(path.stem)
            candidates.extend(parent.name for parent in path.parents[:2] if parent.name)

            seen: set[str] = set()
            for candidate in candidates:
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                try:
                    if get_cached(candidate) is True:
                        resolved_category = "anime"
                        break
                except Exception:
                    break
            else:
                if normalized_itype in {"tv", "tv_episode"} and normalized_category != "movies":
                    resolved_category = "tv"
                elif normalized_itype == "movie" and normalized_category != "tv":
                    resolved_category = "movies"

    has_video = media_counts["video"] > 0
    if resolved_category == "misc" and has_video:
        raise ValueError("video content cannot be submitted as Misc")
    if resolved_category == "books" and media_counts["book"] == 0 and media_counts["audiobook"] == 0:
        raise ValueError("book category requires book metadata or book file types")
    if resolved_category == "apps" and media_counts["app"] == 0:
        raise ValueError("apps category requires application/archive file types")
    if resolved_category == "music" and media_counts["music"] == 0:
        raise ValueError("music category requires audio file types")

    return resolved_category


def _normalize_runtime_target_paths(job: Optional[dict[str, Any]], fallback_paths: Optional[List[str]]) -> list[str]:
    """Return the mutable remaining-path queue for an active targeted job."""
    if job:
        current = job.get("target_paths")
        if isinstance(current, list) and current:
            return [str(path) for path in current if path]

    if not fallback_paths:
        return []

    normalized = [str(path) for path in fallback_paths if path]
    if job is not None:
        job["target_paths"] = normalized
    return normalized


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


def _descendant_files(path: Path, *, video_only: bool, sorted_names: bool) -> list[Path]:
    descendants: list[Path] = []
    try:
        for root, _, files in os.walk(str(path)):
            names = sorted(files) if sorted_names else files
            for file_name in names:
                if file_name.startswith("."):
                    continue
                child = Path(root) / file_name
                if video_only and child.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                descendants.append(child)
    except OSError:
        return descendants
    return descendants


def _plan_explicit_items(
    raw_items: list[tuple[Path, str]],
    *,
    skip_packs: bool,
    folder_limit: int,
    folder_enabled: bool,
    file_limit: int,
    file_enabled: bool,
) -> tuple[list[tuple[Path, str]], set[str], set[str]]:
    sorted_items: list[tuple[Path, str]] = []
    seen_paths: set[str] = set()
    oversized_folders: set[str] = set()
    oversized_files: set[str] = set()

    def append_item(path: Path, category: str) -> None:
        normalized = _normalize_runtime_path(path)
        if normalized in seen_paths:
            return
        seen_paths.add(normalized)
        sorted_items.append((path, category))
        if file_limit and file_enabled and path.is_file():
            if _live_size_bytes(path) / _ONE_GIB > file_limit:
                oversized_files.add(normalized)

    for path, category in raw_items:
        normalized = _normalize_runtime_path(path)
        if normalized in seen_paths:
            continue

        if category in {"tv", "anime"}:
            season_pack = is_season_pack(path)
            if skip_packs and season_pack:
                continue
            if folder_limit and folder_enabled and season_pack:
                if _live_size_bytes(path) / _ONE_GIB > folder_limit:
                    oversized_folders.add(normalized)
                    for child in _descendant_files(path, video_only=True, sorted_names=True):
                        append_item(child, category)
                    continue
            append_item(path, category)
            continue

        if folder_limit and folder_enabled and path.is_dir():
            if _live_size_bytes(path) / _ONE_GIB > folder_limit:
                oversized_folders.add(normalized)
                for child in _descendant_files(path, video_only=False, sorted_names=True):
                    append_item(child, category)
                continue

        append_item(path, category)

    return sorted_items, oversized_folders, oversized_files


def _expand_oversized_category_items(
    cat_items: list[Path],
    *,
    folder_limit: int,
    oversized_folders: set[str],
    season_packs_only: bool,
) -> list[Path]:
    expanded: list[Path] = []
    seen = {_normalize_runtime_path(path) for path in cat_items}
    for path in cat_items:
        if season_packs_only:
            if not is_season_pack(path):
                continue
        elif not path.is_dir():
            continue
        if _live_size_bytes(path) / _ONE_GIB <= folder_limit:
            continue

        oversized_folders.add(_normalize_runtime_path(path))
        for child in _descendant_files(
            path,
            video_only=season_packs_only,
            sorted_names=season_packs_only,
        ):
            child_key = _normalize_runtime_path(child)
            if child_key in seen:
                continue
            expanded.append(child)
            seen.add(child_key)

    if not expanded:
        return cat_items
    retained = [
        path for path in cat_items if _normalize_runtime_path(path) not in oversized_folders
    ]
    return [*retained, *expanded]


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _plan_sorted_items(
    raw_items: list[tuple[Path, str]],
    cats: list[str],
    *,
    preserve_explicit_order: bool,
    skip_packs: bool,
    folder_limit: int,
    folder_enabled: bool,
    file_limit: int,
    file_enabled: bool,
) -> tuple[list[tuple[Path, str]], set[str], set[str]]:
    """Deduplicate, sort, and expand raw items into the concrete processing queue."""
    if preserve_explicit_order:
        return _plan_explicit_items(
            raw_items,
            skip_packs=skip_packs,
            folder_limit=folder_limit,
            folder_enabled=folder_enabled,
            file_limit=file_limit,
            file_enabled=file_enabled,
        )

    by_cat: dict[str, list[Path]] = defaultdict(list)
    seen_paths: set[str] = set()
    for path, cat in raw_items:
        normalized = _normalize_runtime_path(path)
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        by_cat[cat].append(path)

    sorted_items: list[tuple[Path, str]] = []
    oversized_folders: set[str] = set()
    oversized_files: set[str] = set()

    for cat in cats:
        if cat not in by_cat:
            continue

        cat_items = list(by_cat[cat])
        if cat in {"tv", "anime"}:
            cat_items.sort(key=get_tv_sort_key)
            if skip_packs:
                cat_items = [path for path in cat_items if not is_season_pack(path)]
            elif folder_limit and folder_enabled:
                cat_items = _expand_oversized_category_items(
                    cat_items,
                    folder_limit=folder_limit,
                    oversized_folders=oversized_folders,
                    season_packs_only=True,
                )
                cat_items.sort(key=get_tv_sort_key)
            log_verbose(f"Sorted TV items: {[path.name for path in cat_items]}")
        else:
            if folder_limit and folder_enabled:
                cat_items = _expand_oversized_category_items(
                    cat_items,
                    folder_limit=folder_limit,
                    oversized_folders=oversized_folders,
                    season_packs_only=False,
                )
            cat_items.sort(key=_safe_mtime, reverse=True)

        if file_limit and file_enabled:
            for path in cat_items:
                if not path.is_file():
                    continue
                file_size = _live_size_bytes(path) / _ONE_GIB
                if file_size > file_limit:
                    oversized_files.add(_normalize_runtime_path(path))

        sorted_items.extend((path, cat) for path in cat_items)

    return sorted_items, oversized_folders, oversized_files


def _build_duplicate_prefetch_state(
    conf: Any,
    sorted_items: list[tuple[Path, str]],
    configured_folders: list[Path],
    *,
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
) -> tuple[Dict[str, str], Dict[str, Dict[str, Optional[str]]], Callable[[Path], Optional[Path]]]:
    """Resolve item DB keys and batch-prefetch duplicate state for the job queue."""
    from core.database import get_duplicate_status_batch
    from core.registry import get_enabled_indexers

    source_root_cache: Dict[str, Optional[Path]] = {}

    def source_root_for(item_path: Path) -> Optional[Path]:
        item_key = _normalize_runtime_path(item_path)
        if item_key not in source_root_cache:
            source_root_cache[item_key] = find_configured_root(item_path, configured_folders)
        return source_root_cache[item_key]

    eligible_indexers = get_enabled_indexers(conf)
    if target_indexer_ids:
        allowed_ids = set(target_indexer_ids)
        eligible_indexers = [idx for idx in eligible_indexers if idx.id in allowed_ids]
    elif target_indexer_id:
        eligible_indexers = [idx for idx in eligible_indexers if idx.id == target_indexer_id]
    eligible_indexer_ids = [idx.id for idx in eligible_indexers]

    item_db_keys: Dict[str, str] = {}
    for item, _cat in sorted_items:
        item_db_keys[_normalize_runtime_path(item)] = _build_item_key(item, source_root_for(item))

    prefetched_dupes: Dict[str, Dict[str, Optional[str]]] = {}
    if eligible_indexer_ids:
        item_keys = [item_db_keys[_normalize_runtime_path(item)] for item, _cat in sorted_items]
        prefetched_dupes = get_duplicate_status_batch(item_keys, eligible_indexer_ids)

    return item_db_keys, prefetched_dupes, source_root_for


def _iter_work_items(
    sorted_items: list[tuple[Path, str]],
    *,
    preserve_explicit_order: bool,
    runtime_job: Optional[dict[str, Any]],
    paths: Optional[List[str]],
) -> Iterator[tuple[int, tuple[Path, str]]]:
    """Yield the effective work order, respecting runtime reordering for targeted jobs."""
    if not preserve_explicit_order:
        yield from enumerate(sorted_items)
        return

    targeted_lookup = {_normalize_runtime_path(item): (item, cat) for item, cat in sorted_items}
    if runtime_job is None:
        yield from enumerate(sorted_items)
        return

    total = len(sorted_items)
    while targeted_lookup:
        runtime_paths = _normalize_runtime_target_paths(runtime_job, paths)
        next_key = None
        for raw_path in runtime_paths:
            resolved = _normalize_runtime_path(Path(raw_path))
            if resolved in targeted_lookup:
                next_key = resolved
                break

        if next_key is None:
            break

        runtime_job["target_paths"] = [
            raw_path for raw_path in runtime_paths if _normalize_runtime_path(Path(raw_path)) != next_key
        ]
        item, item_cat = targeted_lookup.pop(next_key)
        idx = total - len(targeted_lookup) - 1
        yield idx, (item, item_cat)


def _persist_runtime_job_checkpoint(job: Optional[dict[str, Any]]) -> None:
    """Persist active queue state without coupling processing to the queue service."""
    if job is None:
        return
    callback = job.get("_persist_callback")
    if not callable(callback):
        return
    try:
        callback()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(f"Could not persist runtime job checkpoint: {exc}")


def _runtime_checkpoint_path_key(path: Any) -> str:
    text = str(path or "").strip()
    return os.path.normpath(text).replace("\\", "/") if text else ""


def _begin_runtime_item_checkpoint(
    job: Optional[dict[str, Any]],
    path: Path,
    *,
    make_current: bool,
) -> None:
    if job is None:
        return
    path_text = str(path)
    path_key = _runtime_checkpoint_path_key(path_text)
    inflight = [
        str(value)
        for value in (job.get("_inflight_item_paths") or [])
        if str(value).strip()
    ]
    if path_key and all(_runtime_checkpoint_path_key(value) != path_key for value in inflight):
        inflight.append(path_text)
    job["_inflight_item_paths"] = inflight
    if make_current:
        job["_current_item_path"] = path_text
    _persist_runtime_job_checkpoint(job)


def _complete_runtime_item_checkpoint(job: Optional[dict[str, Any]], path: Path) -> None:
    if job is None:
        return
    path_key = _runtime_checkpoint_path_key(path)
    job["_inflight_item_paths"] = [
        str(value)
        for value in (job.get("_inflight_item_paths") or [])
        if _runtime_checkpoint_path_key(value) != path_key
    ]
    if _runtime_checkpoint_path_key(job.get("_current_item_path")) == path_key:
        job.pop("_current_item_path", None)
    _persist_runtime_job_checkpoint(job)


def _processing_db_type(path: Path, category: str) -> str:
    """Map queue routing categories to the DB-facing item label."""
    if category == "tv":
        return "TV Show" if is_season_pack(path) else "TV Episode"
    if category == "movies":
        return "Movies"
    if category == "anime":
        return "Anime"
    if category == "music":
        return "Music"
    if category == "books":
        return "Books"
    if category == "apps":
        return "Apps"
    return "Misc"


def _selected_indexers(
    conf: Any, target_indexer_id: Optional[str], target_indexer_ids: Optional[List[str]]
) -> list[Any]:
    """Return the enabled indexers selected for this item run."""
    from core.registry import get_enabled_indexers

    indexers = get_enabled_indexers(conf)
    if target_indexer_ids:
        allowed_ids = set(target_indexer_ids)
        indexers = [indexer for indexer in indexers if indexer.id in allowed_ids]
    elif target_indexer_id:
        indexers = [indexer for indexer in indexers if indexer.id == target_indexer_id]
    return indexers


def _should_skip_completed_item(
    indexers: list[Any], conf: Any, dest_status: Dict[str, Optional[str]], *, force: bool, name: str
) -> bool:
    """Return True when every enabled destination already has the item."""
    from core.registry import resolve_indexer_enabled

    active_dests_needed = 0
    already_done_count = 0
    for indexer in indexers:
        if not resolve_indexer_enabled(indexer, conf):
            continue
        active_dests_needed += 1
        if dest_status.get(indexer.id) is not None:
            already_done_count += 1

    if force or active_dests_needed == 0 or already_done_count != active_dests_needed:
        logger.debug(
            "[DUPE-CHECK] %s: force=%r  needed=%d  done=%d  => NOT skipping",
            name,
            force,
            active_dests_needed,
            already_done_count,
        )
        return False

    log_verbose(f"Skipping: {name} is already uploaded to all {active_dests_needed} enabled destinations.")
    return True


def _validate_queue_item(
    path: Path,
    *,
    category: str,
    force: bool,
    configured_folders: list[Path],
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
    skip_enabled: bool,
    skip_config: Optional[Dict[Any, Any]],
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None,
    prefetched_base_folder: Optional[Path] = None,
) -> QueueItemValidation:
    """Validate one queue item without blocking the rest of the job."""
    from core.database import check_duplicate_dynamic

    conf = get_config()
    name = path.name
    db_type = _processing_db_type(path, category)

    try:
        item_size_bytes = _live_size_bytes(path)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        reason = f"Validation failed for {name}: unable to read size ({exc})"
        logger.exception(reason)
        return QueueItemValidation("failed", path, category, db_type, message=reason)

    item_size_gb = item_size_bytes / _ONE_GIB
    if path.is_dir() and getattr(conf, "folder_size_limit_enabled", True):
        folder_limit = getattr(conf, "folder_size_limit_gb", 99) or 0
        if folder_limit and item_size_gb > folder_limit:
            if category in {"tv", "anime"}:
                reason = (
                    f"⏩ Folder '{name}' ({item_size_gb:.1f} GB) exceeds {folder_limit} GB limit "
                    "— pack skipped, episodes still queued"
                )
            else:
                reason = (
                    f"⏩ Folder '{name}' ({item_size_gb:.1f} GB) exceeds {folder_limit} GB limit "
                    "— folder upload skipped, contents queued"
                )
            return QueueItemValidation("skipped", path, category, db_type, message=reason, item_size_bytes=item_size_bytes)

    if path.is_file() and getattr(conf, "file_size_limit_enabled", True):
        file_limit = getattr(conf, "file_size_limit_gb", 0) or 0
        if file_limit and item_size_gb > file_limit:
            reason = f"⏩ File '{name}' ({item_size_gb:.1f} GB) exceeds {file_limit} GB limit — skipped"
            return QueueItemValidation("skipped", path, category, db_type, message=reason, item_size_bytes=item_size_bytes)

    if skip_enabled and skip_config and should_skip_file(name, category, cast(Dict[Any, Any], skip_config)):
        reason = f"⏩ '{name}' matches skip pattern — skipped"
        return QueueItemValidation("skipped", path, category, db_type, message=reason, item_size_bytes=item_size_bytes)

    source_root = prefetched_base_folder if prefetched_base_folder is not None else find_configured_root(path, configured_folders)
    all_indexers = _selected_indexers(conf, target_indexer_id, target_indexer_ids)
    if not all_indexers:
        if target_indexer_ids:
            reason = "Selected indexers were not found or are not enabled."
        elif target_indexer_id:
            reason = f"Indexer '{target_indexer_id}' not found or not enabled."
        else:
            reason = "No enabled indexers are available for upload."
        return QueueItemValidation("stopped", path, category, db_type, message=reason, base_folder=source_root)

    indexer_ids = [idx.id for idx in all_indexers]
    if prefetched_dest_status is not None:
        dest_status = {indexer_id: prefetched_dest_status.get(indexer_id) for indexer_id in indexer_ids}
    else:
        key = _build_item_key(path, source_root)
        try:
            dest_status = check_duplicate_dynamic(key, db_type, indexer_ids)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            reason = f"Validation failed for {name}: duplicate check error ({exc})"
            logger.exception(reason)
            return QueueItemValidation("failed", path, category, db_type, message=reason, base_folder=source_root)

    if _should_skip_completed_item(all_indexers, conf, dest_status, force=force, name=name):
        reason = f"⏩ '{name}' already exists on all selected destinations — skipped"
        return QueueItemValidation(
            "skipped",
            path,
            category,
            db_type,
            message=reason,
            base_folder=source_root,
            item_size_bytes=item_size_bytes,
            prefetched_dest_status=dest_status,
        )

    try:
        submission_category = _resolve_submission_category(path, category, db_type)
    except ValueError as exc:
        reason = f"Validation failed for {name}: category detection failed ({exc})"
        logger.error(reason)
        return QueueItemValidation(
            "failed",
            path,
            category,
            db_type,
            message=reason,
            base_folder=source_root,
            item_size_bytes=item_size_bytes,
            prefetched_dest_status=dest_status,
        )

    return QueueItemValidation(
        "ready",
        path,
        category,
        db_type,
        base_folder=source_root,
        item_size_bytes=item_size_bytes,
        prefetched_dest_status=dest_status,
        submission_category=submission_category,
    )


def preview_processing_items(
    items: List[Dict[str, Any]],
    *,
    target_indexer_id: Optional[str] = None,
    target_indexer_ids: Optional[List[str]] = None,
    enable_duplicate_check: bool = True,
    force: Optional[bool] = None,
    test_mode: bool = False,
    skip_packs: bool = False,
    skip_episodes: bool = False,
    detail_limit: int = 500,
) -> Dict[str, Any]:
    """Plan and validate explicit items without creating a job or staging data."""
    from logic.services import UploadService

    conf = get_config()
    configured_folders = get_configured_folders(conf, must_exist=True)
    resolved_force = UploadService._resolve_force_flag(
        enable_duplicate_check=enable_duplicate_check,
        force=force,
        test_mode=test_mode,
    )
    selected_indexers = _selected_indexers(conf, target_indexer_id, target_indexer_ids)
    selected_indexer_ids = [str(indexer.id) for indexer in selected_indexers]

    details: List[Dict[str, Any]] = []
    raw_items: list[tuple[Path, str]] = []
    seen_paths: set[str] = set()
    input_count = len(items)

    def append_detail(
        *,
        path: str,
        name: str,
        category: str,
        outcome: str,
        reason: str,
        size_bytes: int = 0,
        destinations: Optional[Dict[str, Any]] = None,
    ) -> None:
        details.append(
            {
                "path": path,
                "name": name,
                "category": category,
                "outcome": outcome,
                "reason": reason,
                "size_bytes": int(size_bytes or 0),
                "destinations": destinations or {},
            }
        )

    for item in items:
        path_text = str(item.get("path") or "").strip()
        category = str(item.get("category") or item.get("detected_category") or "").strip().lower()
        name = str(item.get("name") or (Path(path_text).name if path_text else "") or "Unknown item")
        if not path_text:
            append_detail(path="", name=name, category=category, outcome="invalid", reason="Missing source path")
            continue
        path = Path(path_text)
        if not path.is_absolute():
            append_detail(
                path=path_text,
                name=name,
                category=category,
                outcome="invalid",
                reason="Source path must be absolute",
            )
            continue
        if not path.exists():
            append_detail(
                path=path_text,
                name=name,
                category=category,
                outcome="invalid",
                reason="Source path was not found",
            )
            continue
        if not category or category in {"all", "both", "mixed", "selected", "external"}:
            append_detail(
                path=str(path),
                name=name,
                category=category,
                outcome="invalid",
                reason="A concrete upload category is required",
            )
            continue
        normalized = _normalize_runtime_path(path)
        if normalized in seen_paths:
            append_detail(
                path=str(path),
                name=name,
                category=category,
                outcome="excluded",
                reason="Duplicate source path in selection",
            )
            continue
        seen_paths.add(normalized)
        raw_items.append((path, category))

    process_tv_episodes = bool(getattr(conf, "process_tv_episodes", True)) and not skip_episodes
    if not process_tv_episodes:
        retained: list[tuple[Path, str]] = []
        for path, category in raw_items:
            if category in {"tv", "anime"} and path.is_file():
                append_detail(
                    path=str(path),
                    name=path.name,
                    category=category,
                    outcome="excluded",
                    reason="Individual episode processing is disabled",
                )
            else:
                retained.append((path, category))
        raw_items = retained

    categories = list(dict.fromkeys(category for _path, category in raw_items))
    sorted_items, _oversized_folders, oversized_files = _plan_sorted_items(
        raw_items,
        categories,
        preserve_explicit_order=True,
        skip_packs=skip_packs,
        folder_limit=int(getattr(conf, "folder_size_limit_gb", 99) or 0),
        folder_enabled=bool(getattr(conf, "folder_size_limit_enabled", True)),
        file_limit=int(getattr(conf, "file_size_limit_gb", 0) or 0),
        file_enabled=bool(getattr(conf, "file_size_limit_enabled", True)),
    )

    planned_keys = {_normalize_runtime_path(path) for path, _category in sorted_items}
    for path, category in raw_items:
        normalized = _normalize_runtime_path(path)
        if normalized not in planned_keys:
            reason = "Pack processing is disabled" if skip_packs and category in {"tv", "anime"} else "Excluded by processing rules"
            append_detail(
                path=str(path),
                name=path.name,
                category=category,
                outcome="excluded",
                reason=reason,
            )

    prefetched_item_db_keys: Dict[str, str] = {}
    prefetched_dupes: Dict[str, Dict[str, Optional[str]]] = {}
    prefetched_source_root_for: Optional[Callable[[Path], Optional[Path]]] = None
    if sorted_items and selected_indexer_ids:
        (
            prefetched_item_db_keys,
            prefetched_dupes,
            prefetched_source_root_for,
        ) = _build_duplicate_prefetch_state(
            conf,
            sorted_items,
            configured_folders,
            target_indexer_id=target_indexer_id,
            target_indexer_ids=target_indexer_ids,
        )

    skip_config = getattr(conf, "skip_files", None)
    skip_enabled = isinstance(skip_config, dict) and bool(skip_config.get("enabled", False))

    for path, category in sorted_items:
        normalized = _normalize_runtime_path(path)
        item_db_key = prefetched_item_db_keys.get(normalized)
        dest_status = prefetched_dupes.get(item_db_key) if item_db_key else None
        base_folder = prefetched_source_root_for(path) if prefetched_source_root_for is not None else None
        validation = _validate_queue_item(
            path,
            category=category,
            force=resolved_force,
            configured_folders=configured_folders,
            target_indexer_id=target_indexer_id,
            target_indexer_ids=target_indexer_ids,
            skip_enabled=skip_enabled,
            skip_config=cast(Optional[Dict[Any, Any]], skip_config),
            prefetched_dest_status=dest_status,
            prefetched_base_folder=base_folder,
        )
        destination_status = {
            indexer_id: {
                "status": "uploaded" if (validation.prefetched_dest_status or {}).get(indexer_id) else "pending",
                "uploaded_at": (validation.prefetched_dest_status or {}).get(indexer_id),
            }
            for indexer_id in selected_indexer_ids
        }
        all_uploaded = bool(destination_status) and all(
            destination["status"] == "uploaded" for destination in destination_status.values()
        )
        if validation.outcome == "ready":
            outcome = "ready"
        elif validation.outcome == "skipped" and all_uploaded and not resolved_force:
            outcome = "duplicate"
        elif validation.outcome == "skipped":
            outcome = "excluded"
        elif validation.outcome == "stopped":
            outcome = "blocked"
        else:
            outcome = "invalid"

        reason = validation.message
        if not reason and normalized in oversized_files:
            reason = "File exceeds the configured size limit"
        append_detail(
            path=str(path),
            name=path.name,
            category=category,
            outcome=outcome,
            reason=reason,
            size_bytes=validation.item_size_bytes,
            destinations=destination_status,
        )

    destination_summary = {
        indexer_id: {"pending": 0, "uploaded": 0}
        for indexer_id in selected_indexer_ids
    }
    outcome_counts = {
        "ready": 0,
        "duplicate": 0,
        "excluded": 0,
        "invalid": 0,
        "blocked": 0,
    }
    ready_bytes = 0
    total_bytes = 0
    partial_duplicates = 0
    for detail in details:
        outcome = str(detail["outcome"])
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
        size_bytes = int(detail.get("size_bytes") or 0)
        total_bytes += size_bytes
        if outcome == "ready":
            ready_bytes += size_bytes
        destination_values = list((detail.get("destinations") or {}).values())
        uploaded_destinations = sum(1 for destination in destination_values if destination.get("status") == "uploaded")
        if outcome == "ready" and 0 < uploaded_destinations < len(destination_values):
            partial_duplicates += 1
        for indexer_id, destination in (detail.get("destinations") or {}).items():
            status = str(destination.get("status") or "pending")
            destination_summary.setdefault(indexer_id, {"pending": 0, "uploaded": 0})
            destination_summary[indexer_id][status] = destination_summary[indexer_id].get(status, 0) + 1

    return {
        "status": "preview",
        "summary": {
            "selected": input_count,
            "planned": len(sorted_items),
            **outcome_counts,
            "partial_duplicates": partial_duplicates,
            "total_bytes": total_bytes,
            "ready_bytes": ready_bytes,
        },
        "destinations": destination_summary,
        "items": details[: max(1, int(detail_limit))],
        "details_truncated": len(details) > max(1, int(detail_limit)),
        "duplicate_check_bypassed": resolved_force,
    }


def _run_validation_with_timeout(
    path: Path,
    *,
    category: str,
    force: bool,
    configured_folders: list[Path],
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
    skip_enabled: bool,
    skip_config: Optional[Dict[Any, Any]],
    timeout_s: float = _ITEM_VALIDATION_TIMEOUT_SECONDS,
    runtime_job: Optional[dict[str, Any]] = None,
    validation_worker: Optional[Callable[..., QueueItemValidation]] = None,
    isolate_with_process: Optional[bool] = None,
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None,
    prefetched_base_folder: Optional[Path] = None,
) -> QueueItemValidation:
    """Run item validation with per-item isolation and a timeout guard."""

    if isolate_with_process is None:
        env_flag = os.getenv("NZBPOSTARR_VALIDATE_ISOLATE", "").strip().lower()
        if env_flag:
            isolate_with_process = env_flag not in {"0", "false", "no", "off"}
        else:
            # A timed-out Python thread cannot be killed and may continue
            # mutating job or filesystem state after failure is reported.
            # Production validation therefore runs in a process that can be
            # terminated. Tests and explicit injected workers retain the
            # lightweight thread path.
            isolate_with_process = validation_worker is None

    def runner() -> QueueItemValidation:
        if runtime_job is not None:
            set_thread_job(runtime_job)
        worker = validation_worker or _validate_queue_item
        return worker(
            path,
            category=category,
            force=force,
            configured_folders=configured_folders,
            target_indexer_id=target_indexer_id,
            target_indexer_ids=target_indexer_ids,
            skip_enabled=skip_enabled,
            skip_config=skip_config,
            prefetched_dest_status=prefetched_dest_status,
            prefetched_base_folder=prefetched_base_folder,
        )

    if isolate_with_process:
        result_queue = None
        process = None
        try:
            ctx = mp.get_context("spawn")
            result_queue = ctx.Queue(maxsize=1)
            process = ctx.Process(
                target=_validation_worker_entry,
                kwargs={
                    "result_queue": result_queue,
                    "path_text": str(path),
                    "category": category,
                    "force": force,
                    "configured_folder_paths": [str(folder) for folder in configured_folders],
                    "target_indexer_id": target_indexer_id,
                    "target_indexer_ids": list(target_indexer_ids) if target_indexer_ids else None,
                    "skip_enabled": skip_enabled,
                    "skip_config": skip_config,
                    "prefetched_dest_status": prefetched_dest_status,
                    "prefetched_base_folder_path": str(prefetched_base_folder) if prefetched_base_folder else None,
                },
                daemon=True,
            )
            process.start()
            process.join(timeout_s)
            if process.is_alive():
                reason = f"Validation timed out after {int(timeout_s)}s for {path.name}"
                logger.error(reason)
                process.terminate()
                process.join(timeout=5.0)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=1.0)
                return QueueItemValidation(
                    "failed",
                    path,
                    category,
                    _processing_db_type(path, category),
                    message=reason,
                )

            try:
                status, payload = result_queue.get_nowait()
            except stdlib_queue.Empty:
                reason = (
                    f"Validation worker exited without returning a result for {path.name}"
                    if process.exitcode == 0
                    else f"Validation worker exited with code {process.exitcode} for {path.name}"
                )
                logger.error(reason)
                return QueueItemValidation(
                    "failed",
                    path,
                    category,
                    _processing_db_type(path, category),
                    message=reason,
                )

            if status == "ok":
                return cast(QueueItemValidation, payload)

            reason = str(payload or f"Validation crashed for {path.name}")
            logger.error(reason)
            return QueueItemValidation(
                "failed",
                path,
                category,
                _processing_db_type(path, category),
                message=reason,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            reason = f"Validation crashed for {path.name}: {exc}"
            logger.exception(reason)
            return QueueItemValidation(
                "failed",
                path,
                category,
                _processing_db_type(path, category),
                message=reason,
            )
        finally:
            if result_queue is not None:
                try:
                    result_queue.close()
                    result_queue.join_thread()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            if process is not None and process.is_alive():
                try:
                    process.terminate()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(runner)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeoutError:
        future.cancel()
        reason = f"Validation timed out after {int(timeout_s)}s for {path.name}"
        logger.error(reason)
        return QueueItemValidation(
            "failed",
            path,
            category,
            _processing_db_type(path, category),
            message=reason,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        reason = f"Validation crashed for {path.name}: {exc}"
        logger.exception(reason)
        return QueueItemValidation(
            "failed",
            path,
            category,
            _processing_db_type(path, category),
            message=reason,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_validated_upload(
    validation: QueueItemValidation,
    *,
    force: bool,
    test_mode: bool,
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
    runtime_job: Optional[dict[str, Any]] = None,
) -> int:
    """Upload a validated item in the background worker."""
    if runtime_job is not None:
        set_thread_job(runtime_job)
    return process_single(
        validation.path,
        itype=validation.db_type,
        category=validation.category,
        force=force,
        base_folder=validation.base_folder,
        test_mode=test_mode,
        target_indexer_id=target_indexer_id,
        target_indexer_ids=target_indexer_ids,
        prefetched_dest_status=validation.prefetched_dest_status,
        validated_item_size_bytes=validation.item_size_bytes,
        validated_submission_category=validation.submission_category,
    )


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
    """Map logical upload sets to concrete server executions."""
    server_to_sets: dict[tuple[str, bool], tuple[Any, list[str], list[str]]] = {}
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

        group_key = (server.name, is_priority)
        if group_key not in server_to_sets:
            server_to_sets[group_key] = (server, [], [])
        server_to_sets[group_key][1].extend(needed_dests)
        server_to_sets[group_key][2].append(upload_set["id"])

    raw_sets: list[tuple[dict[str, Any], Any]] = []
    for (_, is_priority), (server, dests, ids) in server_to_sets.items():
        raw_sets.append(
            (
                {
                    "id": "/".join(dict.fromkeys(ids)) + (" (P)" if is_priority else " (NP)"),
                    "dests": list(dict.fromkeys(dests)),
                    "backbone": server.backbone[0] if server.backbone else "Unknown",
                    "priority": is_priority,
                },
                server,
            )
        )

    raw_sets.sort(key=lambda value: value[0].get("priority", False), reverse=True)
    return raw_sets


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


def _resolve_support_assets(
    path: Path,
    conf: Any,
    name: str,
    *,
    support_scan: Optional[SupportAssetScan] = None,
) -> tuple[Optional[Path], Optional[Path]]:
    """Resolve optional sidecar assets submitted alongside the NZB."""
    nfo_path = support_scan.nfo_path if support_scan is not None else _find_nfo_path(path)
    if nfo_path:
        log_verbose(f"Found NFO for {name}: {nfo_path.name}")

    mediainfo_path = _find_mediainfo_path(path, conf)
    if mediainfo_path:
        log_verbose(f"Found Mediainfo for {name}: {mediainfo_path.name}")

    return nfo_path, mediainfo_path


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


def _submit_api_batch(
    upload_set: dict[str, Any],
    *,
    conf: Any,
    name: str,
    priority_label: str,
    unique_nzb: Path,
    submission_category: str,
    nfo_path: Optional[Path],
    mediainfo_path: Optional[Path],
) -> list[tuple[str, bool, str]]:
    """Submit an uploaded NZB to one or more indexers."""

    def submit_one(dest_id: str) -> tuple[str, bool, str]:
        result = submit_api(
            f"{priority_label}{name}",
            dest_id,
            conf,
            nzb_path=unique_nzb,
            cat=submission_category,
            nfo_path=nfo_path,
            mediainfo_path=mediainfo_path,
        )
        return dest_id, result.success, result.reason

    dests = upload_set["dests"]
    if len(dests) == 1:
        try:
            return [submit_one(dests[0])]
        except Exception as exc:
            reason = f"Unhandled submission exception for indexer '{dests[0]}': {exc}"
            logger.exception(reason)
            return [(dests[0], False, reason)]

    results: dict[str, tuple[str, bool, str]] = {}
    with ThreadPoolExecutor(max_workers=len(dests)) as api_pool:
        futures = {api_pool.submit(submit_one, dest_id): dest_id for dest_id in dests}
        for future in as_completed(futures):
            dest_id = futures[future]
            try:
                results[dest_id] = future.result()
            except Exception as exc:
                reason = f"Unhandled submission exception for indexer '{dest_id}': {exc}"
                logger.exception(reason)
                results[dest_id] = (dest_id, False, reason)
    return [results[dest_id] for dest_id in dests]


def _persist_submission_results(
    api_results: list[tuple[str, bool, str]],
    *,
    name: str,
    item_size: int,
    key: str,
    itype: str,
    item_path: Path,
    base_folder: Optional[Path],
    category: str,
    test_mode: bool,
    upload_result: dict[str, Any],
) -> bool:
    """Persist per-indexer submission results and return whether any succeeded."""
    any_success = False
    for dest_id, ok, reason in api_results:
        if ok:
            logger.info(f"[INDEXER] {dest_id} accepted '{name}'")
            any_success = True
            if not test_mode and item_size > 0:
                update_db_destination(dest_id, name, item_size, key, itype=itype, **upload_result)
                _record_folder_hierarchy_rows(
                    item_path,
                    base_folder=base_folder,
                    category=category,
                    dest_id=dest_id,
                    upload_result=upload_result,
                )
            continue

        logger.error(f"[INDEXER] {dest_id} failed '{name}': {reason or 'unknown error'}")
        if not test_mode and item_size > 0:
            update_db_destination(
                dest_id,
                name,
                item_size,
                key,
                itype=itype,
                status="failed",
                error=reason or "Indexer submission rejected or unreachable",
            )
    return any_success


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

    priority_label = "Priority " if upload_set.get("priority") else ""
    target_display = _upload_target_display(upload_set)
    log_info(
        f"--- [UPLOAD] Targeting: {target_display} "
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

        api_results = _submit_api_batch(
            upload_set,
            conf=context.conf,
            name=context.name,
            priority_label=priority_label,
            unique_nzb=unique_nzb,
            submission_category=context.submission_category,
            nfo_path=context.nfo_path,
            mediainfo_path=context.mediainfo_path,
        )
        run_success = _persist_submission_results(
            api_results,
            name=context.name,
            item_size=context.item_size,
            key=context.key,
            itype=context.itype,
            item_path=context.path,
            base_folder=context.base_folder,
            category=context.category,
            test_mode=context.test_mode,
            upload_result=upload_result,
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


def process_single(
    path: Path,
    force: bool = False,
    itype: str = "Misc",
    category: str = "misc",
    base_folder: Optional[Path] = None,
    test_mode: bool = False,
    target_indexer_id: Optional[str] = None,
    target_indexer_ids: Optional[List[str]] = None,
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None,
    validated_item_size_bytes: Optional[int] = None,
    validated_submission_category: Optional[str] = None,
) -> int:
    """Process a single item (orchestrated for dual uploads)."""
    from core.database import check_duplicate_dynamic

    conf = get_config()
    name = path.name
    job = get_thread_job()

    if job and not wait_for_job_resume(job):
        return 2

    # ── Size-limit guard (applies to manually queued items too) ──
    item_size_bytes = validated_item_size_bytes if validated_item_size_bytes is not None else _live_size_bytes(path)
    item_size_gb = item_size_bytes / _ONE_GIB

    if path.is_dir() and getattr(conf, "folder_size_limit_enabled", True):
        folder_limit = getattr(conf, "folder_size_limit_gb", 99) or 0
        if folder_limit and item_size_gb > folder_limit:
            log_info(
                f"REJECTED: '{name}' ({item_size_gb:.1f} GB) exceeds folder size limit of {folder_limit} GB",
                "ERROR",
            )
            return 1  # treat as skip

    if path.is_file() and getattr(conf, "file_size_limit_enabled", True):
        file_limit = getattr(conf, "file_size_limit_gb", 0) or 0
        if file_limit and item_size_gb > file_limit:
            log_info(
                f"REJECTED: '{name}' ({item_size_gb:.1f} GB) exceeds file size limit of {file_limit} GB",
                "ERROR",
            )
            return 1  # treat as skip

    all_indexers = _selected_indexers(conf, target_indexer_id, target_indexer_ids)
    if not all_indexers:
        if target_indexer_ids:
            log_info("Selected indexers were not found or are not enabled.")
        elif target_indexer_id:
            log_info(f"Indexer '{target_indexer_id}' not found or not enabled.")
        return 2

    indexer_ids = [idx.id for idx in all_indexers]
    indexer_map = {idx.id: idx for idx in all_indexers}

    key = _build_item_key(path, base_folder)

    if prefetched_dest_status is None:
        dest_status = check_duplicate_dynamic(key, itype, indexer_ids)
    else:
        dest_status = {idx: prefetched_dest_status.get(idx) for idx in indexer_ids}
    is_new = all(v is None for v in dest_status.values())
    global_backfill = conf.enable_backfill

    if _should_skip_completed_item(all_indexers, conf, dest_status, force=force, name=name):
        return 1

    if job:
        job["item_percents"] = {}
        job["item_speeds"] = {}
        job["item_percent"] = 0
        job["current_item"] = name

    all_servers = [s for s in (conf.nntp_servers or []) if getattr(s, "enabled", True)]
    if not all_servers:
        log_info("No configured servers found, skipping upload.", "ERROR")
        return 2

    item_size = item_size_bytes
    raw_sets = _plan_upload_runs(
        _build_upload_sets(all_indexers, conf),
        all_servers=all_servers,
        dest_status=dest_status,
        indexer_map=indexer_map,
        force=force,
        is_new=is_new,
        global_backfill=global_backfill,
    )

    if not raw_sets:
        reason = (
            "no enabled indexers with valid API keys"
            if not all_indexers
            else "all destinations already handled (and force=False)"
        )
        log_info(f"Skipping: {name} - {reason}.")
        return 1

    sets_to_run = _split_parallel_server_connections(raw_sets)

    upload_state = _SingleUploadState()
    try:
        submission_category = validated_submission_category or _resolve_submission_category(path, category, itype)
    except ValueError as exc:
        logger.error(f"Category detection failed for {name}: category={category} type={itype} error={exc}")
        log_info("Category detection failed - upload stopped", "ERROR")
        return 3
    log_info(f"Detected Category: {_submission_category_label(submission_category)}")
    support_scan = _scan_item_support_assets(path) if path.is_dir() else None

    try:
        if prepare_item(path, name, itype, support_scan=support_scan, total_bytes=item_size_bytes):
            nfo_path, mediainfo_path = _resolve_support_assets(path, conf, name, support_scan=support_scan)
            if job and not wait_for_job_resume(job):
                return 2
            if job and job.get("stop_requested"):
                return 2
            upload_context = _SingleUploadContext(
                conf=conf,
                name=name,
                path=path,
                category=category,
                itype=itype,
                base_folder=base_folder,
                test_mode=test_mode,
                item_size=item_size,
                job=job,
                submission_category=submission_category,
                nfo_path=nfo_path,
                mediainfo_path=mediainfo_path,
                key=key,
            )
            with ThreadPoolExecutor(max_workers=len(sets_to_run)) as executor:
                tasks = [
                    executor.submit(
                        _run_single_upload_flow,
                        upload_set,
                        upload_server,
                        context=upload_context,
                        state=upload_state,
                    )
                    for upload_set, upload_server in sets_to_run
                ]
                for task in tasks:
                    task.result()
            if upload_state.errors and not upload_state.any_success:
                log_info(
                    f"All upload targets failed for {name}: {'; '.join(upload_state.errors[:3])}"
                    f"{' ...' if len(upload_state.errors) > 3 else ''}",
                    "ERROR",
                )
            if job and not wait_for_job_resume(job):
                return 2
            if job and job.get("stop_requested"):
                return 2
            return 0 if upload_state.any_success else 4  # 4=upload ok, no indexer accepted

        if job and job.get("stop_requested"):
            return 2
        return 3
    finally:
        prepared_tmp = None
        if job is not None:
            prepared_tmp = job.pop("_current_prepare_tmp", None)
        standard_tmp = conf.tmp_sub / name
        if prepared_tmp and Path(str(prepared_tmp)) != standard_tmp:
            shutil.rmtree(Path(str(prepared_tmp)), ignore_errors=True)
        purge_item_data(name)


def _resolve_job_categories(category: str) -> list[str]:
    from core.registry import get_available_categories

    category_lower = category.lower()
    active_categories = [item["id"] for item in get_available_categories()]
    if category_lower == "all":
        return active_categories or ["movies", "tv", "misc"]
    if category_lower == "both":
        return [item for item in active_categories if item in ("movies", "tv")] or ["movies", "tv"]
    if category_lower in {"mixed", "selected"}:
        return active_categories or ["movies", "tv", "anime", "misc"]
    return [category_lower]


def _build_item_hint_map(item_hints: Optional[List[Dict[str, Any]]]) -> dict[str, Dict[str, Any]]:
    hints: dict[str, Dict[str, Any]] = {}
    for item in item_hints or []:
        raw_path = item.get("path")
        if raw_path:
            hints[_normalize_runtime_path(Path(str(raw_path)))] = dict(item)
    return hints


def _append_targeted_resolution_items(
    raw_items: list[tuple[Path, str]],
    *,
    selected_path: Path,
    resolution: Any,
    conf: Any,
    runtime_job: Optional[dict[str, Any]],
    process_tv_episodes: bool,
    staged_pack_source_dirs: set[str],
) -> None:
    resolved_paths = list(resolution.queue_paths)
    episode_paths_added_with_packs: set[str] = set()
    skip_parent_series_folder = False

    if (
        resolution.category in {"tv", "anime"}
        and selected_path.is_dir()
        and resolved_paths
        and any(resolved_path.is_file() for resolved_path in resolved_paths)
    ):
        pack_entries, episode_paths_added_with_packs = _create_filtered_tv_pack_entries_for_selection(
            selected_path,
            resolved_paths,
            conf,
            runtime_job,
        )
        skip_parent_series_folder = bool(pack_entries)
        if pack_entries:
            staged_pack_source_dirs.add(_normalize_runtime_path(selected_path))
        for staged_pack, pack_episode_paths in pack_entries:
            raw_items.append((staged_pack, resolution.category))
            if process_tv_episodes:
                raw_items.extend((episode_path, resolution.category) for episode_path in pack_episode_paths)

    selected_key = _normalize_runtime_path(selected_path)
    for resolved_path in resolved_paths:
        if (
            skip_parent_series_folder
            and resolved_path.is_dir()
            and _normalize_runtime_path(resolved_path) == selected_key
        ):
            continue
        if resolution.category in {"tv", "anime"} and not process_tv_episodes and resolved_path.is_file():
            continue
        if _normalize_runtime_path(resolved_path) in episode_paths_added_with_packs:
            continue
        raw_items.append((resolved_path, resolution.category))


def _collect_targeted_job_items(
    *,
    paths: List[str],
    item_hints: Optional[List[Dict[str, Any]]],
    category: str,
    conf: Any,
    runtime_job: Optional[dict[str, Any]],
    process_tv_episodes: bool,
) -> list[tuple[Path, str]]:
    log_info(f"Targeted upload: {len(paths)} item(s)")
    raw_items: list[tuple[Path, str]] = []
    staged_pack_source_dirs: set[str] = set()
    item_hint_map = _build_item_hint_map(item_hints)

    for raw_target in paths:
        selected_path = _resolve_targeted_path(raw_target)
        if not selected_path:
            log_info(f"⏩ Non-absolute targeted path rejected: {raw_target}", "WARN")
            continue
        if not selected_path.exists():
            log_info(f"⏩ Missing path: {selected_path} (skipped)", "WARN")
            continue
        if selected_path.name.startswith("."):
            continue

        hint = item_hint_map.get(_normalize_runtime_path(selected_path), {})
        category_hint = str(hint.get("category") or category or "")
        itype_hint = str(hint.get("itype") or "")
        anime_lookup = (
            _processing_cached_anime_lookup
            if _should_skip_live_anime_lookup_for_targeted_item(category_hint, itype_hint)
            else _processing_anime_lookup
        )
        resolution = resolve_explicit_path(
            selected_path,
            category_hint=category_hint,
            itype_hint=itype_hint,
            anime_lookup=anime_lookup,
        )
        _log_explicit_resolution(resolution)
        _append_targeted_resolution_items(
            raw_items,
            selected_path=selected_path,
            resolution=resolution,
            conf=conf,
            runtime_job=runtime_job,
            process_tv_episodes=process_tv_episodes,
            staged_pack_source_dirs=staged_pack_source_dirs,
        )

    return _inject_inferred_tv_pack_entries(raw_items, conf, runtime_job, staged_pack_source_dirs)


def _collect_scanned_job_items(
    conf: Any,
    categories: list[str],
    *,
    process_tv_episodes: bool,
) -> list[tuple[Path, str]]:
    raw_items = []
    for scan_item in scan_configured_items(conf, must_exist=True):
        if scan_item.category not in categories:
            continue
        if scan_item.category in {"tv", "anime"} and not process_tv_episodes and scan_item.path.is_file():
            continue
        raw_items.append((scan_item.path, scan_item.category))
    return raw_items


@dataclass
class _JobRunState:
    """Mutable counters and the single in-flight upload for one processing run."""

    total: int
    effective_limit: Optional[int]
    test_mode: bool
    success_count: int = 0
    duplicate_count: int = 0
    failure_count: int = 0
    completed_count: int = 0
    active_upload_future: Any = None
    active_upload_validation: Optional[QueueItemValidation] = None
    stop_processing: bool = False
    limit_reached: bool = False

    def publish_counts(self) -> None:
        update_job_progress(
            processed=self.completed_count,
            total=self.total,
            percent=int((self.completed_count / self.total) * 100) if self.total else 0,
            skipped=self.duplicate_count,
        )

    def complete_active_upload(self, *, wait: bool) -> None:
        if self.active_upload_future is None or self.active_upload_validation is None:
            return
        if not wait and not self.active_upload_future.done():
            return

        result = self.active_upload_future.result()
        current_job = get_thread_job()
        if current_job is not None and result != 2:
            _complete_runtime_item_checkpoint(current_job, self.active_upload_validation.path)

        if result == 0:
            self.success_count += 1
            self.completed_count += 1
            prefix = "[TEST] " if self.test_mode else ""
            log_completed(
                f"{prefix}COMPLETED: {self.active_upload_validation.path.name} "
                f"({self.success_count}/{self.effective_limit or 'All'})"
            )
            self.publish_counts()
            if self.effective_limit and self.success_count >= self.effective_limit:
                log_success(f"Target limit reached: {self.success_count} uploads.")
                self.limit_reached = True
        elif result == 1:
            self.duplicate_count += 1
            self.completed_count += 1
            self.publish_counts()
        elif result == 2:
            self.stop_processing = True
        elif result in (3, 4):
            self.failure_count += 1
            self.completed_count += 1
            self.publish_counts()

        self.active_upload_future = None
        self.active_upload_validation = None


@dataclass(frozen=True)
class _JobExecutionContext:
    force: bool
    test_mode: bool
    target_indexer_id: Optional[str]
    target_indexer_ids: Optional[List[str]]
    configured_folders: List[Any]
    skip_enabled: bool
    skip_config: Optional[Dict[Any, Any]]
    prefetched_item_db_keys: Dict[str, str]
    prefetched_dupes: Dict[str, Dict[str, Optional[str]]]
    prefetched_source_root_for: Optional[Callable[[Path], Optional[Path]]]


def _prefetched_validation_state(
    context: _JobExecutionContext,
    item: Path,
) -> tuple[Optional[Dict[str, Optional[str]]], Optional[Path]]:
    destination_status = None
    normalized_item = _normalize_runtime_path(item)
    if context.prefetched_item_db_keys:
        item_db_key = context.prefetched_item_db_keys.get(normalized_item)
        if item_db_key:
            destination_status = context.prefetched_dupes.get(item_db_key)

    base_folder = context.prefetched_source_root_for(item) if context.prefetched_source_root_for else None
    return destination_status, base_folder


def _validate_execution_item(
    item: Path,
    category: str,
    current_job: Optional[dict[str, Any]],
    context: _JobExecutionContext,
) -> QueueItemValidation:
    prefetched_dest_status, prefetched_base_folder = _prefetched_validation_state(context, item)

    # Batch duplicate state is already available in the parent process. Avoid
    # launching/importing an isolated Python worker—and avoid walking the item
    # tree—when every selected destination is known to be complete.
    if prefetched_dest_status is not None:
        conf = get_config()
        selected_indexers = _selected_indexers(
            conf,
            context.target_indexer_id,
            context.target_indexer_ids,
        )
        if selected_indexers and _should_skip_completed_item(
            selected_indexers,
            conf,
            prefetched_dest_status,
            force=context.force,
            name=item.name,
        ):
            return QueueItemValidation(
                "skipped",
                item,
                category,
                _processing_db_type(item, category),
                message=f"⏩ '{item.name}' already exists on all selected destinations — skipped",
                base_folder=prefetched_base_folder,
                prefetched_dest_status=prefetched_dest_status,
            )

    return _run_validation_with_timeout(
        item,
        category=category,
        force=context.force,
        configured_folders=context.configured_folders,
        target_indexer_id=context.target_indexer_id,
        target_indexer_ids=context.target_indexer_ids,
        skip_enabled=context.skip_enabled,
        skip_config=context.skip_config,
        runtime_job=current_job,
        prefetched_dest_status=prefetched_dest_status,
        prefetched_base_folder=prefetched_base_folder,
    )


def _handle_nonready_validation(
    validation: QueueItemValidation,
    checkpoint_path: Path,
    current_job: Optional[dict[str, Any]],
    run_state: _JobRunState,
) -> Optional[bool]:
    """Handle non-ready validation outcomes; None means the item is ready to upload."""
    if validation.outcome == "stopped":
        message = validation.message or "Job stopped during validation."
        requested_stop = bool(current_job and current_job.get("stop_requested"))
        log_info(message, "WARNING" if requested_stop else "ERROR")
        update_job_progress(
            msg=message,
            status="stopped" if requested_stop else "failed",
        )
        return False

    if validation.outcome == "skipped":
        log_info(validation.message)
        run_state.duplicate_count += 1
    elif validation.outcome == "failed":
        log_info(validation.message or f"Validation failed for {validation.path.name}", "ERROR")
        run_state.failure_count += 1
    else:
        return None

    run_state.completed_count += 1
    _complete_runtime_item_checkpoint(current_job, checkpoint_path)
    run_state.publish_counts()
    return True


def _execute_job_queue(
    sorted_items: list[tuple[Path, str]],
    *,
    preserve_explicit_order: bool,
    runtime_job: Optional[dict[str, Any]],
    paths: Optional[List[str]],
    context: _JobExecutionContext,
    run_state: _JobRunState,
) -> bool:
    """Validate and upload one item ahead; return False when the run stops early."""
    with ThreadPoolExecutor(max_workers=1) as upload_executor:
        for _index, (item, category) in _iter_work_items(
            sorted_items,
            preserve_explicit_order=preserve_explicit_order,
            runtime_job=runtime_job,
            paths=paths,
        ):
            run_state.complete_active_upload(wait=False)
            if run_state.stop_processing or run_state.limit_reached:
                break

            current_job = get_thread_job()
            if current_job and not wait_for_job_resume(current_job):
                log_info("Job stopped by user.")
                update_job_progress(status="stopped")
                return False
            if current_job and current_job.get("stop_requested"):
                log_info("Job stopped by user.")
                update_job_progress(status="stopped")
                return False

            if current_job is not None:
                _begin_runtime_item_checkpoint(
                    current_job,
                    item,
                    make_current=run_state.active_upload_future is None,
                )
            if current_job is not None and run_state.active_upload_future is None:
                current_job["current_stage"] = "VALIDATING ITEM"
                update_job_progress(
                    msg=f"Validating {item.name} ({run_state.completed_count + 1}/{run_state.total})",
                    processed=run_state.completed_count,
                    total=run_state.total,
                    percent=int((run_state.completed_count / run_state.total) * 100) if run_state.total else 0,
                    skipped=run_state.duplicate_count,
                )

            validation = _validate_execution_item(item, category, current_job, context)
            handled = _handle_nonready_validation(validation, item, current_job, run_state)
            if handled is False:
                return False
            if handled is True:
                continue

            if run_state.active_upload_future is not None:
                run_state.complete_active_upload(wait=True)
                if run_state.stop_processing:
                    return False
                if run_state.limit_reached:
                    break

            if current_job is not None:
                current_job["_current_item_path"] = str(validation.path)
                _persist_runtime_job_checkpoint(current_job)

            run_state.active_upload_validation = validation
            run_state.active_upload_future = upload_executor.submit(
                _run_validated_upload,
                validation,
                force=context.force,
                test_mode=context.test_mode,
                target_indexer_id=context.target_indexer_id,
                target_indexer_ids=context.target_indexer_ids,
                runtime_job=current_job,
            )

        if (
            run_state.active_upload_future is not None
            and not run_state.stop_processing
            and not run_state.limit_reached
        ):
            run_state.complete_active_upload(wait=True)
            if run_state.stop_processing:
                return False

    return True


def run_job(
    category: str,
    limit: Optional[int] = None,
    skip_packs: bool = False,
    skip_episodes: bool = False,
    force: bool = False,
    test_mode: bool = False,
    target_indexer_id: Optional[str] = None,
    target_indexer_ids: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
    item_hints: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Run a batch processing job."""
    conf = get_config()
    if not check_tools(conf):
        return

    runtime_job = get_thread_job()
    if runtime_job:
        runtime_job["test_mode"] = test_mode

    log_info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log_info(f"🚀 Starting job: {category.upper()}")
    log_info(
        f"⚙️  Settings: [Test Mode: {test_mode}] [Skip Dupes: {force}] "
        f"[Skip Packs: {skip_packs}] [Skip Episodes: {skip_episodes}] "
        f"[Target: {limit or 'All'}]"
    )
    log_info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    cat_lower = category.lower()
    cats = _resolve_job_categories(category)
    configured_folders = get_configured_folders(conf, must_exist=True)
    process_tv_episodes = bool(getattr(conf, "process_tv_episodes", True))
    if skip_episodes:
        process_tv_episodes = False

    if not process_tv_episodes and "tv" in cats:
        log_info("⏩ Skipping TV — episodes disabled in settings")
        cats.remove("tv")

    # If explicit paths were provided (e.g. from Pending -> Force Upload),
    # only process those paths and skip full folder scans.
    if paths:
        if cat_lower not in cats and cat_lower not in {"all", "both", "mixed", "selected"}:
            # Respect the processing filters from settings even in targeted mode.
            log_info(f"⏩ Skipping {cat_lower.upper()} — disabled in settings")
            return

        raw_items = _collect_targeted_job_items(
            paths=paths,
            item_hints=item_hints,
            category=category,
            conf=conf,
            runtime_job=runtime_job,
            process_tv_episodes=process_tv_episodes,
        )
    else:
        raw_items = _collect_scanned_job_items(
            conf,
            cats,
            process_tv_episodes=process_tv_episodes,
        )

    if not raw_items:
        if paths:
            selected_count = len(paths)
            reason = (
                f"No valid items remained after filtering: selected={selected_count} "
                f"filtered=0 final_queued=0"
            )
            logger.error(f"[QUEUE-RUN] {reason}")
            update_job_progress(msg=reason, status="failed", total=selected_count, processed=0, percent=0)
            raise ValueError(reason)
        log_info(f"No items found to process for category: {category}")
        return

    # Size limits from config
    folder_limit = getattr(conf, "folder_size_limit_gb", 99)
    folder_enabled = getattr(conf, "folder_size_limit_enabled", True)
    file_limit = getattr(conf, "file_size_limit_gb", 0)
    file_enabled = getattr(conf, "file_size_limit_enabled", True)

    preserve_explicit_order = bool(paths)

    sorted_items, _oversized_folders, _oversized_files = _plan_sorted_items(
        raw_items,
        cats,
        preserve_explicit_order=preserve_explicit_order,
        skip_packs=skip_packs,
        folder_limit=folder_limit,
        folder_enabled=folder_enabled,
        file_limit=file_limit,
        file_enabled=file_enabled,
    )
    if preserve_explicit_order and runtime_job is not None:
        runtime_job["target_paths"] = [str(path) for path, _cat in sorted_items]

    effective_limit = limit
    if not effective_limit:
        configured_limit = getattr(conf, "item_limit_per_category", None)
        if configured_limit:
            effective_limit = int(configured_limit)

    total = len(sorted_items)
    if paths:
        logger.info(
            f"[QUEUE-RUN] selected={len(paths)} filtered={len(raw_items)} final_queued={total}"
        )
    if paths and total == 0:
        reason = (
            f"No valid items remained after queue validation: selected={len(paths)} "
            f"filtered={len(raw_items)} final_queued=0"
        )
        logger.error(f"[QUEUE-RUN] {reason}")
        update_job_progress(msg=reason, status="failed", total=len(paths), processed=0, percent=0)
        raise ValueError(reason)
    update_job_progress(total=total, processed=0, percent=0)
    if effective_limit and not limit:
        log_info(f"Target Limit: Job will stop after {effective_limit} success(es).")

    log_info(f"🔍 Queue Discovery: found {total} items in total.")
    if limit:
        log_info(f"🎯 Target Limit: Job will stop after {limit} success(es).")

    log_info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log_info(f"⚡ Processing queue: {total} items to check.")
    log_info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Skip files config
    skip_config = getattr(conf, "skip_files", None)
    skip_enabled = isinstance(skip_config, dict) and skip_config.get("enabled", False)
    prefetched_item_db_keys: Dict[str, str] = {}
    prefetched_dupes: Dict[str, Dict[str, Optional[str]]] = {}
    prefetched_source_root_for: Optional[Callable[[Path], Optional[Path]]] = None

    if preserve_explicit_order and sorted_items:
        (
            prefetched_item_db_keys,
            prefetched_dupes,
            prefetched_source_root_for,
        ) = _build_duplicate_prefetch_state(
            conf,
            sorted_items,
            configured_folders,
            target_indexer_id=target_indexer_id,
            target_indexer_ids=target_indexer_ids,
        )

    run_state = _JobRunState(
        total=total,
        effective_limit=effective_limit,
        test_mode=test_mode,
    )
    execution_context = _JobExecutionContext(
        force=force,
        test_mode=test_mode,
        target_indexer_id=target_indexer_id,
        target_indexer_ids=target_indexer_ids,
        configured_folders=configured_folders,
        skip_enabled=skip_enabled,
        skip_config=cast(Optional[Dict[Any, Any]], skip_config),
        prefetched_item_db_keys=prefetched_item_db_keys,
        prefetched_dupes=prefetched_dupes,
        prefetched_source_root_for=prefetched_source_root_for,
    )
    if not _execute_job_queue(
        sorted_items,
        preserve_explicit_order=preserve_explicit_order,
        runtime_job=runtime_job,
        paths=paths,
        context=execution_context,
        run_state=run_state,
    ):
        return

    update_job_progress(
        processed=run_state.completed_count,
        percent=100,
        skipped=run_state.duplicate_count,
    )

    from core.database import get_all_upload_stats

    stats = get_all_upload_stats() or {}
    log_completed("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    mode_str = " (TEST RUN)" if test_mode else ""
    log_completed(f"🏁 Job Finished: {category.upper()}{mode_str}")
    log_completed(
        f"✅ Success: {run_state.success_count} | "
        f"⏩ Skipped: {run_state.duplicate_count} | "
        f"❌ Failed: {run_state.failure_count}"
    )

    if stats:
        # Build dynamic totals line from ALL enabled/active indexers
        active_stats = []
        from core.registry import get_enabled_indexers

        for idx in get_enabled_indexers(get_config()):
            # Fetch count from stats dict (keys are format {idx_id}_count)
            count = stats.get(f"{idx.id}_count", 0)
            active_stats.append(f"{idx.name}: {count}")

        if active_stats:
            log_completed(f"📈 Indexer Totals: {' | '.join(active_stats)}")

    log_completed("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


def kill_child_processes(include_running: bool = False) -> None:
    """Kill orphaned / hung tool processes (nyuu, rar, parpar).

    Args:
        include_running: If True, also kills processes that belong to
                         active upload jobs (nuclear option for restarts).
    """
    from logic.process_reaper import reap_all_tools, reap_orphans

    if include_running:
        reap_all_tools(include_active=True)
    else:
        reap_orphans(force=True)
