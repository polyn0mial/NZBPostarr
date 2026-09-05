# ruff: noqa: F403,F405

"""Frontend/backend API contract tests.

The queue page's "Mark Uploaded" action and the history page's group expansion
both shipped calling endpoints that were never routed, so they 404'd silently in
the browser while lint, types and every other test stayed green. This module
turns that class of bug into a test failure.
"""

import re

from tests.support import *

# Frontend sources that may contain /api/... calls.
_JS_ROOT = REPO_ROOT / "webui" / "assets" / "js"
_TEMPLATE_ROOT = REPO_ROOT / "webui"

# URLs the scanner cannot resolve statically. Keep this list short and justified;
# every entry is a hole in the check.
_ALLOWED_UNRESOLVABLE = {
    # Built as `/api/system/update/status${suffix}` where suffix is "" or "?force=true".
    "/api/system/update/statusX",
}

_CALL_RE = re.compile(r"""["'`](/api/[^"'`\s]*)["'`]""")


def _frontend_sources():
    sources = [p for p in _JS_ROOT.rglob("*.js") if "dist" not in p.parts and "vendor" not in p.parts]
    sources += sorted(_TEMPLATE_ROOT.glob("*.html"))
    sources += sorted((_TEMPLATE_ROOT / "partials").glob("*.html"))
    return sources


def _normalize(url: str) -> str:
    """Collapse template placeholders and drop the query string."""
    url = re.sub(r"\$\{[^}]*\}", "X", url)
    return url.split("?")[0].rstrip("/") or "/"


def _collect_frontend_calls():
    calls: dict[str, list[str]] = {}
    for src in _frontend_sources():
        text = src.read_text(encoding="utf-8", errors="replace")
        for match in _CALL_RE.finditer(text):
            url = _normalize(match.group(1))
            line = text[: match.start()].count("\n") + 1
            calls.setdefault(url, []).append(f"{src.relative_to(REPO_ROOT)}:{line}")
    return calls


def _registered_path_matchers():
    """Regexes for every path FastAPI actually serves, from the OpenAPI schema."""
    matchers = []
    for path in app_mod.app.openapi().get("paths", {}):
        pattern = re.escape(path)
        # re.escape leaves { } alone on modern Python; turn {param} into a segment match.
        pattern = re.sub(r"\\?\{[^}]+\\?\}", r"[^/]+", pattern)
        matchers.append((path, re.compile("^" + pattern + "$")))
    return matchers

def test_every_frontend_api_call_has_a_route() -> None:
    calls = _collect_frontend_calls()
    assert calls, "expected to find /api calls in the frontend sources"

    matchers = _registered_path_matchers()
    unmatched = {}
    for url, sites in calls.items():
        if url in _ALLOWED_UNRESOLVABLE:
            continue
        probe = url.replace("X", "placeholder")
        if not any(rx.match(probe) for _path, rx in matchers):
            unmatched[url] = sites

    assert not unmatched, "frontend calls endpoints with no route:\n" + "\n".join(
        f"  {url}\n    " + "\n    ".join(sites) for url, sites in sorted(unmatched.items())
    )

def test_mark_uploaded_route_is_wired() -> None:
    paths = app_mod.app.openapi()["paths"]
    assert "/api/pending/mark-uploaded" in paths
    assert "post" in paths["/api/pending/mark-uploaded"]


def test_anime_cache_correction_route_is_wired() -> None:
    paths = app_mod.app.openapi()["paths"]
    assert "/api/pending/anime-cache" in paths
    assert "post" in paths["/api/pending/anime-cache"]

def test_grouped_items_route_is_wired() -> None:
    paths = app_mod.app.openapi()["paths"]
    assert "/api/uploads/grouped/items" in paths
    assert "get" in paths["/api/uploads/grouped/items"]


def test_grouped_errors_route_is_wired() -> None:
    paths = app_mod.app.openapi()["paths"]
    assert "/api/uploads/errors/grouped" in paths
    assert "get" in paths["/api/uploads/errors/grouped"]

def test_mark_uploaded_rejects_empty_input(monkeypatch) -> None:
    calls: list[tuple] = []

    def _fake(item_keys, indexer_ids, itype="Misc"):
        calls.append((item_keys, indexer_ids, itype))
        return len(item_keys) * len(indexer_ids)

    monkeypatch.setattr(db, "mark_as_uploaded", _fake)

    for bad in (
        app_mod.MarkUploadedRequest(item_keys=[], indexer_ids=["geek"]),
        app_mod.MarkUploadedRequest(item_keys=["a"], indexer_ids=[]),
    ):
        with pytest.raises(HTTPException) as exc:
            _run_async(app_mod.mark_items_uploaded(bad))
        assert exc.value.status_code == 400

    # Nothing should have been written for the rejected requests.
    assert calls == []

    ok = app_mod.MarkUploadedRequest(item_keys=["a", "b"], indexer_ids=["geek"])
    result = _run_async(app_mod.mark_items_uploaded(ok))
    assert result["records_created"] == 2
    assert result["items"] == 2
    assert result["indexers"] == 1
    assert calls == [(["a", "b"], ["geek"], "Misc")]


def test_anime_cache_correction_persists_and_refreshes(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []
    refresh_reasons: list[str] = []

    monkeypatch.setattr(
        "logic.anime_cache.set_cached",
        lambda name, is_anime: calls.append((name, is_anime)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        app_mod._pending_index,
        "request_refresh",
        lambda *, reason: refresh_reasons.append(reason),
    )

    result = app_mod.correct_pending_anime_cache(
        app_mod.AnimeCacheCorrectionRequest(name="Ghost in the Shell", is_anime=False)
    )

    assert result == {
        "status": "success",
        "name": "Ghost in the Shell",
        "is_anime": False,
        "category": "misc",
    }
    assert calls == [("Ghost in the Shell", False)]
    assert refresh_reasons == ["anime-cache-correction"]


def test_anime_cache_correction_rejects_blank_title() -> None:
    with pytest.raises(HTTPException) as exc:
        app_mod.correct_pending_anime_cache(app_mod.AnimeCacheCorrectionRequest(name="  ", is_anime=False))
    assert exc.value.status_code == 400

def test_grouped_items_passes_destination_through(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fake(title_key_value, destination="all"):
        seen["title_key"] = title_key_value
        seen["destination"] = destination
        return {"title_key": title_key_value, "items": []}

    monkeypatch.setattr(db, "get_group_upload_items", _fake)

    app_mod.get_grouped_items("some show", destination="geek")

    assert seen == {"title_key": "some show", "destination": "geek"}

def test_grouped_errors_passes_params_through(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fake(indexer_id="all", limit=50, since_days=None, include_muted=True):
        seen["indexer_id"] = indexer_id
        seen["limit"] = limit
        seen["since_days"] = since_days
        seen["include_muted"] = include_muted
        return {"issues": [], "total_issues": 0}

    monkeypatch.setattr(db, "get_grouped_upload_errors", _fake)

    result = app_mod.get_grouped_errors(destination="geek", limit=5, since_days=3, include_muted=False)

    assert seen == {"indexer_id": "geek", "limit": 5, "since_days": 3, "include_muted": False}
    assert result == {"issues": [], "total_issues": 0}


def test_mute_and_unmute_routes_are_wired() -> None:
    paths = app_mod.app.openapi()["paths"]
    assert "/api/uploads/errors/mute" in paths
    assert "post" in paths["/api/uploads/errors/mute"]
    assert "/api/uploads/errors/unmute" in paths
    assert "post" in paths["/api/uploads/errors/unmute"]


def test_mute_and_unmute_grouped_error_pass_params_through(monkeypatch) -> None:
    seen: list[tuple] = []

    monkeypatch.setattr(db, "mute_upload_issue", lambda indexer_id, signature: seen.append(("mute", indexer_id, signature)) or True)
    monkeypatch.setattr(db, "unmute_upload_issue", lambda indexer_id, signature: seen.append(("unmute", indexer_id, signature)) or True)

    mute_result = app_mod.mute_grouped_error(app_mod.MuteIssueRequest(indexer_id="geek", signature="auth failed"))
    unmute_result = app_mod.unmute_grouped_error(app_mod.MuteIssueRequest(indexer_id="geek", signature="auth failed"))

    assert seen == [("mute", "geek", "auth failed"), ("unmute", "geek", "auth failed")]
    assert mute_result == {"status": "success", "muted": True}
    assert unmute_result == {"status": "success", "muted": False}

def test_no_inline_script_inside_the_vue_root() -> None:
    """Vue silently discards <script>/<style> in a client component template.

    queue.html's scroll-to-staging FAB sat in `{% block content %}` (rendered
    inside #app) and therefore never bound its click handler.
    """
    offenders = []
    for template in sorted(_TEMPLATE_ROOT.glob("*.html")):
        text = template.read_text(encoding="utf-8", errors="replace")
        block = re.search(r"\{%\s*block content\s*%\}(.*?)\{%\s*endblock\s*%\}", text, re.S)
        if not block:
            continue
        body = block.group(1)
        for tag in re.finditer(r"<(script|style)(?![^>]*\bsrc=)[^>]*>", body):
            line = text[: block.start(1)].count("\n") + body[: tag.start()].count("\n") + 1
            offenders.append(f"{template.name}:{line} <{tag.group(1)}>")

    assert not offenders, (
        "inline <script>/<style> inside the Vue-mounted content block is dropped "
        "by the template compiler; move it to {% block scripts %}:\n  "
        + "\n  ".join(offenders)
    )
