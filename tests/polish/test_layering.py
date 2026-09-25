"""Import layering: core < logic < api/cli, and the stdlib-only bootstrap entry points."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterator, Tuple

ROOT = Path(__file__).resolve().parents[2]

# Known upward imports still to be removed by the batch that owns the module; each entry is
# (importing file, imported module). Shrink this list, never grow it.
KNOWN_UPWARD = {
    ("core/utils.py", "logic.services"),  # run_command reaches UploadService for stop checks
}


def _imports(path: Path, top_level_only: bool = False) -> Iterator[Tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = tree.body if top_level_only else ast.walk(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def _top(module: str) -> str:
    return module.split(".", 1)[0]


def _violations(package: str, forbidden: set[str]) -> list[str]:
    found = []
    for path in sorted((ROOT / package).rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for lineno, module in _imports(path):
            if _top(module) in forbidden and (rel, module) not in KNOWN_UPWARD:
                found.append(f"{rel}:{lineno} imports {module}")
    return found


def test_core_imports_nothing_above_it() -> None:
    assert _violations("core", {"logic", "api", "cli"}) == []


def test_logic_imports_nothing_from_api_or_cli() -> None:
    assert _violations("logic", {"api", "cli"}) == []


def test_bootstrap_imports_only_the_stdlib() -> None:
    path = ROOT / "cli" / "launcher" / "bootstrap.py"
    bad = [
        f"{lineno} {module}"
        for lineno, module in _imports(path)
        if _top(module) not in sys.stdlib_module_names and module != "__future__"
    ]
    assert bad == []


def test_setup_imports_only_the_stdlib_before_install() -> None:
    """setup.py runs before the venv exists: module level is stdlib + the stdlib-only bootstrap.

    Function-level imports (yaml, core.config) run only after install_requirements.
    """
    allowed = {"cli.launcher.bootstrap", "core.tools"}
    bad = [
        f"{lineno} {module}"
        for lineno, module in _imports(ROOT / "setup.py", top_level_only=True)
        if _top(module) not in sys.stdlib_module_names and module not in allowed
    ]
    assert bad == []
