#!/usr/bin/env python3
"""
NZBPostarr - First-Run Setup Wizard
====================================
Interactive guided installer for new server deployments.

Run:  python setup.py

Handles:
  1. System dependency checks (python version, OS packages)
  2. Virtual environment creation & pip deps
  3. External tool installation (nyuu, parpar, rar)
  4. Guided config.yaml generation
  5. Optional systemd service creation
  6. Smoke test - verifies everything actually works
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli.launcher.bootstrap import REQS as REQS_FILE
from cli.launcher.bootstrap import VENV as VENV_DIR
from cli.launcher.bootstrap import VENV_PY, create_venv, install_requirements

# ── Paths ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent


def _get_setup_config_path() -> Path:
    env_path = os.getenv("NZBPOSTARR_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    return ROOT / ".config" / "nzbpostarr" / "config.yaml"


CONFIG_FILE = _get_setup_config_path()
DEFAULTS_FILE = ROOT / "core" / "config.defaults.yaml"

# ── Styling ──────────────────────────────────────────────────────────
_COLOR = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[38;5;114m",
    "blue": "\033[38;5;111m",
    "yellow": "\033[38;5;221m",
    "red": "\033[38;5;203m",
    "cyan": "\033[38;5;80m",
    "magenta": "\033[38;5;176m",
    "white": "\033[97m",
    "grey": "\033[38;5;245m",
    "bg_blue": "\033[48;5;24m",
    "bg_green": "\033[48;5;22m",
    "bg_red": "\033[48;5;52m",
}

# Disable colour on dumb terminals
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    _COLOR = {k: "" for k in _COLOR}

C = _COLOR  # short alias


def _c(color: str, text: str) -> str:
    return f"{C.get(color, '')}{text}{C['reset']}"


def banner() -> None:
    art = rf"""
{C["blue"]}{C["bold"]}
    ╔══════════════════════════════════════════════════════╗
    ║                                                      ║
    ║     ███╗   ██╗███████╗██████╗ ██████╗                ║
    ║     ████╗  ██║╚══███╔╝██╔══██╗██╔══██╗              ║
    ║     ██╔██╗ ██║  ███╔╝ ██████╔╝██████╔╝              ║
    ║     ██║╚██╗██║ ███╔╝  ██╔══██╗██╔═══╝               ║
    ║     ██║ ╚████║███████╗██████╔╝██║                    ║
    ║     ╚═╝  ╚═══╝╚══════╝╚═════╝ ╚═╝                   ║
    ║              {C["cyan"]}P O S T A R R{C["blue"]}                            ║
    ║                                                      ║
    ║     {C["dim"]}{C["white"]}First-Run Setup Wizard{C["blue"]}{C["bold"]}                        ║
    ╚══════════════════════════════════════════════════════╝
{C["reset"]}"""
    print(art)


def header(title: str) -> None:
    w = 56
    print()
    print(f"  {C['blue']}{'━' * w}{C['reset']}")
    print(f"  {C['bold']}{C['white']}  {title}{C['reset']}")
    print(f"  {C['blue']}{'━' * w}{C['reset']}")


def step(num: int, total: int, label: str) -> None:
    pct = int(num / total * 100)
    bar_w = 20
    filled = int(bar_w * num / total)
    bar = f"{C['green']}{'█' * filled}{C['grey']}{'░' * (bar_w - filled)}{C['reset']}"
    print(f"\n  {bar}  {C['bold']}Step {num}/{total}{C['reset']}  {label}  {C['dim']}({pct}%){C['reset']}")


def ok(msg: str) -> None:
    print(f"  {C['green']}✓{C['reset']} {msg}")


def warn(msg: str) -> None:
    print(f"  {C['yellow']}⚠{C['reset']} {msg}")


def fail(msg: str) -> None:
    print(f"  {C['red']}✗{C['reset']} {msg}")


def info(msg: str) -> None:
    print(f"  {C['blue']}ℹ{C['reset']} {msg}")


def dim(msg: str) -> None:
    print(f"  {C['dim']}{msg}{C['reset']}")


def ask(prompt: str, default: str = "", password: bool = False) -> str:
    """Prompt user for input with an optional default."""
    suffix = f" [{C['cyan']}{default}{C['reset']}]" if default else ""
    try:
        if password:
            import getpass

            val = getpass.getpass(f"  {C['white']}▸ {prompt}{suffix}: {C['reset']}")
        else:
            val = input(f"  {C['white']}▸ {prompt}{suffix}: {C['reset']}")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return val.strip() or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        val = input(f"  {C['white']}▸ {prompt} [{C['cyan']}{hint}{C['reset']}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    if not val:
        return default
    return val in ("y", "yes")


def ask_choice(prompt: str, choices: List[str], default: int = 0) -> int:
    """Numbered menu selection."""
    for i, c in enumerate(choices):
        marker = f"{C['cyan']}▸{C['reset']}" if i == default else " "
        print(f"    {marker} {C['bold']}{i + 1}{C['reset']}. {c}")
    while True:
        raw = ask(prompt, str(default + 1))
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return idx
        except ValueError:
            pass
        warn("Invalid choice, try again.")


def run(cmd: str, check: bool = True, capture: bool = False, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a shell command with optional capture."""
    return subprocess.run(
        cmd,
        shell=True,
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def cmd_version(name: str) -> Optional[str]:
    """Try to get a version string from a command."""
    for flag in ("--version", "-V", "version"):
        try:
            r = subprocess.run(f"{name} {flag}", shell=True, capture_output=True, text=True, timeout=10)
            out = (r.stdout + r.stderr).strip()
            if out:
                # Extract first version-looking string
                m = re.search(r"(\d+\.\d+[\.\d]*)", out)
                return m.group(1) if m else out[:60]
        except Exception:
            continue
    return None


# =====================================================================
#  STEP 1 - System Checks
# =====================================================================
def check_system(total: int) -> Dict[str, Any]:
    step(1, total, "System Check")
    results: Dict[str, Any] = {"os": platform.system(), "issues": []}

    # Python version
    py_ver = sys.version_info
    if py_ver >= (3, 10):
        ok(f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}")
    else:
        fail(f"Python {py_ver.major}.{py_ver.minor} - need 3.10+")
        results["issues"].append("python")

    # OS
    os_name = platform.system()
    ok(f"OS: {os_name} {platform.release()}")
    if os_name == "Linux":
        ok("Platform: Linux (recommended)")
    elif os_name == "Darwin":
        ok("Platform: macOS")
    else:
        warn("Platform: Windows - some features may require WSL")

    # RAM
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    gb = round(kb / 1024 / 1024, 1)
                    if gb >= 1:
                        ok(f"RAM: {gb} GB")
                    else:
                        warn(f"RAM: {gb} GB (1 GB+ recommended)")
                    break
    except (OSError, ValueError):
        dim("RAM: could not detect")

    # Disk space
    try:
        usage = shutil.disk_usage(str(ROOT))
        free_gb = round(usage.free / (1024**3), 1)
        if free_gb >= 2:
            ok(f"Disk free: {free_gb} GB")
        else:
            warn(f"Disk free: {free_gb} GB (low - 2 GB+ recommended)")
    except Exception:
        dim("Disk: could not detect")

    return results


# =====================================================================
#  STEP 2 - Virtual Environment & Python Deps
# =====================================================================
def setup_venv(total: int) -> None:
    step(2, total, "Python Environment")

    if VENV_DIR.exists() and VENV_PY.exists():
        ok(f"Virtual environment exists: {VENV_DIR}")
        if ask_yes("Recreate venv from scratch?", default=False):
            shutil.rmtree(VENV_DIR)
        else:
            _install_pip_deps()
            return

    info(f"Creating virtual environment at {VENV_DIR}...")
    create_venv()
    if VENV_PY.exists():
        ok("venv created")
    else:
        fail("Failed to create venv")
        sys.exit(1)

    _install_pip_deps()


def _install_pip_deps() -> None:
    if not REQS_FILE.exists():
        warn("requirements.lock not found - skipping pip install")
        return

    info(f"Installing Python packages from {REQS_FILE.name}...")
    r = install_requirements(capture=True)
    if r.returncode == 0:
        ok("All Python packages installed")
    else:
        fail("Some packages failed to install:")
        print(r.stderr[-500:] if r.stderr else "(no output)")
        if not ask_yes("Continue anyway?"):
            sys.exit(1)


# =====================================================================
#  STEP 3 - External Tools
# =====================================================================
TOOLS_INFO = {
    "nyuu": {
        "desc": "Usenet binary poster (NNTP upload engine)",
        "check": "nyuu",
        "install_apt": None,
        "install_url": "https://github.com/animetosho/Nyuu/releases",
        "install_cmd_linux": (
            "curl -sL $(curl -s https://api.github.com/repos/animetosho/Nyuu/releases/latest "
            '| grep -oP \'"browser_download_url":\\s*"\\K[^"]*linux-x64[^"]*\') -o /tmp/nyuu.7z '
            "&& 7z x -o/usr/local/bin /tmp/nyuu.7z nyuu parpar 2>/dev/null "
            "|| (mkdir -p /tmp/nyuu_ext && cd /tmp/nyuu_ext && 7z x /tmp/nyuu.7z && "
            "cp -f nyuu parpar /usr/local/bin/) "
            "&& chmod +x /usr/local/bin/nyuu /usr/local/bin/parpar"
        ),
    },
    "parpar": {
        "desc": "PAR2 parity file generator (fast, async)",
        "check": "parpar",
        "install_apt": None,
        "install_url": "https://github.com/animetosho/ParPar/releases",
        "install_cmd_linux": None,  # bundled with nyuu release
    },
    "rar": {
        "desc": "RAR archiver for splitting files",
        "check": "rar",
        "install_apt": "rar",
        "install_url": "https://www.rarlab.com/download.htm",
        "install_cmd_linux": (
            "apt-get install -y rar 2>/dev/null || "
            "(curl -sL https://www.rarlab.com/rar/rarlinux-x64-624.tar.gz | tar xz -C /tmp "
            "&& cp /tmp/rar/rar /tmp/rar/unrar /usr/local/bin/ && chmod +x /usr/local/bin/rar)"
        ),
    },
    "mediainfo": {
        "desc": "Media metadata extraction",
        "check": "mediainfo",
        "install_apt": "mediainfo",
        "install_url": "https://mediaarea.net/en/MediaInfo/Download",
        "install_cmd_linux": "apt-get install -y mediainfo 2>/dev/null",
    },
    "tmux": {
        "desc": "Terminal multiplexer (keeps app running after disconnect)",
        "check": "tmux",
        "install_apt": "tmux",
        "install_url": None,
        "install_cmd_linux": "apt-get install -y tmux 2>/dev/null",
    },
    "7z": {
        "desc": "Archive utility (needed to extract nyuu release)",
        "check": "7z",
        "install_apt": "p7zip-full",
        "install_url": None,
        "install_cmd_linux": "apt-get install -y p7zip-full 2>/dev/null",
    },
}

# Tools that are required vs optional
REQUIRED_TOOLS = ["nyuu", "parpar", "rar"]
OPTIONAL_TOOLS = ["mediainfo", "tmux", "7z"]


def check_tools(total: int) -> Dict[str, bool]:
    step(3, total, "External Tools")
    status: Dict[str, bool] = {}

    all_tools = REQUIRED_TOOLS + OPTIONAL_TOOLS
    for name in all_tools:
        t = TOOLS_INFO[name]
        found = cmd_exists(t["check"])
        status[name] = found

        ver = cmd_version(t["check"]) if found else None
        ver_str = f" (v{ver})" if ver else ""
        required = name in REQUIRED_TOOLS

        if found:
            ok(f"{name}{ver_str} - {t['desc']}")
        elif required:
            fail(f"{name} - {C['red']}MISSING (required){C['reset']} - {t['desc']}")
        else:
            warn(f"{name} - not found (optional) - {t['desc']}")

    # Offer to install missing ones
    missing_required = [n for n in REQUIRED_TOOLS if not status[n]]
    missing_optional = [n for n in OPTIONAL_TOOLS if not status[n]]

    if missing_required or missing_optional:
        print()
        if platform.system() == "Linux":
            auto_installable = [
                n
                for n in (missing_required + missing_optional)
                if TOOLS_INFO[n].get("install_cmd_linux") or TOOLS_INFO[n].get("install_apt")
            ]
            if auto_installable:
                info(f"Missing tools that can be auto-installed: {', '.join(auto_installable)}")
                if ask_yes("Attempt automatic installation?"):
                    _auto_install_tools(auto_installable, status)
        else:
            for n in missing_required:
                url = TOOLS_INFO[n].get("install_url")
                if url:
                    info(f"Install {n}: {url}")

    # Final check
    still_missing = [n for n in REQUIRED_TOOLS if not status.get(n)]
    if still_missing:
        warn(f"Required tools still missing: {', '.join(still_missing)}")
        warn("You can install them later and configure paths in config.yaml")
        if not ask_yes("Continue without them?"):
            sys.exit(1)

    return status


def _auto_install_tools(names: List[str], status: Dict[str, bool]) -> None:
    """Attempt to install tools on Linux."""
    is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False

    # First ensure 7z is available (needed for nyuu extraction)
    if "nyuu" in names and not cmd_exists("7z"):
        if "7z" not in names:
            names.insert(0, "7z")

    for name in names:
        t = TOOLS_INFO[name]
        info(f"Installing {name}...")

        cmd = t.get("install_cmd_linux")
        if not cmd and t.get("install_apt"):
            cmd = f"apt-get install -y {t['install_apt']}"

        if not cmd:
            warn(f"No auto-install method for {name}")
            continue

        # Prepend sudo if needed
        if not is_root and ("apt-get" in cmd or "cp " in cmd):
            cmd = f"sudo {cmd}"

        try:
            r = run(cmd, check=False, capture=True, timeout=300)
            if r.returncode == 0 and cmd_exists(t["check"]):
                ok(f"{name} installed successfully")
                status[name] = True
            else:
                fail(f"Failed to install {name}")
                if r.stderr:
                    dim(r.stderr[-200:])
        except subprocess.TimeoutExpired:
            fail(f"{name} install timed out")
        except Exception as e:
            fail(f"{name} install error: {e}")


# =====================================================================
#  STEP 4 - Configuration
# =====================================================================
def setup_config(total: int, tool_status: Dict[str, bool]) -> None:
    step(4, total, "Configuration")

    if CONFIG_FILE.exists():
        ok(f"Config file already exists: {CONFIG_FILE}")
        if not ask_yes("Overwrite with fresh config?", default=False):
            info(f"Keeping existing config file: {CONFIG_FILE}")
            return

    config: Dict[str, Any] = {}
    print()
    info("Let's configure NZBPostarr. Press Enter to accept defaults.\n")

    # ── Base folder ──────────────────────────────────────────────────
    header("Media Folders")
    info("Where are your media files stored?")
    dim("This is the root directory containing Movies/, TV/, etc.")
    base = ask("Base media folder", str(Path.home() / "usenet"))
    config["base_folder"] = base

    # Scan folders
    print()
    info("Scan folders (contents are auto-categorized)")
    dim("Leave blank to skip a folder.\n")

    prompts = [
        ("Primary movies folder", "Movies"),
        ("Primary TV folder", "TV"),
        ("Primary misc folder", "Misc"),
    ]

    folder_paths: List[Dict[str, object]] = []
    for label, default_sub in prompts:
        default_path = f"{base}/{default_sub}"
        path = ask(label, default_path)
        if path:
            folder_paths.append({"path": path, "monitor": False})

    while ask_yes("Add another scan folder?", default=False):
        path = ask("  Folder path", base)
        if path:
            folder_paths.append({"path": path, "monitor": False})

    config["folder_paths"] = folder_paths

    # ── Identity ─────────────────────────────────────────────────────
    print()
    header("Upload Identity")
    info("How your uploads appear on Usenet.")
    dim("Use a pseudonym - this is public metadata.\n")
    config["poster_name"] = ask("Poster name", "Anonymous")
    config["poster_email"] = ask("Poster email", "anon@example.com")

    # ── NNTP Server ──────────────────────────────────────────────────
    print()
    header("Usenet Server (NNTP)")
    info("You need at least one Usenet provider.")
    dim("Common providers: Frugal, Eweka, Newshosting, Supernews\n")

    servers: List[Dict[str, Any]] = []
    add_server = True
    server_num = 0

    while add_server:
        server_num += 1
        prefix = "Primary" if server_num == 1 else f"Server {server_num}"
        srv_name = ask("  Server name", prefix)
        srv_host = ask("  Hostname", "news.example.com")
        srv_port = int(ask("  Port", "563"))
        srv_ssl = ask_yes("  Use SSL?", default=True)
        srv_user = ask("  Username", "")
        srv_pass = ask("  Password", password=True)
        srv_conns = int(ask("  Max connections", "20"))

        servers.append(
            {
                "name": srv_name,
                "host": srv_host,
                "port": srv_port,
                "ssl": srv_ssl,
                "user": srv_user,
                "pass": srv_pass,
                "max_connections": srv_conns,
                "enabled": True,
                "backbone": ["Default"],
            }
        )
        ok(f"Added server: {srv_name} ({srv_host})")

        if server_num < 3:
            add_server = ask_yes("Add another server?", default=False)
        else:
            add_server = False

    config["nntp_servers"] = servers

    # ── Indexer API Keys ─────────────────────────────────────────────
    print()
    header("Indexer API Keys")
    info("Configure which indexers to upload to.")
    dim("You can add/change these later in the WebUI settings.\n")

    indexers = [
        ("geek", "NZBGeek", "https://nzbgeek.info"),
        ("su", "NZB.Life", "https://nzb.life"),
        ("planet", "NZBPlanet", "https://nzbplanet.net"),
        ("slug", "DrunkenSlug", "https://drunkenslug.com"),
        ("in", "NZBs.in", "https://nzbs.in"),
        ("omg", "OMGwtfnzbs", "https://omgwtfnzbs.me"),
    ]

    api_keys: Dict[str, str] = {}
    usernames: Dict[str, str] = {}

    for idx_id, idx_name, idx_url in indexers:
        dim(f"  {idx_name} - {idx_url}")
        key = ask(f"  {idx_name} API key (blank to skip)", "")
        if key:
            api_keys[idx_id] = key
            # OMG also needs a username
            if idx_id == "omg":
                uname = ask(f"  {idx_name} username", "")
                if uname:
                    usernames[idx_id] = uname

    if api_keys:
        config["api_keys"] = api_keys
    else:
        config["api_keys"] = {}
    if usernames:
        config["usernames"] = usernames
    else:
        config["usernames"] = {}

    # ── Tool Paths (override if non-standard) ────────────────────────
    print()
    header("Tool Configuration")

    nyuu_path = "nyuu"
    parpar_path = "parpar"

    if not tool_status.get("nyuu"):
        nyuu_path = ask("Path to nyuu binary", "nyuu")
    if not tool_status.get("parpar"):
        parpar_path = ask("Path to parpar binary", "parpar")

    config["nyuu_path"] = nyuu_path
    config["parpar_path"] = parpar_path
    config["rar_size"] = ask("RAR split size", "100m")
    config["article_size"] = ask("Article size", "1M")

    # ── Processing Defaults ──────────────────────────────────────────
    print()
    header("Processing Defaults")

    config["process_tv_episodes"] = ask_yes("Process individual TV episodes?", default=True)
    config["dynamic_packs"] = ask_yes("Dynamic pack detection?", default=True)
    config["enable_duplicate_checking"] = ask_yes("Skip already-uploaded items?", default=True)
    config["enable_backfill"] = ask_yes("Enable backfill mode?", default=True)
    config["verbose"] = False
    config["quiet"] = False
    config["test_run"] = False

    # ── Web Interface ────────────────────────────────────────────────
    print()
    header("Web Interface")

    config["host"] = ask("Bind address", "0.0.0.0")
    config["port"] = int(ask("Port", "8000"))
    config["debug"] = False
    config["log_level"] = "INFO"
    config["ui_refresh_seconds"] = 2

    # ── Fill remaining defaults ──────────────────────────────────────
    config.setdefault("alt_bins", ["alt.binaries.misc"])
    config.setdefault("include_readme", True)
    config.setdefault("upload_max_retries", 3)
    config.setdefault("upload_retry_delay_seconds", 5)
    config.setdefault("item_limit_per_category", 0)
    config.setdefault("folder_size_limit_gb", 99)
    config.setdefault("folder_size_limit_enabled", True)
    config.setdefault("file_size_limit_gb", 0)
    config.setdefault("file_size_limit_enabled", True)
    config.setdefault("enable_duplicate_bypass", True)
    config.setdefault("nfolder", None)
    config.setdefault("dashboard_stats_enabled", True)
    config.setdefault(
        "dashboard_stats_modules",
        ["cpu", "memory", "disk", "free_space", "upload", "download"],
    )
    config.setdefault("skip_files", {"enabled": False, "display_mode": "disabled", "patterns": []})

    # ── Write config.yaml ────────────────────────────────────────────
    print()
    info(f"Writing config to: {CONFIG_FILE}")

    import yaml  # should be available since we just installed deps

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write("# NZBPostarr Configuration\n")
        f.write(f"# Generated by setup wizard on {_now_str()}\n")
        f.write("# Edit this file or use the WebUI settings page.\n\n")
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    ok(f"Config file written ({CONFIG_FILE})")


# =====================================================================
#  STEP 5 - Systemd Service (optional)
# =====================================================================
SYSTEMD_UNIT = """\
[Unit]
Description=NZBPostarr - Usenet Upload Manager
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={root}
ExecStart={python} {root}/main.py --direct
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def setup_systemd(total: int) -> None:
    step(5, total, "System Service (optional)")

    if platform.system() != "Linux":
        dim("Systemd is Linux-only - skipping")
        return

    if not cmd_exists("systemctl"):
        dim("systemctl not found - skipping systemd setup")
        return

    if not ask_yes("Create a systemd service? (auto-start on boot)", default=True):
        dim("Skipping systemd setup")
        return

    user = ask("Run as user", os.environ.get("USER", "root"))
    python_path = str(VENV_PY) if VENV_PY.exists() else sys.executable

    unit_content = SYSTEMD_UNIT.format(
        user=user,
        root=str(ROOT),
        python=python_path,
    )

    unit_path = Path("/etc/systemd/system/nzbpostarr.service")
    is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False

    print()
    dim("Service file contents:")
    for line in unit_content.strip().split("\n"):
        dim(f"  {line}")

    if not ask_yes(f"Write to {unit_path}?"):
        # Offer to save locally instead
        local_path = ROOT / "nzbpostarr.service"
        with open(local_path, "w") as f:
            f.write(unit_content)
        ok(f"Saved locally: {local_path}")
        info(f"Install manually: sudo cp {local_path} {unit_path}")
        info("Then: sudo systemctl daemon-reload && sudo systemctl enable nzbpostarr")
        return

    try:
        sudo = "" if is_root else "sudo "
        tmp = ROOT / ".tmp_unit"
        with open(tmp, "w") as f:
            f.write(unit_content)

        run(f"{sudo}cp {tmp} {unit_path}", check=True)
        tmp.unlink(missing_ok=True)

        run(f"{sudo}systemctl daemon-reload", check=True)

        if ask_yes("Enable service to start on boot?"):
            run(f"{sudo}systemctl enable nzbpostarr", check=True)
            ok("Service enabled (starts on boot)")

        if ask_yes("Start NZBPostarr now?", default=False):
            run(f"{sudo}systemctl start nzbpostarr", check=True)
            ok("Service started!")
            info("Check status: systemctl status nzbpostarr")

    except Exception as e:
        fail(f"Systemd setup failed: {e}")
        warn("You can start manually: python main.py")


# =====================================================================
#  STEP 6 - Smoke Test
# =====================================================================
def smoke_test(total: int) -> None:
    step(6, total, "Verification")

    checks_passed = 0
    checks_total = 0

    # 1. Config loads
    checks_total += 1
    info("Testing config load...")
    try:
        r = run(
            f"{VENV_PY} -c \"from core.config import load_config; c = load_config(); print(f'OK: port={{c.port}}, {{len(c.nntp_servers)}} server(s)')\"",
            check=False,
            capture=True,
        )
        if r.returncode == 0:
            ok(f"Config: {r.stdout.strip()}")
            checks_passed += 1
        else:
            fail(f"Config load failed: {r.stderr.strip()[:200]}")
    except Exception as e:
        fail(f"Config test error: {e}")

    # 2. Database initializes
    checks_total += 1
    info("Testing database init...")
    try:
        r = run(
            f"{VENV_PY} -c \"from core.db.schema import init_database; init_database(); print('OK')\"",
            check=False,
            capture=True,
        )
        if r.returncode == 0:
            ok("Database: initialized")
            checks_passed += 1
        else:
            fail(f"Database init failed: {r.stderr.strip()[:200]}")
    except Exception as e:
        fail(f"Database test error: {e}")

    # 3. Indexer registry loads
    checks_total += 1
    info("Testing indexer registry...")
    try:
        r = run(
            f"{VENV_PY} -c \"from core.registry import get_registry; r = get_registry(); print(f'OK: {{len(r.all())}} indexers loaded')\"",
            check=False,
            capture=True,
        )
        if r.returncode == 0:
            ok(f"Indexers: {r.stdout.strip()}")
            checks_passed += 1
        else:
            fail(f"Indexer load failed: {r.stderr.strip()[:200]}")
    except Exception as e:
        fail(f"Indexer test error: {e}")

    # 4. External tools
    for tool_name in REQUIRED_TOOLS:
        checks_total += 1
        t = TOOLS_INFO[tool_name]
        if cmd_exists(t["check"]):
            ok(f"Tool: {tool_name} ✓")
            checks_passed += 1
        else:
            warn(f"Tool: {tool_name} - not found (uploads will fail until installed)")

    # 5. Folder paths exist
    if CONFIG_FILE.exists():
        try:
            import yaml

            with open(CONFIG_FILE) as f:
                data = yaml.safe_load(f) or {}
            for fp in data.get("folder_paths", []):
                path = fp.get("path", "")
                cat = fp.get("category", "?")
                checks_total += 1
                if path and Path(path).exists():
                    ok(f"Folder [{cat}]: {path}")
                    checks_passed += 1
                elif path:
                    warn(f"Folder [{cat}]: {path} - does not exist yet")
                    if ask_yes(f"  Create {path}?", default=True):
                        try:
                            Path(path).mkdir(parents=True, exist_ok=True)
                            ok(f"  Created: {path}")
                            checks_passed += 1
                        except OSError as e:
                            fail(f"  Could not create: {e}")
        except Exception:
            pass

    # Summary
    print()
    pct = int(checks_passed / checks_total * 100) if checks_total else 0
    if pct == 100:
        ok(f"All {checks_total} checks passed!")
    elif pct >= 70:
        warn(f"{checks_passed}/{checks_total} checks passed ({pct}%) - some issues to address")
    else:
        fail(f"{checks_passed}/{checks_total} checks passed ({pct}%) - review errors above")


# =====================================================================
#  STEP 7 - Done!
# =====================================================================
def show_summary() -> None:
    print()
    print(f"  {C['blue']}{'━' * 56}{C['reset']}")
    print(f"  {C['bold']}{C['green']}  Setup Complete!{C['reset']}")
    print(f"  {C['blue']}{'━' * 56}{C['reset']}")
    print()
    print(f"  {C['bold']}Quick Start:{C['reset']}")
    print()
    print(f"    {C['cyan']}Start NZBPostarr:{C['reset']}")
    print(f"      cd {ROOT}")
    print("      python main.py")
    print()
    print(f"    {C['cyan']}Or with systemd:{C['reset']}")
    print("      sudo systemctl start nzbpostarr")
    print()
    print(f"    {C['cyan']}WebUI:{C['reset']}")

    port = 8000
    try:
        import yaml

        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = yaml.safe_load(f) or {}
            port = data.get("port", 8000)
    except Exception:
        pass

    print(f"      http://YOUR_SERVER_IP:{port}")
    print()
    print(f"    {C['cyan']}Edit configuration:{C['reset']}")
    print(f"      nano {CONFIG_FILE}")
    print("      (or use the WebUI Settings page)")
    print()

    dim("  Logs:       data/logs/nzbpostarr.log")
    dim("  Database:   data/history/usenet_uploads.db")
    dim("  Temp files: data/tmp/")
    print()


# =====================================================================
#  HELPERS
# =====================================================================
def _now_str() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# =====================================================================
#  MAIN
# =====================================================================
def main() -> None:
    banner()

    # Quick pre-flight: are we already set up?
    if CONFIG_FILE.exists() and VENV_PY.exists():
        ok("NZBPostarr appears to be already configured.")
        choice = ask_choice(
            "What would you like to do?",
            [
                "Run full setup again (overwrite config)",
                "Re-install dependencies only",
                "Check system & run smoke test",
                "Exit",
            ],
            default=0,
        )
        if choice == 3:
            return
        elif choice == 1:
            total = 2
            setup_venv(1)
            smoke_test(2)
            return
        elif choice == 2:
            total = 2
            check_system(1)
            smoke_test(2)
            return
        # choice == 0: fall through to full setup

    total = 6
    check_system(total)
    setup_venv(total)
    tool_status = check_tools(total)
    setup_config(total, tool_status)
    setup_systemd(total)
    smoke_test(total)
    show_summary()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {C['yellow']}Setup cancelled.{C['reset']}\n")
        sys.exit(1)
