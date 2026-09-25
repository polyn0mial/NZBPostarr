"""Characterization of the HTTP route table, the middleware stack and the CLI surface."""

import argparse
import sys
from typing import Any, Iterator

import pytest

import app as app_mod
from logic import headless as headless_mod
from logic.mcp_server import MCP_PATH
from tests.characterization._snapshot import HERE, assert_json_snapshot, assert_text_snapshot, updating

CLI_HELP_DIR = HERE / "cli_help"


def _effective_routes(routes: list[Any]) -> Iterator[Any]:
    """Yield routes in match order, expanding included routers in place."""
    for route in routes:
        expand = getattr(route, "effective_route_contexts", None)
        if callable(expand):
            yield from expand()
        else:
            yield route


def _route_table() -> list[list[Any]]:
    table: list[list[Any]] = []
    for route in _effective_routes(list(app_mod.app.routes)):
        path = str(getattr(route, "path", ""))
        # The MCP endpoint is mounted only when the optional extra is installed and enabled.
        if path == MCP_PATH:
            continue
        table.append([sorted(getattr(route, "methods", None) or []), path])
    return table


def _middleware_stack() -> list[str]:
    stack: list[str] = []
    for middleware in app_mod.app.user_middleware:
        name = middleware.cls.__name__
        dispatch = middleware.kwargs.get("dispatch")
        if dispatch is not None:
            name = f"{name}:{dispatch.__name__}"
        stack.append(name)
    return stack


def _segments(path: str) -> list[str]:
    return path.split("/")[1:]


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _shadows(general: str, specific: str) -> bool:
    """True when every request for specific would also match general."""
    general_parts, specific_parts = _segments(general), _segments(specific)
    if len(general_parts) != len(specific_parts) or general == specific:
        return False
    for general_part, specific_part in zip(general_parts, specific_parts):
        if _is_param(general_part):
            if not specific_part or _is_param(specific_part):
                return False
        elif general_part != specific_part:
            return False
    return True


def test_route_table_matches_snapshot() -> None:
    assert_json_snapshot(HERE / "routes.json", _route_table())


def test_literal_routes_precede_the_parameter_routes_that_would_shadow_them() -> None:
    table = _route_table()
    checked = 0
    for general_index, (general_methods, general_path) in enumerate(table):
        for specific_index, (specific_methods, specific_path) in enumerate(table):
            if not set(general_methods) & set(specific_methods):
                continue
            if _shadows(general_path, specific_path):
                checked += 1
                assert specific_index < general_index, f"{specific_methods} {specific_path} after {general_path}"

    assert checked > 0, "no literal route is shadowed by a parameter route"
    order = [(tuple(methods), path) for methods, path in table]
    assert order.index((("DELETE",), "/api/uploads/jobs/completed")) < order.index(
        (("DELETE",), "/api/uploads/jobs/{job_id}")
    )
    assert table[-1] == [["GET"], "/{page_name}"]


def test_middleware_order_matches_snapshot() -> None:
    assert_json_snapshot(HERE / "middleware.json", _middleware_stack())


def _subparsers(parser: argparse.ArgumentParser) -> Iterator[tuple[str, argparse.ArgumentParser]]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            yield from action.choices.items()


def _walk_parsers(
    parser: argparse.ArgumentParser, names: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    yield names, parser
    for name, child in _subparsers(parser):
        yield from _walk_parsers(child, (*names, name))


def _describe_action(action: argparse.Action) -> dict[str, Any]:
    choices = action.choices
    if isinstance(choices, dict):
        choices = list(choices)
    elif choices is not None:
        choices = [str(choice) for choice in choices]
    return {
        "option_strings": list(action.option_strings),
        "dest": action.dest,
        "nargs": action.nargs if action.nargs is None or isinstance(action.nargs, (int, str)) else str(action.nargs),
        "metavar": action.metavar if action.metavar is None or isinstance(action.metavar, str) else list(action.metavar),
        "default": repr(action.default),
        "type": getattr(action.type, "__name__", repr(action.type)) if action.type is not None else None,
        "choices": choices,
        "required": bool(action.required),
        "help": action.help,
    }


def _cli_surface() -> dict[str, Any]:
    surface: dict[str, Any] = {}
    for names, parser in _walk_parsers(headless_mod.build_headless_parser()):
        surface[" ".join(names) or "(root)"] = {
            "prog": parser.prog,
            "description": parser.description,
            "epilog": parser.epilog,
            "actions": [_describe_action(action) for action in parser._actions],
        }
    return surface


def test_cli_surface_matches_snapshot() -> None:
    assert_json_snapshot(CLI_HELP_DIR / "surface.json", _cli_surface())


def test_cli_help_text_matches_snapshot(monkeypatch) -> None:
    """format_help() of every parser, pinned for the Python minor version that recorded it.

    argparse wording changes between Python releases, so other versions rely on
    test_cli_surface_matches_snapshot, which pins the same content structurally.
    """
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("PYTHON_COLORS", "0")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    version_file = CLI_HELP_DIR / "python_version.txt"
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if updating():
        assert_text_snapshot(version_file, f"{running}\n")
    elif version_file.read_text(encoding="utf-8").strip() != running:
        pytest.skip(f"help text recorded on another Python version than {running}")

    helps = {
        "-".join(("headless", *names)): parser.format_help()
        for names, parser in _walk_parsers(headless_mod.build_headless_parser())
    }
    assert_json_snapshot(CLI_HELP_DIR / "index.json", sorted(helps))
    for name, text in helps.items():
        assert_text_snapshot(CLI_HELP_DIR / f"{name}.txt", text)
