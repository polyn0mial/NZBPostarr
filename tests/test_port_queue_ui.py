# ruff: noqa: F403,F405

"""Queue page fixes ported from the live server (queue-ui area)."""

import re

from tests.support import *

PAGES = REPO_ROOT / "webui" / "assets" / "js" / "pages"


def _queue_html() -> str:
    return _read_repo_text("webui", "queue.html")


def _block(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_split_queue_modules_import_the_page_helpers_they_use() -> None:
    # esbuild minifies each module's top-level names, so a helper that one queue module
    # defines but another only references by name is a ReferenceError at runtime.
    # Every module must import what it uses.
    modules = sorted((PAGES / "queue").glob("*.js"))
    assert (PAGES / "queue" / "index.js") in modules
    assert not list(PAGES.glob("queue*.js")), "the old queue.js / queue-*.js split files are gone"
    helpers = []
    for module in modules:
        for exported in re.findall(r"^export \{([^}]*)\};", module.read_text(encoding="utf-8"), re.M):
            helpers += [name.strip() for name in exported.split(",") if name.strip()]
    assert "deepFreezePendingTree" in helpers
    shared = ["categoryMeta", "categoryLabel", "categoryToItype", "sharedItypeToCategory", "Sortable"]

    for module in modules:
        text = module.read_text(encoding="utf-8")
        own = set(re.findall(r"^(?:async )?(?:function|const|var|let) ([A-Za-z_$][\w$]*)", text, re.M))
        imports = "\n".join(line for line in text.splitlines() if line.startswith("import "))
        body = "\n".join(line for line in text.splitlines() if not line.startswith("import "))
        for name in helpers + shared:
            if name in own:
                continue
            if re.search(rf"(?<![\w.$]){name}\b", body):
                assert re.search(rf"\b{name}\b", imports), f"{module.name} uses {name} without importing it"
    assert "normalizeExtChild" not in "".join(m.read_text(encoding="utf-8") for m in modules)


def test_lazy_children_are_grafted_into_the_tree_and_rehydrated_after_refresh() -> None:
    queue_js = _queue_source()
    apply_block = _block(queue_js, "async function applyLoadedPendingItems(", "function normalizePendingExternalGroups(")

    assert apply_block.index("self._graftLoadedChildren(rawItems);") < apply_block.index("self._lastRawItems = rawItems;")
    assert "await self._hydrateExpandedExtChildren();" in apply_block
    assert "extLoadedChildren" not in queue_js
    assert "for (const it2 of group.items) visit(it2);" in queue_js

    loader = _block(queue_js, 'async ensureExtChildrenLoaded(itemOrKey, fallbackPath = "") {', "isExtChildrenLoading(")
    assert "/api/pending/children?" in loader
    assert "{ timeoutMs: 9e4 }" in loader
    assert "this._replacePendingTree(rawBase);" in loader

    graft = _block(queue_js, "_graftLoadedChildren(newBase) {", "_clonePendingItems(items) {")
    # The previous tree is frozen; grafting its arrays directly would make the
    # normalizer throw on the next refresh.
    assert "this._clonePendingItems({ kids: oldKids })" in graft

    group_select = _block(queue_js, "async toggleExtGroupSelection(groupOrIdx, checked) {", "areAllExtGroupSelected(")
    assert "await this._ensureExtSubtreeLoaded(item);" in group_select


def test_manual_selection_is_not_gated_by_auto_select_rules() -> None:
    queue_js = _queue_source()
    for computed_cache in ("_cachedActionEntries", "_groupSelectableItems", "_extGroupAllSelectedMap"):
        assert computed_cache not in queue_js

    toggle_item = _block(queue_js, "toggleItemSelection(item, checked) {", "toggleFlatCategorySelection(")
    assert "isAutoSelectable" not in toggle_item
    selected_entries = _block(queue_js, "collectSelectedExternalActionEntries(item) {", "collectVisibleExternalActionEntries(")
    assert "const itemSelected = this.selectedItems.has(item.key);" in selected_entries


def test_load_pending_keeps_silent_polling_and_handles_expired_sessions() -> None:
    queue_js = _queue_source()
    run_block = _block(queue_js, "async function runPendingLoad(self, forceRefresh, silent) {", "async function applyLoadedPendingItems(")

    assert "if (!silent) {" in run_block
    assert 'params.append("known_cached_at", String(self.cachedAt));' in run_block
    assert "{ timeoutMs: 9e4 }" in run_block
    assert '"/login?next=" + encodeURIComponent(window.location.pathname)' in run_block
    assert "!(e2.status >= 500) && !self._loadPendingErrShown" in run_block
    assert "self.loadPending(false, true)" in queue_js


def test_category_overrides_are_loaded_and_saved_on_the_server() -> None:
    queue_js = _queue_source()

    assert 'this.apiFetch("/api/pending/category-overrides")' in queue_js
    assert 'this.apiPost("/api/pending/category-overrides", { key, category: value || null })' in queue_js
    assert "Promise.all([this.loadCategoryOverrides(), this.loadProcessingSettings()])" in queue_js


def test_ignore_rules_and_pack_only_children() -> None:
    queue_js = _queue_source()
    ignored = _block(queue_js, "isSelectionIgnored(item) {", "isAutoSelectable(item) {")

    assert "if (this.isPackOnlyExternalChild(item)) return false;" in ignored
    assert '["disc", "music", "books", "ebooks", "audiobooks"].includes(resolvedCategory)' in ignored
    bubble = _block(queue_js, "shouldShowYieldBubble(item) {", "getDetectionLabel(item) {")
    assert "if (hasSourceToken(folderName)) return false;" in bubble
    category = _block(queue_js, "getCategoryForItem(item) {", "_resolveExtCategory(key) {")
    assert category.index("inheritedExternalCategory") < category.index("directManualCategory")
    assert category.index("this._resolveExtCategory(item.key)") < category.index("this.itypeToCategory(item.itype)")
    assert "inferCategoryFromText" not in queue_js


def test_finished_jobs_list_is_cached_for_a_day() -> None:
    queue_js = _queue_source()

    assert 'localStorage.setItem("nzb_finished_jobs"' in _block(queue_js, "async loadJobs() {", "async revalidateQueuedJobs()")
    assert "Date.now() - cached.ts < 864e5" in queue_js
    assert 'localStorage.removeItem("nzb_finished_jobs")' in _block(queue_js, "async clearFinished() {", "async deleteJob(")
    assert "showFinishedDetailModal" not in queue_js


def test_queue_template_uses_server_layout() -> None:
    html = _queue_html()
    scripts = html.split("{% block scripts %}", 1)[1]

    # Storage sanitiser and error overlay run before the page bundle, without hard-coded revisions.
    bundle = scripts.index("/assets/js/dist/pages/queue/index.js")
    assert scripts.index("nzbpostarr_queue_bootstrap_reset_v20260805_r3") < bundle
    assert scripts.index('<script src="/assets/js/components/error-overlay.js') < bundle
    assert 'data-build="[[ cache_bust ]]"' in scripts
    assert "20260804" not in scripts
    overlay = _read_repo_text("webui", "assets", "js", "components", "error-overlay.js")
    assert "window.__queueShowOverlay = showOverlay;" in overlay
    assert "/queue-error-beacon?title=" in overlay
    assert "20260804" not in overlay

    # Action bar stays on screen; buttons disable at zero.
    assert html.count(':disabled="selectedCount === 0"') == 4
    # Job queue header acts on the running job, with Clear Pending, no job picker.
    assert 'v-model="jobQueueControlJobId"' not in html
    assert '@click="clearJobQueue()"' in html
    assert "isJobQueueActiveEntry(job) ? jobTitle(job) : jobDisplayName(job)" in html
    # Rows: transfer lock, lazy-loading placeholders, manual-only groups, capsule pills.
    assert html.count(">Transferring</span>") == 4
    assert html.count("Loading child items...") == 2
    assert ':disabled="isGroupManualSelectionOnly(extGroup)"' in html
    assert html.count("width:88px;min-width:88px;") == 3
    assert "pending-virtual-row" in html
    assert "flex-shrink-0" not in html
