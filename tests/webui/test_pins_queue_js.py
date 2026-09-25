"""Source-text pins on the queue page scripts (webui/assets/js/pages/queue*.js)."""

from tests.webui._source import _queue_source


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
                "const data = await self.apiFetch(`/api/pending/items?${params}`, { timeoutMs: 9e4 });",
                'await this.apiPost("/api/pending/anime-cache", {',
                "async correctAnimeCache(item, isAnime) {",
                "createSortableInstance(instanceKey, container, options) {",
                'this.createSortableInstance("_pendingExternalGroupsSortable", container, {',
                'this.createSortableInstance("_activeJobModalSortable", container, {',
                'this.createSortableInstance("_queuedJobModalSortable", container, {',
                'this.createSortableInstance("_sortable", container, {',
            ],
        ),
    ]

    for case_name, _path_parts, required_substrings in cases:
        text = _queue_source()
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


def test_webui_queue_performance_guards_are_present() -> None:
    queue_js = _queue_source()

    assert "this.debouncedLoadPending.cancel();" in queue_js
    assert "clearInterval(this._animeWatcher);" in queue_js
    assert 'params.append("known_cached_at", String(self.cachedAt));' in queue_js
    assert "this.startInterval(() => this.loadQueuedPaths(), 1e4);" not in queue_js
    assert "/api/uploads/queue/${jobId}/active-items" in queue_js
    assert "/api/uploads/queue/${job.job_id}/completed-items" in queue_js
    assert "completedJobModalFilteredItems()" in queue_js
    assert "this.activeJobModalItems = this.buildJobPathItems(updatedActive);" not in queue_js


def test_queue_page_keeps_data_loading_separate_from_queue_validation() -> None:
    queue_js = _queue_source()

    load_pending_block = queue_js.split("async loadPending(forceRefresh = false, silent = false) {", 1)[1].split(
        "async loadQueuedPaths() {", 1
    )[0]
    load_queued_block = queue_js.split("async loadQueuedPaths() {", 1)[1].split("isItemQueued(item) {", 1)[0]

    assert "this.syncSelectionToVisible();" not in load_pending_block
    assert "this.syncSelectionToVisible();" not in load_queued_block


def test_bulk_force_upload_bypasses_review_and_staging() -> None:
    queue_js = _queue_source()
    force_block = queue_js.split("async _doForceUploadBulk(indexerId, skipDupeCheck = false) {", 1)[1].split(
        "closeBulkPreviewModal() {",
        1,
    )[0]

    assert 'this.apiPost("/api/pending/force-upload", request)' in force_block
    assert "/api/pending/preview-upload" not in force_block
    assert "this.showBulkPreviewModal = true" not in force_block


def test_nested_external_lookup_includes_lazy_loaded_and_ignored_groups() -> None:
    queue_js = _queue_source()
    # Lazily loaded children are grafted into the item tree itself, so the lookup walks
    # children/files of every section and never skips a group.
    lookup_block = queue_js.split('_findPendingNodeByKey(items, targetKey, targetPath = "") {', 1)[1].split(
        "_findCurrentPendingNode(",
        1,
    )[0]

    assert "if (Array.isArray(node.children)) childLists.push(node.children);" in lookup_block
    assert "childLists.push(node.files)" in lookup_block
    assert "for (const section of Object.values(items)) {" in lookup_block
    assert "allow_bulk_selection === false" not in lookup_block
    assert "extLoadedChildren" not in queue_js


def test_pending_rows_size_the_category_select_per_category() -> None:
    queue_js = _queue_source()

    assert "categorySelectWidthClass(cat) {" in queue_js
