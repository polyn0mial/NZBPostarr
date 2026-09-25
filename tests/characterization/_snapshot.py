"""Compare values with committed snapshot files.

Set NZBP_UPDATE_SNAPSHOTS=1 to rewrite the snapshots instead of comparing; a
batch that changes a snapshot on purpose lists the change in its report.
"""

import json
import os
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def updating() -> bool:
    return os.environ.get("NZBP_UPDATE_SNAPSHOTS") == "1"


def _dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def assert_json_snapshot(path: Path, value: Any) -> None:
    if updating():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump_json(value), encoding="utf-8", newline="\n")
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert json.loads(_dump_json(value)) == expected, f"{path.name} changed"


def assert_text_snapshot(path: Path, text: str) -> None:
    if updating():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    # A checkout with core.autocrlf may turn the committed LF endings into CRLF.
    assert text == path.read_text(encoding="utf-8").replace("\r\n", "\n"), f"{path.name} changed"
