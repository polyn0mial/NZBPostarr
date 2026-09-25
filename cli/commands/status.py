"""`status`: configuration, indexer and tool readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cli.output import _print_status_report


def cmd_status(args: argparse.Namespace) -> int:
    """Show current configuration and system status.

    Exit codes: 0 when every required tool (rar, parpar, nyuu) is found on
    PATH; 1 when any required tool is missing. mediainfo is optional and
    does not affect the exit code.
    """
    from core.config import get_config
    from core.registry import get_registry, resolve_indexer_enabled

    conf = get_config()
    registry = get_registry()
    enabled = registry.enabled(conf)
    all_idx = registry.all()

    import shutil

    required_tools = ["rar", "parpar", "nyuu"]
    tool_paths = {tool: shutil.which(tool) for tool in required_tools + ["mediainfo"]}
    missing_required = [tool for tool in required_tools if not tool_paths[tool]]

    if args.json:
        payload = {
            "nntp_servers": [
                {
                    "name": srv.name,
                    "host": srv.host,
                    "port": srv.port,
                    "enabled": bool(srv.enabled),
                    "max_connections": srv.max_connections,
                }
                for srv in conf.nntp_servers
            ],
            "indexers": {
                "enabled": len(enabled),
                "total": len(all_idx),
                "items": [
                    {"id": idx.id, "name": idx.name, "enabled": resolve_indexer_enabled(idx, conf)}
                    for idx in all_idx
                ],
            },
            "folders": [
                {
                    "path": fp.get("path", "?"),
                    "exists": Path(fp["path"]).exists() if fp.get("path") else False,
                    "monitor": bool(fp.get("monitor")),
                }
                for fp in conf.folder_paths
            ],
            "tools": tool_paths,
            "missing_required_tools": missing_required,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 1 if missing_required else 0

    return _print_status_report(conf, enabled, all_idx, required_tools, tool_paths, missing_required, resolve_indexer_enabled)
