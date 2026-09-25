"""
📦 NZBPostarr - Utilities Module
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Common utilities and formatting functions.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import codecs
import fnmatch
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union, cast

import humanfriendly  # type: ignore[import-untyped]
from loguru import logger
from rich.console import Console
from rich.text import Text
from watchdog.observers import Observer

_console = Console()
_job_ctx: ContextVar[Optional[Dict[str, Any]]] = ContextVar("job", default=None)
_job_lock = threading.Lock()

_SUBMISSION_CATEGORY_ALIASES = {
    "movie": "movies",
    "movies": "movies",
    "tv": "tv",
    "show": "tv",
    "shows": "tv",
    "tv show": "tv",
    "tv episode": "tv",
    "episode": "tv",
    "anime": "anime",
    "music": "music",
    "book": "books",
    "books": "books",
    "audiobook": "audiobooks",
    "audiobooks": "audiobooks",
    "ebook": "books",
    "ebooks": "books",
    "app": "apps",
    "apps": "apps",
    "game": "apps",
    "games": "apps",
    "misc": "misc",
    "other": "misc",
}


def normalize_submission_category(raw: Any) -> str:
    """Normalize user-, queue-, and scanner-facing category aliases."""
    cleaned = " ".join(str(raw or "").strip().lower().split())
    return _SUBMISSION_CATEGORY_ALIASES.get(cleaned, cleaned)


def atomic_write_text(path: Path, content: str) -> None:
    """Durably write a complete UTF-8 text file before replacing its destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass


# Add custom levels to Loguru
def _register_levels() -> None:
    levels = [
        ("SECTION", 35, "<cyan>"),
        ("PROGRESS", 25, "<yellow>"),
        ("SUCCESS", 26, "<green>"),
        ("COMPLETED", 27, "<magenta>"),
        ("VERBOSE", 5, "<blue>"),
        ("WARN", 30, "<yellow>"),
    ]
    for name, no, color in levels:
        try:
            logger.level(name, no=no, color=color)
        except (TypeError, ValueError):
            pass


_register_levels()

# Configure File Logging
try:
    # Keep runtime logs out of the repo root (and out of git).
    log_file = Path(__file__).resolve().parent.parent / "data" / "logs" / "nzbpostarr.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_file),
        rotation="10 MB",
        retention="7 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        encoding="utf-8",
        enqueue=True,
    )
except Exception:
    pass


def log_backend_timing(
    name: str,
    start_time: float,
    *,
    context: str = "",
    warn_threshold_s: float = 1.0,
) -> None:
    """Emit timing details for expensive backend work when verbose logging is enabled."""
    from core import config as config_mod

    conf = getattr(config_mod, "_GLOBAL_CONFIG", None)
    if not bool(getattr(conf, "verbose", False)):
        return

    elapsed = time.perf_counter() - start_time
    suffix = f" | {context}" if context else ""
    message = f"[backend-timing] {name} took {elapsed:.3f}s{suffix}"
    logger.log("DEBUG" if elapsed >= warn_threshold_s else "VERBOSE", message)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string."""
    return str(Text.from_ansi(text).plain)


def extract_percentage(line: str) -> Optional[float]:
    """Extract a percentage value (0.0 to 100.0) from a line of text."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
    if match:
        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            pass
    return None


def extract_speed(line: str) -> Optional[str]:
    """Extract a speed string (e.g., '10.2 MiB/s') from a line of text."""
    # Matches common speed formats: 10 MiB/s, 500 KB/s, 1.2 GB/s, etc.
    match = re.search(r"([\d\.]+\s*[KMG]?[iI]?[bB]/s)", line, re.I)
    return match.group(1) if match else None


def is_priority_key(progress_key: Optional[str]) -> bool:
    """Check if a progress key indicates a priority upload."""
    return "(P)" in (progress_key or "")


def get_priority_label(is_priority: bool) -> str:
    """Return a label for logging based on priority status."""
    return "Priority " if is_priority else ""


def _process_output_line(
    line: str,
    output_lines: Deque[str],
    log_prefix: str,
    parser: Optional[Callable[[str], Optional[str]]],
    quiet: bool,
    job: Optional[Dict[str, Any]],
) -> None:
    """Process a single line of subprocess output (shared by chunk reader)."""
    output_lines.append(line)

    if parser:
        parsed_line = parser(line)
        if parsed_line:
            update_job_progress(msg=f"[{log_prefix}] {parsed_line}")
            if not quiet:
                logger.log("VERBOSE", f"[{log_prefix}] {parsed_line}")
            return

    # Fallback line processing if no parser or parser didn't match
    lower_line = line.lower()
    pct = extract_percentage(line)
    if pct is not None:
        update_kwargs: Dict[str, Any] = {"msg": f"[{log_prefix}] {line}"}
        if not parser:
            update_kwargs["item_percent"] = int(pct)

        update_job_progress(**update_kwargs)
        if not quiet:
            logger.log("PROGRESS", f"[{log_prefix}] {line}")
    elif lower_line.startswith("[warn]") or "will retry" in lower_line:
        # Tool-level warnings (e.g. Nyuu [WARN] NNTP timeouts, post-check retries) -
        # these are transient/recoverable and should be WARNING, not ERROR.
        logger.warning(f"[{log_prefix}] {line}")
    elif any(x in lower_line for x in ["error", "unknown", "failed"]):
        logger.error(f"[{log_prefix}] {line}")
    elif any(x in lower_line for x in ["success", "finished", "done"]):
        if not quiet:
            logger.log("VERBOSE", f"[{log_prefix}] {line}")


def _run_command_terminate(process: "subprocess.Popen[bytes]", log_prefix: str, reason: str) -> None:
    process.terminate()
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        process.kill()
    logger.info(f"Process terminated by user{reason}: {log_prefix}")


def _run_command_process_text(
    data: str,
    remainder: str,
    output_lines: Deque[str],
    log_prefix: str,
    parser: Optional[Callable[[str], Optional[str]]],
    quiet: bool,
    job: Optional[Dict[str, Any]],
) -> str:
    combined = remainder + data
    ends_with_newline = bool(combined) and combined[-1] in ("\n", "\r")
    parts = combined.splitlines()
    next_remainder = "" if ends_with_newline else (parts.pop() if parts else "")
    for raw_line in parts:
        line = strip_ansi(raw_line.strip())
        if line:
            _process_output_line(line, output_lines, log_prefix, parser, quiet, job)
    return next_remainder


def _run_command_read_stdout(stdout_pipe: Any, chunks: "queue.Queue[Optional[bytes]]") -> None:
    try:
        while True:
            chunk = stdout_pipe.read(64 * 1024)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            chunks.put(chunk)
    finally:
        chunks.put(None)


_RUN_COMMAND_STOPPED = object()


def _run_command_stream_output(
    process: "subprocess.Popen[bytes]",
    job: Optional[Dict[str, Any]],
    output_lines: Deque[str],
    log_prefix: str,
    parser: Optional[Callable[[str], Optional[str]]],
    quiet: bool,
) -> Tuple[Optional[threading.Thread], Any]:
    """Read/process live stdout for `run_command`.

    Returns ``(reader_thread, _RUN_COMMAND_STOPPED)`` if a stop/pause caused
    early termination, else ``(reader_thread, None)``.
    """
    if not process.stdout:
        return None, None

    # Keep blocking pipe reads off the orchestration thread.  A quiet
    # child process must not delay stop/pause checks until it emits 4KB
    # or exits.  The unbuffered binary pipe also lets each read return
    # whatever is currently available.
    chunks: "queue.Queue[Optional[bytes]]" = queue.Queue()
    # Bind the pipe locally: the `if process.stdout` guard above does not
    # narrow the attribute inside the closure.
    stdout_pipe = process.stdout

    reader = threading.Thread(
        target=_run_command_read_stdout,
        args=(stdout_pipe, chunks),
        name=f"{log_prefix}-stdout",
        daemon=True,
    )
    reader.start()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    remainder = ""
    reader_done = False
    while True:
        # Pause never blocks or kills a running tool: the current item finishes
        # and the job holds at the next item boundary (wait_for_job_resume in
        # logic/processing). Only an explicit stop ends the process early.
        if job and job.get("stop_requested"):
            _run_command_terminate(process, log_prefix, "")
            return reader, _RUN_COMMAND_STOPPED

        try:
            chunk = chunks.get(timeout=0.1)
        except queue.Empty:
            if process.poll() is not None and (reader_done or not reader.is_alive()):
                break
            continue

        if chunk is None:
            reader_done = True
            remainder = _run_command_process_text(
                decoder.decode(b"", final=True), remainder, output_lines, log_prefix, parser, quiet, job
            )
            if process.poll() is not None:
                break
            continue

        remainder = _run_command_process_text(
            decoder.decode(chunk), remainder, output_lines, log_prefix, parser, quiet, job
        )

    if remainder.strip():
        line = strip_ansi(remainder.strip())
        if line:
            _process_output_line(
                line,
                output_lines,
                log_prefix,
                parser,
                quiet,
                job,
            )

    return reader, None


def run_command(
    cmd: List[str],
    log_prefix: str,
    job: Optional[Dict[str, Any]] = None,
    parser: Optional[Callable[[str], Optional[str]]] = None,
    quiet: bool = False,
) -> Tuple[bool, Deque[str]]:
    """Run a command with stop-check capability and live logging."""
    from logic.runtime import get_engine

    processes = get_engine().process_registry()
    job_id = job.get("job_id") if job else None
    output_lines: Deque[str] = deque(maxlen=2000)

    if job and not wait_for_job_resume(job):
        return False, output_lines

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    except FileNotFoundError:
        executable = cmd[0] if cmd else "<missing>"
        message = f"{log_prefix} executable not found: {executable} (cwd={os.getcwd()})"
        logger.exception(message)
        output_lines.append(message)
        if job:
            update_job_progress(msg=message)
        return False, output_lines
    except OSError as exc:
        message = f"{log_prefix} failed to start command: {exc}"
        logger.exception(message)
        output_lines.append(message)
        if job:
            update_job_progress(msg=f"[{log_prefix}] {exc}")
        return False, output_lines

    if job_id:
        processes.register(job_id, process)

    reader: Optional[threading.Thread] = None
    try:
        reader, stopped = _run_command_stream_output(process, job, output_lines, log_prefix, parser, quiet)
        if stopped is _RUN_COMMAND_STOPPED:
            return False, output_lines
    finally:
        process.wait()
        if reader is not None:
            reader.join(timeout=1.0)
        if job_id:
            processes.unregister(job_id, process)

    if process.returncode != 0:
        if not (job and job.get("stop_requested")):
            logger.error(f"{log_prefix} failed with code {process.returncode}")
            for line in list(output_lines)[-5:]:
                logger.error(f"[{log_prefix} Output] {line}")
        return False, output_lines

    return True, output_lines


def start_watchdog_observer(
    watches: List[Tuple[Any, Path, bool]],
    *,
    observer_factory: Optional[Callable[[], Any]] = None,
) -> Tuple[Optional[Any], int]:
    """Start a watchdog observer for the provided handler/path specs."""
    factory = observer_factory or Observer
    observer = factory()
    scheduled = 0

    for handler, folder, recursive in watches:
        path = Path(folder)
        if not path.exists() or not path.is_dir():
            continue
        observer.schedule(handler, str(path), recursive=recursive)
        scheduled += 1

    if scheduled == 0:
        return None, 0

    observer.start()
    return observer, scheduled


def stop_watchdog_observer(observer: Any, *, join_timeout_s: float = 5.0) -> None:
    """Stop and join a watchdog observer when present."""
    if observer is None:
        return

    observer.stop()
    observer.join(timeout=join_timeout_s)


def set_thread_job(job_dict: Optional[Dict[str, Any]]) -> Any:
    """Bind ``job_dict`` to the current thread/task context.

    Returns the contextvar token so callers can ``reset_thread_job`` later
    and restore the prior value. The token is opaque -- treat it as private.
    Callers that don't need to restore (e.g. the lifetime of the worker
    thread is the lifetime of the job) may discard the token.
    """
    return _job_ctx.set(job_dict)


def reset_thread_job(token: Any) -> None:
    """Restore the prior thread-job binding for ``token``.

    Safe to call with a stale token: the underlying ``ContextVar`` raises
    ``LookupError`` / ``ValueError`` when the token can't be applied (e.g.
    different context); we swallow those because the caller has already
    decided the binding is no longer wanted.
    """
    if token is None:
        return
    try:
        _job_ctx.reset(token)
    except (LookupError, ValueError):
        # Token belongs to a different context (e.g. a thread that already
        # exited). The next ``get_thread_job`` will simply observe whatever
        # value is currently set, which is the desired fallback.
        _job_ctx.set(None)


def get_thread_job() -> Optional[Dict[str, Any]]:
    return _job_ctx.get()


def wait_for_job_resume(job: Optional[Dict[str, Any]], check_interval: float = 0.2) -> bool:
    """Block while a job is paused; return False when a stop is requested."""
    if not job:
        return True

    if job.get("pause_requested") and job.get("status") != "paused":
        job["status"] = "paused"
        job["progress"] = "Paused by user"
        job["speed"] = "Paused"
        job["current_stage"] = "PAUSED"
        callback = job.get("_pause_ack_callback")
        if callable(callback):
            callback()

    while job.get("pause_requested"):
        if job.get("stop_requested"):
            return False
        time.sleep(check_interval)

    return not bool(job.get("stop_requested"))


VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".ts",
    ".m2ts",
    ".mov",
    ".m4v",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".flv",
    ".vob",
    ".webm",
    ".ogv",
    ".ogm",
}

MUSIC_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".aac",
    ".wav",
    ".m4a",
    ".ogg",
    ".wma",
    ".ape",
    ".opus",
    ".aiff",
    ".alac",
    ".dsf",
    ".dff",
    ".mka",
}

EBOOK_EXTENSIONS = {
    ".epub",
    ".mobi",
    ".azw",
    ".azw3",
    ".cbr",
    ".cbz",
    ".lit",
    ".djvu",
    ".pdf",
}

AUDIOBOOK_EXTENSIONS = {
    ".m4b",
}

_LEGACY_EPISODE_NUMBER_RE = re.compile(
    r"^(?P<prefix>.+?)(?:[.\s_-]+)(?P<code>[1-9]\d{2,3})(?:[.\s_-]+)(?P<suffix>.+)$",
    re.IGNORECASE,
)


def _normalize_release_segment(value: str) -> str:
    cleaned = Path(value).stem
    cleaned = re.sub(r"[\[\](){}]+", " ", cleaned)
    cleaned = re.sub(r"[._-]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def has_multi_file_episode_pattern(names: List[str], *, min_matches: int = 2) -> bool:
    """Return True when multiple names share legacy episodic numbering.

    This catches older TV releases that use repeated numeric episode codes like
    ``101``, ``102``, ``1001`` instead of explicit ``S01E01`` tags.
    """
    min_matches = max(min_matches, 2)

    matches_by_prefix: Dict[str, set[str]] = {}
    for raw_name in names:
        match = _LEGACY_EPISODE_NUMBER_RE.match(Path(raw_name).stem)
        if not match:
            continue

        code = match.group("code")
        code_value = int(code)
        if 1900 <= code_value <= 2099:
            continue

        prefix = _normalize_release_segment(match.group("prefix"))
        suffix = _normalize_release_segment(match.group("suffix"))
        if len(prefix) < 4 or len(suffix) < 2:
            continue

        seen_codes = matches_by_prefix.setdefault(prefix, set())
        seen_codes.add(code)
        if len(seen_codes) >= min_matches:
            return True

    return False


def purge_item_data(name: str) -> None:
    """Complete cleanup of temporary data for a given item name."""
    from core.config import get_config

    conf = get_config()

    # 1. Purge NZB
    nzb_path = conf.get_nzb_path(name)
    if nzb_path.exists():
        try:
            nzb_path.unlink()
        except OSError:
            pass

    # 2. Purge TMP folder (Rar/Par artifacts)
    tmp_folder = conf.tmp_sub / name
    if tmp_folder.exists():
        shutil.rmtree(tmp_folder, ignore_errors=True)

    # 3. Purge Mediainfo
    info_path = conf.mediainfo_sub / f"{name}.mediainfo.nfo"
    if info_path.exists():
        try:
            info_path.unlink()
        except OSError:
            pass

    log_verbose(f"Purged all temp data for: {name}")


def run_global_purge() -> None:
    """Clean all temporary internal directories (tmp, mediainfo, nzbs)."""
    from core.config import get_config

    conf = get_config()
    targets = [conf.tmp_sub, conf.mediainfo_sub, conf.nzb_sub]

    log_verbose("Running global purge of internal temporary directories...")
    start_time = time.time()
    count = 0

    for target in targets:
        if target.exists():
            # Optimization: Use a faster way to iterate and delete if many files exist
            for item in target.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                    count += 1
                except Exception as e:
                    logger.debug(f"Failed to purge {item}: {e}")

    elapsed = time.time() - start_time
    if count > 0:
        log_verbose(f"Global purge complete. Removed {count} items in {elapsed:.2f}s.")
    else:
        log_verbose("Global purge complete (no items to remove).")


def update_job_progress(**kwargs: Any) -> None:
    """Update progress state for the current thread's job."""
    job = _job_ctx.get()
    if not job:
        return

    # Map kwargs to job keys
    mapping = {
        "msg": "progress",
        "processed": "items_processed",
        "total": "items_total",
        "percent": "progress_percent",
        "status": "status",
        "item_name": "current_item",
        "item_percent": "item_percent",
        "speed": "speed",
        "eta": "eta",
        "skipped": "items_skipped",
    }

    with _job_lock:
        for k, v in kwargs.items():
            if k == "item_size":
                job["item_size_str"] = format_size(v)
            elif k == "key":
                continue  # Handled below
            elif k == "item_name":
                # Only let priority labels or new item names win
                current = job.get("current_item", "")
                is_prio = "Priority" in str(v)
                curr_is_prio = "Priority" in str(current)

                # If we have a priority label already, don't let a non-priority one overwrite it
                # UNLESS the non-priority one is a completely different item name
                if curr_is_prio and not is_prio:
                    # Check if names (ignoring Priority prefix) are different
                    curr_clean = current.replace("Priority ", "")
                    new_clean = str(v).replace("Priority ", "")
                    if curr_clean == new_clean:
                        continue

                job["current_item"] = v
            elif k in mapping:
                job[mapping[k]] = v

        # Handle multi-item tracking (Dual Uploads)
        key = kwargs.get("key")
        if key:
            if "item_percents" not in job:
                job["item_percents"] = {}
            if "item_speeds" not in job:
                job["item_speeds"] = {}

            if "item_percent" in kwargs:
                # Store as float for precision, but dashboard often expects int
                val = float(kwargs["item_percent"])
                job["item_percents"][key] = val

                # RECALCULATE GLOBAL ITEM PERCENT
                # We always want to show the average of active items
                active_percents = [v for v in job["item_percents"].values() if v is not None]
                if active_percents:
                    job["item_percent"] = int(sum(active_percents) / len(active_percents))

            if "speed" in kwargs:
                # Store the speed and ensure it's a clean, stripped string
                job["item_speeds"][key] = str(kwargs["speed"]).strip()

                # Aggregate active speeds (e.g. "10 MB/s | 12 MB/s")
                all_speeds = [v for v in job["item_speeds"].values() if v and v != "Starting..."]
                if all_speeds:
                    job["speed"] = " | ".join(dict.fromkeys(all_speeds))


def log_info(m: str, level: str = "INFO") -> None:
    logger.log(level, m)


def log_success(m: str) -> None:
    logger.log("SUCCESS", m)


def log_completed(m: str) -> None:
    logger.log("COMPLETED", m)


def log_verbose(m: str) -> None:
    logger.log("VERBOSE", m)


def format_size(b: Union[int, float]) -> str:
    """Format bytes into a human-readable size string."""
    return cast(str, humanfriendly.format_size(b, binary=True))


def compute_size_uncached(p: Path) -> int:
    """Compute size recursively without caching for live filesystem views."""
    try:
        if p.is_file():
            return p.stat().st_size
        total = 0
        for root, _dirs, files in os.walk(str(p)):
            for fname in files:
                try:
                    total += os.path.getsize(os.path.join(root, fname))
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def should_skip_file(filename: str, category: str, skip_config: Optional[Dict[str, Any]] = None) -> bool:
    """Check if a filename matches any skip pattern for the given category.

    Supports both glob (fnmatch) and regex patterns via the ``is_regex`` flag
    on each pattern entry.
    """
    if not skip_config or not skip_config.get("enabled"):
        return False

    for p in skip_config.get("patterns", []):
        pattern = p.get("pattern", "")
        cats = p.get("categories", [])
        if not pattern:
            continue
        # Empty categories list means the pattern applies to every category
        if cats and category not in cats:
            continue
        if p.get("is_regex"):
            try:
                if re.search(pattern, filename, re.IGNORECASE):
                    return True
            except re.error:
                continue  # invalid regex - skip silently
        else:
            if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                return True
    return False
