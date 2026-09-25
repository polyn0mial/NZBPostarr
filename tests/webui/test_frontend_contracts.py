"""Snapshot of the user-visible dialog messages and the browser storage keys.

A refactor of the web UI sources must keep every confirmDialog/promptDialog
message and every localStorage/sessionStorage key literal. Set
NZBP_UPDATE_SNAPSHOTS=1 to rewrite contracts.json after an intended change.
"""

import json
import os
import re
from pathlib import Path

from tests.webui._source import REPO_ROOT

CONTRACTS = Path(__file__).resolve().parent / "contracts.json"
WEBUI = REPO_ROOT / "webui"
_SKIPPED_DIRS = {"dist", "vendor", "node_modules"}

_STRING = r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`"""
_DIALOG_CALL = re.compile(rf"\b(confirmDialog|promptDialog)\(\s*({_STRING})")
_STORAGE_KEY = re.compile(r"""['"`](nzb(?:postarr)?_[A-Za-z0-9_]*)""")
_PERSIST_REV = re.compile(r"""QUEUE_PERSIST_REV\s*=\s*['"]([^'"]+)['"]|data-persist-rev="([^"]+)\"""")


def _message(literal: str) -> str:
    """The message text without its quotes, so a quote-style change is not a contract change."""
    quote = literal[0]
    return literal[1:-1].replace("\\" + quote, quote)


def _sources() -> list[Path]:
    files: list[Path] = []
    for folder, dirnames, filenames in os.walk(WEBUI):
        dirnames[:] = [name for name in dirnames if name not in _SKIPPED_DIRS]
        files.extend(Path(folder) / name for name in filenames if name.endswith((".js", ".mjs", ".html")))
    return sorted(files)


def _contracts() -> dict[str, list]:
    dialogs: list[list[str]] = []
    storage_keys: set[str] = set()
    persist_revs: set[str] = set()
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        dialogs.extend([kind, _message(literal)] for kind, literal in _DIALOG_CALL.findall(text))
        storage_keys.update(_STORAGE_KEY.findall(text))
        persist_revs.update(value for match in _PERSIST_REV.findall(text) for value in match if value)
    return {
        "dialogs": sorted(dialogs),
        "storage_keys": sorted(storage_keys),
        "persist_revs": sorted(persist_revs),
    }


def test_frontend_contracts_match_snapshot() -> None:
    current = _contracts()
    if os.environ.get("NZBP_UPDATE_SNAPSHOTS") == "1":
        CONTRACTS.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return
    assert current == json.loads(CONTRACTS.read_text(encoding="utf-8"))
