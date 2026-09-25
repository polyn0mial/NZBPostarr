# Auto-split from processing.py - verbatim symbol bodies, synthesized imports.

from logic.processing_base import (
    AUDIOBOOK_EXTENSIONS, Any, Dict, EBOOK_EXTENSIONS, List, Lock, MUSIC_EXTENSIONS, Optional, Path, VIDEO_EXTENSIONS, _APP_EXTENSIONS, _ONE_GIB, copy,
    dataclass, field, has_clear_movie_year, has_multi_file_episode_pattern, humanfriendly, log_verbose, logger, normalize_category, os, re,
    shutil, update_job_progress,
)

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

@dataclass(frozen=True)
class SupportAssetScan:
    """Single-pass scan results reused across mediainfo and upload sidecars."""

    nfo_path: Optional[Path] = None
    mediainfo_source_path: Optional[Path] = None

def _mediainfo_output_path(path: Path, conf: Any) -> Path:
    """Return the canonical mediainfo sidecar path for an item."""
    return conf.mediainfo_sub / f"{path.name}.mediainfo.nfo"

def _mediainfo_sidecar_has_escaped_names(info_path: Path) -> bool:
    """True for sidecars written by the old re.escape code ('Movie\\.2020\\ 1080p').

    Sanitized names use forward slashes only, so a backslash on a name line
    marks an old sidecar that must be regenerated instead of reused.
    """
    try:
        text = info_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(
        "\\" in line
        for line in text.splitlines()
        if line.startswith(("Complete name", "Folder name", "File name"))
    )

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
        (r"^(Complete name\s+:\s+).*", str(rel_target).replace("\\", "/")),
        (r"^(Folder name\s+:\s+).*", str(rel_folder).replace("\\", "/")),
        (r"^(File name\s+:\s+).*", target.name),
    ]
    for pattern, text in patterns:
        # A callable replacement inserts the name literally: no backslash
        # escapes and no group-reference parsing of names starting with digits.
        sanitized_output = re.sub(
            pattern,
            lambda match, value=text: match.group(1) + value,
            sanitized_output,
            flags=re.MULTILINE,
        )
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

def _resolve_targeted_path(raw_path: str) -> Optional[Path]:
    """Targeted jobs only accept explicit absolute paths."""
    if not raw_path:
        return None
    path = Path(str(raw_path))
    return path if path.is_absolute() else None

def _safe_fs_component(value: str, *, fallback: str = "item", max_length: int = 120) -> str:
    """Return a stable path component safe for temporary workspace names."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length]

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

def _has_tv_season_pack_name(path: Path) -> bool:
    """Return True when a folder name itself looks like a season pack."""
    folder_name = path.name.lower()
    return bool(re.search(r"(?:^|[^a-z0-9])s\d{1,2}(?:[^a-z0-9]|$)", folder_name)) or any(
        token in folder_name for token in ("season", "complete")
    )

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
    # Files outside a configured root (staged pack episodes) are keyed by
    # their pack folder too, so same-named episodes of two packs stay apart.
    if not path.is_dir():
        parent_name = path.parent.name
        if parent_name and parent_name not in {"", "."}:
            return f"{parent_name}/{path.name}"
    return path.name

def _already_exists_skip_message(path: Path, source_root: Optional[Path], size_bytes: int) -> str:
    """Name the pack folder and size so the log shows which copy was skipped."""
    skip_folder = path.parent.name if (source_root is None or path.parent != source_root) else ""
    skip_mb = round(size_bytes / (1024 * 1024)) if size_bytes else 0
    size_part = f" ({skip_mb} MB)" if skip_mb else ""
    folder_part = f" [{skip_folder}]" if skip_folder else ""
    return f"⏩ '{path.name}'{folder_part}{size_part} already exists on all selected destinations - skipped"

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
    normalized = normalize_category(key)
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
        "disc": "DISC",
        "music": "Music",
        "audiobooks": "Audiobooks",
        "books": "Books",
        "apps": "Apps",
        "misc": "Misc",
    }.get(category, category or "Unknown")

def _resolve_ambiguous_submission_category(path: Path, normalized_category: str, normalized_itype: str) -> str:
    """Disambiguate a tv/movies/misc category against cached anime status.

    Extracted from _resolve_submission_category to keep its own branching down.
    """
    if normalized_itype in {"anime", "music", "audiobooks", "books", "apps"}:
        return normalized_itype

    from logic.classify.anime import get_cached

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
                return "anime"
        except Exception:
            return normalized_category
    else:
        if normalized_itype in {"tv", "tv_episode"} and normalized_category != "movies":
            return "tv"
        if normalized_itype == "movie" and normalized_category != "tv":
            return "movies"

    return normalized_category

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

def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0

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

def _classify_preview_validation(
    validation: "QueueItemValidation",
    *,
    normalized: str,
    selected_indexer_ids: List[str],
    resolved_force: bool,
    oversized_files: Any,
) -> "tuple[str, str, Dict[str, Dict[str, Any]]]":
    """Turn one item's validation result into a preview outcome/reason/destinations.
    Extracted from preview_processing_items to keep its own branching down."""
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
    return outcome, reason, destination_status

def _summarize_preview_details(
    details: List[Dict[str, Any]],
    selected_indexer_ids: List[str],
) -> "tuple[Dict[str, Dict[str, int]], Dict[str, int], int, int, int]":
    """Aggregate per-item preview details into summary counters.
    Extracted from preview_processing_items to keep its own branching down."""
    destination_summary: Dict[str, Dict[str, int]] = {
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

    return destination_summary, outcome_counts, ready_bytes, total_bytes, partial_duplicates

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

