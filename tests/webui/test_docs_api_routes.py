"""The API reference in docs.html only lists routes the app actually serves."""

import re

import app as app_mod
from tests.webui._source import _read_repo_text

_PARAM = re.compile(r"\{[^}]+\}")
_CODE_PATH = re.compile(r"<code[^>]*>(/api/[^<\s]*)</code>")
# The method badges abbreviate DELETE.
_BADGE_METHOD = {"DEL": "delete"}
_ENDPOINT_ROW = re.compile(
    r'<span class="doc-badge badge-\w+[^"]*">(\w+)</span>\s*<code class="doc-code[^"]*">(/api/[^<\s]*)</code>'
)


def _normalise(path: str) -> str:
    """Path parameter names differ between the docs ({id}) and the app ({job_id})."""
    return _PARAM.sub("{}", path)


def _served_routes() -> dict[str, set[str]]:
    """Normalised path -> lower-case methods, from the schema the app publishes."""
    served: dict[str, set[str]] = {}
    for path, operations in app_mod.app.openapi()["paths"].items():
        served.setdefault(_normalise(path), set()).update(operations)
    return served


def test_docs_page_lists_only_served_api_paths() -> None:
    docs = _read_repo_text("webui", "docs.html")
    served = _served_routes()

    # "/api/*" in the overview names the prefix, not a route.
    shown = {path for path in _CODE_PATH.findall(docs) if "*" not in path}
    assert shown, "docs.html API reference not found"
    missing = sorted(path for path in shown if _normalise(path) not in served)
    assert missing == [], f"docs.html lists routes the app does not serve: {missing}"


def test_docs_endpoint_rows_use_served_methods() -> None:
    docs = _read_repo_text("webui", "docs.html")
    served = _served_routes()

    rows = _ENDPOINT_ROW.findall(docs)
    assert rows, "docs.html endpoint rows not found"
    wrong = sorted(
        f"{method} {path}"
        for method, path in rows
        if _BADGE_METHOD.get(method, method.lower()) not in served.get(_normalise(path), set())
    )
    assert wrong == [], f"docs.html shows methods the app does not serve: {wrong}"
