from types import SimpleNamespace

from core import tools
from tests.conftest import _run_async


def _fake_tools(tmp_path):
    paths = {}
    for name in tools.PROCESSING_TOOLS:
        path = tmp_path / "bin" / f"{name}.exe"
        path.parent.mkdir(exist_ok=True)
        path.write_text("", encoding="utf-8")
        paths[name] = path
    return paths


def test_configured_path_wins_over_path_lookup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    configured = _fake_tools(tmp_path)["rar"]

    assert tools.resolve_tool("rar", str(configured)) == str(configured)
    assert tools.resolve_tool("rar") is None


def test_missing_configured_path_is_missing_even_when_on_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tools.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert tools.resolve_tool("rar", str(tmp_path / "nope" / "rar")) is None
    assert tools.resolve_tool("rar", "") == "/usr/bin/rar"
    assert tools.resolve_tool("rar", "rar5") == "/usr/bin/rar5"


def test_check_tools_reads_each_configured_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    paths = _fake_tools(tmp_path)
    conf = SimpleNamespace(**{f"{name}_path": str(path) for name, path in paths.items()})

    assert tools.check_tools(conf) == {name: str(path) for name, path in paths.items()}
    assert tools.check_tools(SimpleNamespace()) == {name: None for name in tools.PROCESSING_TOOLS}


def test_tool_version_is_none_for_a_missing_tool(monkeypatch) -> None:
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)

    assert tools.tool_version("definitely-not-installed") is None


def test_health_uses_configured_tool_paths(tmp_path, monkeypatch) -> None:
    from api import system as system_api

    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    paths = _fake_tools(tmp_path)
    folder = tmp_path / "media"
    folder.mkdir()
    conf = SimpleNamespace(
        **{f"{name}_path": str(path) for name, path in paths.items()},
        nntp_servers=[],
        get_nzb_path=lambda _name: tmp_path / "nzb" / "test.nzb",
    )
    monkeypatch.setattr(system_api, "get_config", lambda: conf)
    monkeypatch.setattr(system_api, "get_configured_folders", lambda _conf: [folder])

    result = _run_async(system_api.health_check())

    assert result["tools"] == {name: True for name in tools.PROCESSING_TOOLS}
    assert result["status"] == "Healthy"
