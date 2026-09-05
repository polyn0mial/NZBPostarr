# ruff: noqa: F403,F405

"""NZBPostarr frontend tests."""

from tests.support import *


@pytest.mark.skipif(
    not _playwright_browsers_available(),
    reason="Playwright browsers are not installed",
)
def test_e2e_web_page_loads(base_url: str, page: Page) -> None:
    assert expect is not None
    if not base_url:
        pytest.skip("base_url fixture is not configured")

    page.set_default_timeout(120000)
    page.set_default_navigation_timeout(120000)

    cases = [
        ("/", "/api/dashboard/summary", "Dashboard", "Overview", "heading", "Console Output"),
        ("/queue", "/api/pending/summary", "Queue", "Pending Items", "id", "#upload-queue-section"),
        ("/settings", "/api/settings", "Settings", "Updates", "placeholder", "Search settings..."),
    ]

    for case_name, route, response_path, title, primary_heading, locator_kind, locator_value in (
        ("dashboard", *cases[0]),
        ("queue", *cases[1]),
        ("settings", *cases[2]),
    ):
        with page.expect_response(
            lambda response, response_path=response_path: _path_matches(response.url, response_path)
            and response.status == 200,
            timeout=120000,
        ):
            page.goto(f"{base_url}{route}", wait_until="commit")

        _expect_title(page, title)
        expect(page.get_by_role("heading", name=primary_heading)).to_be_visible()
        expect(_page_smoke_locator(page, locator_kind, locator_value)).to_be_visible()

def test_webui_queue_assets_include_expected_selection_logic() -> None:
    cases = [
        (
            "queue-js",
            ("webui", "assets", "js", "pages", "queue.js"),
            [
                "const cat = this.getCategoryForItem(child);",
                "nextCategories[item.key] = manualCategory || this.serverCategoryForItem(item);",
                "if (this.externalCategories[cursor]) return this.externalCategories[cursor];",
                "for (const child of item.children || []) {",
                "const skipDupeCheck = this.forceSkipDupeCheck;",
                "this._doForceUploadSingle(target, indexerId, skipDupeCheck);",
                "this._doForceUploadBulk(indexerId, skipDupeCheck);",
                'target.label || "selected items",',
                "selectedActionCount() {",
                "return this.selectedCount;",
                "selectedBreakdown() {",
                "_selectionKeyIsDirectory(key, fallbackPath",
                "syncSelectionToVisible()",
                "if (!node || !node.key || !this.extItemPassesFilters(node)) return;",
                "this.getVisibleSelectableFlatItems(cat.id).forEach((item) => {",
                "const selection = this.buildSelectedActionPayloads();",
                'this.buildActionPayloadsFromEntries(this.getActionEntriesForItem(item, "external"))',
                "No visible selected items are available to stage",
                "const data = await self.apiFetch(`/api/pending/items?${params}`, { timeoutMs: 15e3 });",
                'await this.apiPost("/api/pending/anime-cache", {',
                "async correctAnimeCache(item, isAnime) {",
                "createSortableInstance(instanceKey, container, options) {",
                'this.createSortableInstance("_pendingExternalGroupsSortable", container, {',
                'this.createSortableInstance("_activeJobModalSortable", container, {',
                'this.createSortableInstance("_queuedJobModalSortable", container, {',
                'this.createSortableInstance("_sortable", container, {',
            ],
        ),
        (
            "queue-html",
            ("webui", "queue.html"),
            [
                '@click.stop="forceUploadExtChild(child, $event)"',
                '@click.stop="forceUploadExtChild(gc, $event)"',
                ':disabled="!getUploadCategoryForItem(child)"',
                ':disabled="!getUploadCategoryForItem(gc)"',
                '@click.stop="correctAnimeCache(item, getCategoryForItem(item) !== \'anime\')"',
                '@click.stop="correctAnimeCache(child, getCategoryForItem(child) !== \'anime\')"',
                '@click.stop="correctAnimeCache(gc, getCategoryForItem(gc) !== \'anime\')"',
            ],
        ),
    ]

    for case_name, path_parts, required_substrings in cases:
        text = _queue_source() if case_name == "queue-js" else _read_repo_text(*path_parts)
        for needle in required_substrings:
            assert needle in text, f"{case_name}: {needle}"

    queue_js = _queue_source()
    assert "this._pendingExternalGroupsSortable = null;" not in queue_js
    for obsolete_browser_inference in (
        "inferExternalCategory",
        "inferCategoryFromText",
        "TV_NAME_PATTERN",
        "MOVIE_NAME_PATTERN",
    ):
        assert obsolete_browser_inference not in queue_js

def test_webui_performance_guards_are_present() -> None:
    page_base_js = _read_repo_text("webui", "assets", "js", "page-base.js")
    dashboard_js = _read_repo_text("webui", "assets", "js", "pages", "dashboard.js")
    queue_js = _queue_source()
    queue_html = _read_repo_text("webui", "queue.html")
    core_css = _read_repo_text("webui", "assets", "css", "core.css")

    assert "const CONSOLE_MAX_LINES = 300;" in dashboard_js
    assert "this.consoleLogs.splice(0, this.consoleLogs.length - CONSOLE_MAX_LINES);" in dashboard_js
    assert "this.consoleLogs.shift();" not in dashboard_js
    assert "behavior: 'auto'" in dashboard_js
    assert "scroll-smooth" not in dashboard_js

    assert "startTimeout(fn, ms)" in page_base_js
    assert "clearTimeout(this._healthTimer);" in page_base_js
    assert "document.removeEventListener('click', this._touchInfotipHandler);" in page_base_js
    assert "this.debouncedLoadPending.cancel();" in queue_js
    assert "clearInterval(this._animeWatcher);" in queue_js
    assert 'params.append("known_cached_at", String(self.cachedAt));' in queue_js
    assert "this.startInterval(() => this.loadQueuedPaths(), 1e4);" not in queue_js
    assert "/api/uploads/queue/${jobId}/active-items" in queue_js
    assert "/api/uploads/queue/${job.job_id}/completed-items" in queue_js
    assert "completedJobModalFilteredItems()" in queue_js
    assert "this.activeJobModalItems = this.buildJobPathItems(updatedActive);" not in queue_js
    assert "pending-virtual-row" in queue_html
    assert "Completed Job Items Modal" in queue_html
    assert "content-visibility: auto;" in core_css

def test_legacy_queue_and_history_pages_redirect_to_canonical_routes() -> None:
    cases = [
        ("pending", app_mod.redirect_pending_to_queue, "/queue"),
        ("pending.html", app_mod.redirect_pending_to_queue, "/queue"),
        ("uploads", app_mod.redirect_uploads_to_history, "/history"),
        ("uploads.html", app_mod.redirect_uploads_to_history, "/history"),
    ]

    for page_name, handler, destination in cases:
        response = _run_async(handler(_make_request(f"/{page_name}")))
        assert response.status_code == 301, page_name
        assert response.headers["location"] == destination, page_name

def test_legacy_multi_episode_folder_stays_tv_across_detectors(tmp_path) -> None:
    show_dir = tmp_path / "Aliens In The Family [1996] - Hensen"
    _touch(show_dir / "Season 01 - TV" / "Aliens in the Family - 101 - Meet the Brodys (Divx).avi", b"a")
    _touch(show_dir / "Season 01 - TV" / "Aliens in the Family - 102 - Bobut Conquers All (Divx).avi", b"b")

    assert app_mod._detect_content_itype(show_dir.name, show_dir, "") == "TV Show"
    assert app_mod._detect_external_category(show_dir.name, show_dir) == "tv"
    assert pending_scan.detect_auto_itype(show_dir) == "TV Show"
    assert pending_scan.detect_auto_category(show_dir) == "tv"
