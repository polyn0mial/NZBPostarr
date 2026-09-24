from __future__ import annotations

import time

from logic import process_reaper


class _Proc:
    def __init__(self, info: dict, extra: dict | None = None) -> None:
        self.info = info
        self._extra = extra

    def as_dict(self, attrs):
        assert self._extra is not None, "status/ppid must be read only for candidate processes"
        return {key: self._extra.get(key) for key in attrs}


def test_stale_scan_reads_status_and_ppid_only_for_candidates(monkeypatch) -> None:
    old = time.time() - process_reaper.TOOL_TIMEOUT_S - 60
    rows = [
        _Proc({"pid": 10, "name": "explorer.exe", "create_time": old, "cmdline": ["explorer.exe"]}),
        _Proc(
            {"pid": 11, "name": "nyuu", "create_time": time.time(), "cmdline": ["nyuu"]},
            {"status": process_reaper.psutil.STATUS_ZOMBIE, "ppid": 1},
        ),
        _Proc(
            {"pid": 12, "name": "python3", "create_time": old, "cmdline": ["python3", "upload.py"]},
            {"status": process_reaper.psutil.STATUS_RUNNING, "ppid": 999},
        ),
        _Proc(
            {"pid": 13, "name": "python3", "create_time": old, "cmdline": ["python3", "upload.py"]},
            {"status": process_reaper.psutil.STATUS_RUNNING, "ppid": 7},
        ),
    ]
    monkeypatch.setattr(process_reaper.psutil, "process_iter", lambda _attrs: rows)

    stale = process_reaper._find_stale_processes(protected=set(), own_tree={7})

    assert [(proc.pid, proc.reason) for proc in stale] == [(11, "zombie_tool"), (12, "orphan_upload_script")]
