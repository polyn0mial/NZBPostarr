"""`config get|set`: dotted configuration keys through the validated writer.

App modules are imported inside the functions that use them, so the CLI starts without loading the app.
"""

from __future__ import annotations

import argparse
from typing import Any

from cli.output import _emit_result


def _config_path_segments(path: str) -> list[str]:
    """Normalize a dotted path and reject ambiguous empty segments."""
    segments = path.split(".")
    if not path or any(not segment.strip() for segment in segments):
        raise ValueError("configuration keys must contain non-empty dotted path segments")
    return [segment.strip() for segment in segments]


def _config_value_at_path(data: Any, path: str) -> Any:
    """Read a dotted config path without exposing a second config model."""
    current = data
    for segment in _config_path_segments(path):
        if not isinstance(current, dict) or segment not in current:
            raise KeyError(path)
        current = current[segment]
    return current


def _set_config_value_at_path(data: dict[str, Any], path: str, value: Any) -> None:
    """Set a dotted config path, requiring all parent mappings to exist."""
    segments = _config_path_segments(path)
    current: dict[str, Any] = data
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            raise KeyError(path)
        current = child
    current[segments[-1]] = value


def cmd_config(args: argparse.Namespace) -> int:
    """Read or update configuration through core.config's synchronized writer."""
    from core.config import get_config, save_config

    command = getattr(args, "config_command", None)
    path = str(getattr(args, "key", "") or "").strip()
    try:
        data = get_config().model_dump(mode="json", by_alias=True)
    except Exception as exc:
        return _emit_result(
            args,
            {"status": "error", "message": f"Unable to load configuration: {exc}"},
            human=f"Error: unable to load configuration: {exc}",
            rc=1,
        )

    if command == "get":
        try:
            value = _config_value_at_path(data, path)
        except ValueError as exc:
            return _emit_result(
                args,
                {"status": "error", "message": str(exc)},
                human=f"Error: {exc}",
                rc=1,
            )
        except KeyError:
            return _emit_result(
                args,
                {"status": "error", "message": f"Configuration key '{path}' was not found."},
                human=f"Error: configuration key '{path}' was not found.",
                rc=1,
            )
        return _emit_result(args, {"key": path, "value": value}, human=str(value))

    if command == "set":
        import yaml

        try:
            value = yaml.safe_load(args.value)
            _set_config_value_at_path(data, path, value)
        except (KeyError, ValueError, yaml.YAMLError) as exc:
            return _emit_result(
                args,
                {"status": "error", "message": f"Invalid configuration update: {exc}"},
                human=f"Error: invalid configuration update: {exc}",
                rc=1,
            )
        if not save_config(data):
            return _emit_result(
                args,
                {"status": "error", "message": "Failed to save configuration."},
                human="Error: failed to save configuration.",
                rc=1,
            )
        return _emit_result(
            args,
            {"status": "updated", "key": path, "value": value, "restart_required": True},
            human=f"Updated {path}. Restart the long-lived NZBPostarr process to apply all watcher changes.",
        )

    return _emit_result(
        args,
        {"status": "error", "message": "No config command given."},
        human="Error: use 'config get' or 'config set'.",
        rc=1,
    )
