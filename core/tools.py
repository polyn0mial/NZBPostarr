"""External tools (rar, parpar, nyuu, ...): where they are and which version is installed.

Standard library only: setup.py imports this before the venv exists.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

PROCESSING_TOOLS = ("rar", "parpar", "nyuu")


def resolve_tool(name: str, configured_path: Any = None) -> Optional[str]:
    """Return the executable for ``name``, or None when it is missing.

    A configured path wins over PATH: an explicit path (absolute or with a directory part)
    must exist; a bare command name is looked up on PATH.
    """
    text = str(configured_path or "").strip() or name.strip()
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return str(candidate) if candidate.exists() else None
    return shutil.which(text)


def tool_command(conf: Any, name: str) -> str:
    """The command string configured for ``name`` (``<name>_path``), else the bare name."""
    return str(getattr(conf, f"{name}_path", "") or name)


def tool_version(name: str, configured_path: Any = None) -> Optional[str]:
    """Best-effort version string of a tool, or None when it is missing or silent."""
    executable = resolve_tool(name, configured_path)
    if not executable:
        return None
    for flag in ("--version", "-V", "version"):
        try:
            r = subprocess.run([executable, flag], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        out = (r.stdout + r.stderr).strip()
        if out:
            m = re.search(r"(\d+\.\d+[\.\d]*)", out)
            return m.group(1) if m else out[:60]
    return None


def check_tools(conf: Any, names: tuple[str, ...] = PROCESSING_TOOLS) -> Dict[str, Optional[str]]:
    """Resolve each tool through its configured path: name -> executable (None when missing)."""
    return {name: resolve_tool(name, tool_command(conf, name)) for name in names}
