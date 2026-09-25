"""Source-text pins on the dashboard page script."""

from tests.webui._source import _read_repo_text


def test_webui_dashboard_performance_guards_are_present() -> None:
    dashboard_js = _read_repo_text("webui", "assets", "js", "pages", "dashboard.js")

    assert "const CONSOLE_MAX_LINES = 300;" in dashboard_js
    assert "this.consoleLogs.splice(0, this.consoleLogs.length - CONSOLE_MAX_LINES);" in dashboard_js
    assert "this.consoleLogs.shift();" not in dashboard_js
    assert "behavior: 'auto'" in dashboard_js
    assert "scroll-smooth" not in dashboard_js
