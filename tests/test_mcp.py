"""MCP endpoint tests.

The `mcp` package is an optional extra, so everything here runs without it
installed. Tests that genuinely need the SDK skip cleanly.
"""

from types import SimpleNamespace

from fastapi import Request
import pytest

from api import auth as auth_api, mcp as mcp_api

from tests.conftest import patch_hit


_READ_TOOLS = {
    "get_status",
    "list_jobs",
    "get_job",
    "get_job_items",
    "get_dashboard",
    "get_stats",
    "list_indexers",
    "get_history",
    "get_recent_errors",
    "get_logs",
}
_MUTATING_TOOLS = {
    "trigger_upload",
    "pause_queue",
    "resume_queue",
    "pause_job",
    "resume_job",
    "stop_job",
    "retry_job",
}

def _request(path: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": headers or [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    return Request(scope)

def test_tool_table_covers_read_and_mutating_surface() -> None:
    tools = mcp_api.build_tool_table()

    assert _READ_TOOLS | _MUTATING_TOOLS == set(tools)
    # Every mutating tool must be declared as such, or the read-only default leaks writes.
    assert _MUTATING_TOOLS == set(mcp_api._MUTATING_TOOLS)
    for name, fn in tools.items():
        assert callable(fn), name
        assert (fn.__doc__ or "").strip(), f"{name} needs a docstring: it becomes the MCP tool description"

def test_mcp_stays_disabled_without_explicit_config() -> None:
    assert mcp_api.mcp_configured(SimpleNamespace(mcp_enabled=False, mcp_token=None)) is False
    assert mcp_api.mcp_configured(SimpleNamespace(mcp_enabled=False, mcp_token="tok")) is False
    # Enabled but untokenized must NOT mount: the endpoint can control the queue.
    assert mcp_api.mcp_configured(SimpleNamespace(mcp_enabled=True, mcp_token="")) is False
    assert mcp_api.mcp_configured(SimpleNamespace(mcp_enabled=True, mcp_token="   ")) is False
    assert mcp_api.mcp_configured(SimpleNamespace(mcp_enabled=True, mcp_token="tok")) is True

def test_build_mcp_asgi_app_returns_none_when_not_configured() -> None:
    assert mcp_api.build_mcp_asgi_app(SimpleNamespace(mcp_enabled=False, mcp_token="tok")) is None
    assert mcp_api.build_mcp_asgi_app(SimpleNamespace(mcp_enabled=True, mcp_token="")) is None

def test_mcp_token_check_rejects_bad_credentials(monkeypatch) -> None:
    patch_hit(monkeypatch, auth_api, "get_config", lambda: SimpleNamespace(mcp_token="s3cret"))

    assert auth_api._verify_mcp_token(_request("/mcp")) is False
    assert auth_api._verify_mcp_token(_request("/mcp", [(b"authorization", b"Bearer wrong")])) is False
    assert auth_api._verify_mcp_token(_request("/mcp", [(b"authorization", b"Basic s3cret")])) is False
    assert auth_api._verify_mcp_token(_request("/mcp", [(b"authorization", b"s3cret")])) is False
    assert auth_api._verify_mcp_token(_request("/mcp", [(b"authorization", b"Bearer s3cret")])) is True

def test_mcp_token_check_fails_closed_when_no_token_configured(monkeypatch) -> None:
    patch_hit(monkeypatch, auth_api, "get_config", lambda: SimpleNamespace(mcp_token=""))

    # An empty configured token must never authenticate an empty presented token.
    assert auth_api._verify_mcp_token(_request("/mcp", [(b"authorization", b"Bearer ")])) is False
    assert auth_api._verify_mcp_token(_request("/mcp", [(b"authorization", b"Bearer x")])) is False

def test_mcp_path_is_not_in_the_public_auth_bypass_lists() -> None:
    # /mcp gets its own bearer branch; it must never be silently public.
    assert "/mcp" not in auth_api._AUTH_PUBLIC_PATHS
    for prefix in auth_api._AUTH_PUBLIC_PREFIXES:
        assert not "/mcp".startswith(prefix)

def test_list_indexers_tool_never_returns_credential_values() -> None:
    # Exercises the real registry projection, not a stub: to_ui_dict is what keeps
    # credentials out, and this tool must not bypass it.
    payload = mcp_api.build_tool_table()["list_indexers"]()
    rows = payload["indexers"]

    assert rows, "expected the bundled indexers to load"
    for row in rows:
        # The field names are present and always blank; only the boolean says
        # whether a key is configured.
        assert row.get("api_key") == ""
        assert row.get("username") == ""
        assert isinstance(row.get("has_api_key"), bool)

def test_build_mcp_asgi_app_gates_mutating_tools(monkeypatch) -> None:
    if not mcp_api.mcp_available():
        pytest.skip("optional 'mcp' package is not installed")

    registered: list[str] = []

    class _FakeServer:
        def __init__(self, **_kwargs) -> None:
            pass

        def add_tool(self, _fn, name: str = "") -> None:
            registered.append(name)

        def streamable_http_app(self) -> object:
            return object()

    import mcp.server.fastmcp as fastmcp_mod

    monkeypatch.setattr(fastmcp_mod, "FastMCP", _FakeServer)

    mcp_api.build_mcp_asgi_app(
        SimpleNamespace(mcp_enabled=True, mcp_token="tok", mcp_allow_mutations=False, mcp_allowed_hosts=["*"])
    )
    assert set(registered) == _READ_TOOLS

    registered.clear()
    mcp_api.build_mcp_asgi_app(
        SimpleNamespace(mcp_enabled=True, mcp_token="tok", mcp_allow_mutations=True, mcp_allowed_hosts=["*"])
    )
    assert set(registered) == _READ_TOOLS | _MUTATING_TOOLS
