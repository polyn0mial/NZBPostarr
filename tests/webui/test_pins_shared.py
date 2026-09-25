"""Source-text pins on the shared page base script and stylesheets."""

from tests.webui._source import _read_repo_text


def test_webui_shared_performance_guards_are_present() -> None:
    page_base_js = _read_repo_text("webui", "assets", "js", "page-base.js")
    core_css = _read_repo_text("webui", "assets", "css", "core.css")

    assert "startTimeout(fn, ms)" in page_base_js
    assert "clearTimeout(this._healthTimer);" in page_base_js
    assert "document.removeEventListener('click', this._touchInfotipHandler);" in page_base_js
    assert "content-visibility: auto;" in core_css


def test_queue_job_count_describes_remaining_items() -> None:
    page_base_js = _read_repo_text("webui", "assets", "js", "page-base.js")

    assert "target_path_count" in page_base_js
    assert "not uploaded`" in page_base_js
