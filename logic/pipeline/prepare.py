"""Item preparation: tool checks, RAR/PAR2 staging, MediaInfo/NFO sidecars, temp space, support assets."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import humanfriendly  # type: ignore[import-untyped]
from loguru import logger

from core import tools as core_tools
from core.config import get_config
from core.fs import compute_size_uncached
from core.logging import log_info, log_verbose
from core.media import VIDEO_EXTENSIONS
from core.proc import extract_percentage, extract_speed, run_command
from logic.jobs.context import get_thread_job, update_job_progress


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


def _safe_fs_component(value: str, *, fallback: str = "item", max_length: int = 120) -> str:
    """Return a stable path component safe for temporary workspace names."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
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


def _find_mediainfo_path(path: Path, conf: Any) -> Optional[Path]:
    """Return the generated mediainfo sidecar for an item when present."""
    candidate = _mediainfo_output_path(path, conf)
    return candidate if candidate.exists() else None


def _new_prepare_tmp_path(conf: Any, name: str, job: Optional[dict[str, Any]]) -> Path:
    """Create a unique temp path for this item and expose it to upload workers."""
    job_part = _safe_fs_component(str(job.get("job_id") or "job") if job else "job", max_length=40)
    item_part = _safe_fs_component(name)
    tmp = conf.tmp_sub / f"{job_part}-{item_part}-{uuid.uuid4().hex[:10]}"
    if job is not None:
        job["_current_prepare_tmp"] = str(tmp)
    return tmp


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
            and not _mediainfo_sidecar_has_escaped_names(info_path)
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

    from logic.pipeline.posting import ProgressTracker

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


def check_tools(conf: Optional[Any] = None) -> bool:
    """Verify that required external tools are available."""
    conf = conf or get_config()
    missing = [
        f"{name} ({core_tools.tool_command(conf, name)})"
        for name, executable in core_tools.check_tools(conf).items()
        if not executable
    ]
    if missing:
        msg = f"Missing required tools: {', '.join(missing)}"
        log_info(msg, "ERROR")
        update_job_progress(msg=msg, status="failed")
        return False
    return True


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
