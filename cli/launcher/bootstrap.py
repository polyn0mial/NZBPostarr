"""Launcher paths and the venv bootstrap. Standard library only: runs before the venv exists."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_NAME = "NZBPostarr"

# The launcher lives directly in the workspace root.
ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQS = ROOT / "requirements.lock"


def create_venv() -> None:
    """Create the project venv with the interpreter running this launcher."""
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)


def install_requirements(*, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Install the pinned runtime from requirements.lock into the venv; the caller judges the return code."""
    return subprocess.run(
        [str(VENV_PY), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(REQS)],
        check=False,
        capture_output=capture,
        text=True,
    )


def in_venv() -> bool:
    """Check if running inside virtual environment."""
    return sys.prefix != sys.base_prefix


def bootstrap() -> None:
    """Create venv, install deps, relaunch."""
    print("\n" + "=" * 60)
    print(f"  {PROJECT_NAME} - Bootstrapping")
    print("=" * 60 + "\n")

    if not VENV.exists():
        print("[1/3] Creating .venv...")
        create_venv()
    else:
        print("[1/3] .venv exists")

    if REQS.exists():
        # Optimization: Skip pip check if requirements haven't changed since last successful install
        marker = VENV / ".last_pip_check"
        if not marker.exists() or REQS.stat().st_mtime > marker.stat().st_mtime:
            print("[2/3] Installing/Updating requirements (this may take a moment)...")
            install_requirements().check_returncode()
            marker.touch()
        else:
            print("[2/3] Requirements up to date")
    else:
        print(f"[2/3] Warning: {REQS} not found")

    print("[3/3] Relaunching in venv...\n")
    result = subprocess.run([str(VENV_PY), str(ROOT / "main.py"), *sys.argv[1:]], check=False)
    sys.exit(result.returncode)
