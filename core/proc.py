"""External-tool subprocesses: live output parsing, stop checks and job process registration.

``core`` never imports ``logic``. The job layer plugs itself in instead:
``logic.jobs.context`` installs the progress and pause/resume hooks, and the upload
service installs the per-job process registry.
"""

from __future__ import annotations

import codecs
import os
import queue
import re
import subprocess
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Protocol, Tuple

from loguru import logger

from core.logging import strip_ansi


class ProcessRegistry(Protocol):
    """Tracks the tool processes of a job so stop/clear can terminate them."""

    def register_process(self, job_id: str, process: Any) -> None: ...

    def unregister_process(self, job_id: str, process: Optional[Any] = None) -> None: ...


def _no_progress(**_kwargs: Any) -> None:
    return None


def _not_stopped(job: Optional[Dict[str, Any]]) -> bool:
    return not bool(job and job.get("stop_requested"))


_progress_hook: Callable[..., None] = _no_progress
_resume_hook: Callable[[Optional[Dict[str, Any]]], bool] = _not_stopped
_registry_factory: Optional[Callable[[], ProcessRegistry]] = None


def install_job_hooks(
    *,
    progress: Callable[..., None],
    wait_for_resume: Callable[[Optional[Dict[str, Any]]], bool],
) -> None:
    """Route tool progress and pause/stop waits through the job layer."""
    global _progress_hook, _resume_hook
    _progress_hook = progress
    _resume_hook = wait_for_resume


def set_process_registry(factory: Optional[Callable[[], ProcessRegistry]]) -> None:
    """Install the factory that returns the registry job processes are recorded in."""
    global _registry_factory
    _registry_factory = factory


def _report_progress(**kwargs: Any) -> None:
    _progress_hook(**kwargs)


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
            _report_progress(msg=f"[{log_prefix}] {parsed_line}")
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

        _report_progress(**update_kwargs)
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
    job_id = job.get("job_id") if job else None
    registry = _registry_factory() if (job_id and _registry_factory is not None) else None
    output_lines: Deque[str] = deque(maxlen=2000)

    if job and not _resume_hook(job):
        return False, output_lines

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    except FileNotFoundError:
        executable = cmd[0] if cmd else "<missing>"
        message = f"{log_prefix} executable not found: {executable} (cwd={os.getcwd()})"
        logger.exception(message)
        output_lines.append(message)
        if job:
            _report_progress(msg=message)
        return False, output_lines
    except OSError as exc:
        message = f"{log_prefix} failed to start command: {exc}"
        logger.exception(message)
        output_lines.append(message)
        if job:
            _report_progress(msg=f"[{log_prefix}] {exc}")
        return False, output_lines

    if registry is not None and job_id:
        registry.register_process(job_id, process)

    reader: Optional[threading.Thread] = None
    try:
        reader, stopped = _run_command_stream_output(process, job, output_lines, log_prefix, parser, quiet)
        if stopped is _RUN_COMMAND_STOPPED:
            return False, output_lines
    finally:
        process.wait()
        if reader is not None:
            reader.join(timeout=1.0)
        if registry is not None and job_id:
            registry.unregister_process(job_id, process)

    if process.returncode != 0:
        if not (job and job.get("stop_requested")):
            logger.error(f"{log_prefix} failed with code {process.returncode}")
            for line in list(output_lines)[-5:]:
                logger.error(f"[{log_prefix} Output] {line}")
        return False, output_lines

    return True, output_lines
