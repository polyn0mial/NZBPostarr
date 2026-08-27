import {
    createVuePage,
    categoryMeta,
    categoryLabel,
    itypeToCategory as sharedItypeToCategory,
    categoryToItype,
} from 'page-base';
import Sortable from 'sortablejs';
import debounce from 'lodash.debounce';

var CACHE_KEY = "nzbpostarr_pending_cache";
var EXTERNAL_GROUP_ORDER_KEY = "nzbpostarr_pending_external_group_order";
var EXTERNAL_GROUP_LOCK_KEY = "nzbpostarr_pending_external_group_order_locked";
var FILTER_MODE_OPTIONS = [
  { value: "hideQueued", label: "Hide Staged" },
  { value: "onlyQueued", label: "Only Staged" },
  { value: "hideCompleted", label: "Hide Completed" },
  { value: "showCompleted", label: "Show Completed" },
  { value: "hideIgnored", label: "Hide Ignored" }
];
var SESSION_CACHE_MAX_BYTES = 2e6;
function normalizeExtChild(child, inheritedCategory, normalizePathKey) {
  if (!child || typeof child !== "object") return;
  if (!child.key && child.path) {
    child.key = `path:${normalizePathKey(child.path)}`;
  }
  child.files = [];
  const childCategory = child.detected_category || child.category || inheritedCategory || "";
  child.assigned_category_safe = childCategory;
  if (!child.detected_category && childCategory) child.detected_category = childCategory;
}

function deepFreezePendingTree(items) {
  if (!items || typeof items !== "object") return items;
  const visit = (node) => {
    if (!node || typeof node !== "object" || Object.isFrozen(node)) return;
    if (node.indexers && typeof node.indexers === "object") Object.freeze(node.indexers);
    if (node.indexer_errors && typeof node.indexer_errors === "object") Object.freeze(node.indexer_errors);
    const children = node.children;
    if (Array.isArray(children)) {
      for (const child of children) visit(child);
      Object.freeze(children);
    }
    Object.freeze(node);
  };
  if (Array.isArray(items.external)) {
    for (const group of items.external) {
      if (Array.isArray(group.items)) {
        // Don't freeze individual external items - their .children must stay
        // mutable so ensureExtChildrenLoaded can write lazy-loaded children back.
        Object.freeze(group.items);
      }
      Object.freeze(group);
    }
    Object.freeze(items.external);
  }
  for (const [key, val] of Object.entries(items)) {
    if (key === "external" || !Array.isArray(val)) continue;
    for (const it2 of val) visit(it2);
    Object.freeze(val);
  }
  return Object.freeze(items);
}
// Pick which job the queue-control panel should track after a refresh:
// keep the current selection if it is still present, otherwise prefer the
// running job, then the paused one, then whatever is first in either list.
function resolvePreferredJobId(currentId, running, queued) {
  const availableJobIds = new Set([...running, ...queued].map((job) => job.job_id));
  if (currentId && !availableJobIds.has(currentId)) {
    currentId = null;
  }
  if (!currentId) {
    const preferredJob = running.find((job) => job.status === "running")
      || running.find((job) => job.status === "paused")
      || running[0]
      || queued[0];
    currentId = preferredJob?.job_id || null;
  }
  return currentId;
}
// Kick off one revalidation of queued jobs after a refresh lands while the
// queue is paused and non-empty; only ever once per pause, until it unpauses.
function maybeRevalidateQueuedJobs(self) {
  if (!self.queueControl?.paused) {
    self._pendingQueuedJobRevalidationDone = false;
    return;
  }
  if (!self._pendingQueuedJobRevalidationDone && ((self.counts?.queued || 0) > 0 || (self.counts?.running || 0) > 0)) {
    self._pendingQueuedJobRevalidationDone = true;
    void self.revalidateQueuedJobs();
  }
}
// Keep the open "active job" modal in sync with a fresh queue snapshot, or
// close it if the job it was showing is gone.
function syncActiveJobModal(self) {
  if (!self.showActiveJobModal || !self.activeJobModalJob) return;
  const updatedActive = self.running.find((j2) => j2.job_id === self.activeJobModalJob.job_id);
  if (updatedActive) {
    self.activeJobModalJob = updatedActive;
    if (!self.activeJobModalSaving) {
      void self.loadActiveJobModalItems(updatedActive.job_id, true);
    }
  } else {
    self.closeActiveJobModal();
  }
}
// Keep the open "queued job" modal in sync with a fresh queue snapshot, or
// close it if the job it was showing is gone.
function syncQueuedJobModal(self) {
  if (!self.showQueuedJobModal || !self.queuedJobModalJob) return;
  const updated = self.queued.find((j2) => j2.job_id === self.queuedJobModalJob.job_id);
  if (updated) {
    self.queuedJobModalJob = updated;
    if (!self.queuedJobModalRenaming) {
      self.queuedJobModalName = updated.display_name || "";
    }
    if (!self.queuedJobModalScheduling) {
      self.hydrateQueuedJobSchedule(updated.run_after || null);
    }
  } else {
    self.closeQueuedJobModal();
  }
}
var vm = createVuePage({
persist: ["literalSearch", "selectedCategories", "collapsedCategories", "filterMode", "ignoredPaths", "unignoredPaths", "queueSectionExpanded", "manualExternalCategories", "bulkSelectCategoriesSelected"],
revisionSensitivePersistKeys: ["ignoredPaths", "unignoredPaths"],
  data() {
    return {
      loading: true,
      startingQueue: false,
      // ── Pending items grouped by category ──
      items: {
        movies: [],
        misc: [],
        external: []
      },
      // External items: user-chosen category before upload
      externalCategories: {},
      manualExternalCategories: {},
      pendingExternalGroupOrder: [],
      pendingExternalGroupOrderLoaded: false,
      // Active indexers from API
      activeIndexers: [],
      // Available categories from backend (indexer plugins)
      categories: [],
      // Summary stats
      summary: {
        movies: 0,
        misc: 0,
        external: 0,
        total: 0
      },
      // Cache tracking
      cachedAt: null,
      // Processing filter state
      processingFilters: {},
      // Search & filter
      searchQuery: "",
      literalSearch: false,
      selectedCategories: ["all"],
      categorySelectionReady: false,
      categoryFilterOpen: false,
      bulkSelectOpen: false,
      bulkSelectCategoriesSelected: [],
      // Legacy single-select state kept for migration from older persisted sessions.
      filterCategory: "all",
      _searchTimer: null,
      _lastLoadSig: null,
      expandedExtItems: {},
      extLoadedChildren: {},
      allExpanded: false,
      // Category collapse
      collapsedCategories: {
        movies: false,
        misc: false
      },
      // Main section collapse state
      queueSectionExpanded: {
        pending: true,
        staging: true,
        processing: true,
        active: true,
        waiting: true,
        finished: true
      },
      // Selection
      selectedItems: /* @__PURE__ */ new Set(),
      selectedMeta: /* @__PURE__ */ new Map(),
      // Skip files config from backend
      skipFiles: { enabled: false, display_mode: "disabled" },
      // Summary pre-loaded
      summaryReady: false,
      // Loading elapsed timer (seconds)
      loadingElapsed: 0,
      _loadingTimer: null,
      _queuedPathsTimer: null,
      // Mark uploaded modal
      showMarkModal: false,
      markAllIndexers: true,
      markIndexerSelection: {},
      // Bulk-upload planning modal
      showBulkPreviewModal: false,
      bulkPreview: null,
      bulkPreviewRequest: null,
      bulkPreviewMode: "bulk",
      bulkPreviewIndexerName: "",
      bulkPreviewLoading: false,
      bulkPreviewStarting: false,
      // Force upload flyout
      flyoutOpen: false,
      flyoutTarget: null,
      flyoutPos: { x: 0, y: 0 },
      forceSkipDupeCheck: false,
      // Queue awareness
      queuedPaths: /* @__PURE__ */ new Set(),
      filterMode: [],
      filterModeOpen: false,
      filterModePos: { x: 0, y: 0 },
      categoryFilterPos: { x: 0, y: 0 },
      bulkSelectPos: { x: 0, y: 0 },
      ignoredPaths: [],
      unignoredPaths: [],
      queueStats: { total: 0, movies: 0, tv: 0 },
      // ── Queue items (drag-sortable staging) ──
      queueItems: [],
      // ── Job data ──
      running: [],
      queued: [],
      finished: [],
      counts: { running: 0, queued: 0, finished: 0 },
      queueControl: { paused: false, active: null },
      pendingJobActions: /* @__PURE__ */ new Set(),
      jobQueueControlJobId: null,
      // ── Finished modal ──
      showFinishedModal: false,
      // ── Active job viewer modal ──
      showActiveJobModal: false,
      activeJobModalJob: null,
      activeJobModalItems: [],
      activeJobModalLoading: false,
      activeJobModalSearch: "",
      activeJobModalSaving: false,
      // ── Completed job items modal ──
      completedJobModalJob: null,
      completedJobModalItems: [],
      completedJobModalLoading: false,
      completedJobModalSearch: "",
      _loadActiveJobItemsPromise: null,
      _activeJobItemsRequestJobId: null,
      // ── Queued job editor modal ──
      showQueuedJobModal: false,
      queuedJobModalJob: null,
      queuedJobModalItems: [],
      queuedJobModalLoading: false,
      queuedJobModalSearch: "",
      _queuedJobItemsCache: {},
      _queuedJobLoadTimer: null,
      queuedJobModalName: "",
      queuedJobModalDate: "",
      queuedJobModalHasTime: false,
      queuedJobModalTime: "12:00",
      queuedJobModalSaving: false,
      queuedJobModalRenaming: false,
      queuedJobModalScheduling: false,
      // ── Queue group collapse tracking (expanded = true, absent = collapsed) ──
      expandedQueueGroups: {},
      // ── Anime detection indicator ──
      animeDetecting: false,
      _animeWatcher: null,
      // ── Raw (pre-itype-filter) items from last server response ──
      _lastRawItems: null,
      // ── Memoization-version counters ──
      // Bumped whenever inputs that affect a memoized lookup change.
      // Computeds depend on these counters so they only rebuild when needed,
      // not on every reactive tick. Keeping them as primitive counters keeps
      // dependency tracking cheap (no nested-object reads).
      _itemsVersion: 0,
      // bumped when items tree is replaced
      _queuedPathsVersion: 0,
      // bumped when queuedPaths is replaced
      _filterVersion: 0,
      // bumped when filter mode / search / categories / ignored / external category overrides change
      _indexersVersion: 0,
      // bumped when activeIndexers changes
      _externalGroupOrderVersion: 0,
      pendingExternalGroupOrderLocked: (() => {
        try {
          return localStorage.getItem(EXTERNAL_GROUP_LOCK_KEY) === "true";
        } catch (_e2) {
          return false;
        }
      })(),
      _pendingQueuedJobRevalidationDone: false
    };
  },
  created() {
    if (!Array.isArray(this.selectedCategories)) {
      const legacy = typeof this.filterCategory === "string" && this.filterCategory ? this.filterCategory : "all";
      this.selectedCategories = [legacy];
    }
    this.selectedCategories = Array.from(new Set(this.selectedCategories.filter(Boolean)));
    if (this.selectedCategories.length === 0 || this.selectedCategories.includes("all")) {
      this.selectedCategories = ["all"];
    }
    if (!Array.isArray(this.filterMode)) {
      this.filterMode = this.filterMode ? [this.filterMode] : [];
    }
    if (!Array.isArray(this.ignoredPaths)) {
      this.ignoredPaths = [];
    }
    if (!Array.isArray(this.unignoredPaths)) {
      this.unignoredPaths = [];
    }
    this.ignoredPaths = Array.from(new Set(this.ignoredPaths.map((p2) => this.normalizePathKey(p2)).filter(Boolean)));
    this.unignoredPaths = Array.from(new Set(this.unignoredPaths.map((p2) => this.normalizePathKey(p2)).filter(Boolean)));
    this.externalCategories = {};
    const defaults2 = {
      pending: true,
      staging: true,
      processing: true,
      active: true,
      waiting: true,
      finished: true
    };
    if (!this.queueSectionExpanded || typeof this.queueSectionExpanded !== "object") {
      this.queueSectionExpanded = { ...defaults2 };
    } else {
      this.queueSectionExpanded = { ...defaults2, ...this.queueSectionExpanded };
    }
    try {
      const stickyPending = localStorage.getItem("nzb_pending_expanded");
      if (stickyPending === "true" || stickyPending === "false") {
        this.queueSectionExpanded.pending = stickyPending === "true";
      }
    } catch (_e2) {
    }
    this.debouncedLoadPending = debounce(() => this.loadPending(), 350);
  },
  computed: {
    normalizedSelectedCategories() {
      const raw = Array.isArray(this.selectedCategories) ? this.selectedCategories : [];
      const unique = Array.from(new Set(raw)).filter(Boolean);
      if (unique.length === 0 || unique.includes("all")) return ["all"];
      if (!this.categorySelectionReady) return unique;
      const valid = new Set(this.availableCategories.map((c2) => c2.value));
      const filtered = unique.filter((v2) => valid.has(v2));
      if (filtered.length === 0) return valid.has("all") ? ["all"] : [];
      return filtered;
    },
    selectedCategorySet() {
      return new Set(this.normalizedSelectedCategories);
    },
    categoryFilterLabel() {
      const selected = this.normalizedSelectedCategories;
      if (selected.length === 0) return "No Categories";
      if (selected.includes("all")) return "All Categories";
      if (selected.length === 1) {
        const hit = this.availableCategories.find((c2) => c2.value === selected[0]);
        return hit ? hit.label : selected[0];
      }
      return `${selected.length} Categories`;
    },
    activeFilterModes() {
      const values = Array.isArray(this.filterMode) ? this.filterMode : [];
      return new Set(values);
    },
    filterModeLabel() {
      const modes = Array.isArray(this.filterMode) ? this.filterMode : [];
      if (modes.length === 0) return "Filters";
      const labels = { hideQueued: "Hide Staged", onlyQueued: "Only Staged", hideCompleted: "Hide Completed", showCompleted: "Show Completed", hideIgnored: "Hide Ignored" };
      if (modes.length === 1) return labels[modes[0]] || modes[0];
      return `${modes.length} Filters`;
    },
    filterModeOptions() {
      return FILTER_MODE_OPTIONS;
    },
    bulkSelectLabel() {
      const selected = this.normalizedBulkSelectCategories;
      if (!selected.length) return "Bulk Select";
      if (selected.includes("all")) return "All Uploadable";
      if (selected.length === 1) {
        const hit = this.bulkSelectCategories.find((cat) => cat.value === selected[0]);
        return hit ? hit.label : selected[0];
      }
      return `${selected.length} Categories`;
    },
    normalizedBulkSelectCategories() {
      const raw = Array.isArray(this.bulkSelectCategoriesSelected) ? this.bulkSelectCategoriesSelected : [];
      const unique = Array.from(new Set(raw)).filter(Boolean);
      if (unique.includes("all")) return ["all"];
      const valid = new Set(this.bulkSelectCategories.map((cat) => cat.value));
      return unique.filter((v) => valid.has(v));
    },
    bulkSelectSelectedSet() {
      return new Set(this.normalizedBulkSelectCategories);
    },
    bulkSelectCategories() {
      const selected = new Set(Array.isArray(this.bulkSelectCategoriesSelected) ? this.bulkSelectCategoriesSelected.filter(Boolean) : []);
      return this.availableCategories.filter((cat) => cat.value !== "all" && Number(cat.count || 0) > 0).map((cat) => ({
        ...cat,
        checked: selected.has(cat.value)
      }));
    },
    ignoredCount() {
      return Array.isArray(this.ignoredPaths) ? this.ignoredPaths.length : 0;
    },
    selectedCount() {
      return this.selectedItems.size;
    },
    selectedActionCount() {
      return this.selectedCount;
    },
    _cachedActionEntries() {
      return this.getSelectedActionEntries();
    },
    _groupSelectableItems() {
      // Precomputes selectable nodes per group. Does NOT read selectedItems,
      // so it only recomputes when items/filters change - not on every click.
      const map = /* @__PURE__ */ new Map();
      (this.items.external || []).forEach((group) => {
        const gKey = this.externalGroupKey(group);
        const selectable = [];
        (group.items || []).forEach((item) => {
          selectable.push(...this.collectVisibleSelectableExternalNodes(item));
        });
        map.set(gKey, selectable);
      });
      return map;
    },
    _extGroupAllSelectedMap() {
      // Uses _groupSelectableItems (cached), so only does Set lookups per click.
      if (this.selectedItems.size === 0) return /* @__PURE__ */ new Map();
      const map = /* @__PURE__ */ new Map();
      for (const [gKey, selectable] of this._groupSelectableItems) {
        const allSelected = selectable.length > 0 &&
          selectable.every((node) => this.selectedItems.has(node.key));
        map.set(gKey, allSelected);
      }
      return map;
    },
    selectedBreakdown() {
      let packs = 0;
      const episodeFiles = /* @__PURE__ */ new Set();
      const visible = this.getVisibleSelectableItemsMap();
      for (const key of this.selectedItems) {
        const meta = visible.get(key) || this.selectedMeta.get(key);
        const path = meta && meta.path || "";
        const isDir = this._selectionKeyIsDirectory(key, path);
        if (isDir) packs += 1;
        else if (path || key) episodeFiles.add(this.normalizePathKey(path || key));
      }
      for (const entry of this._cachedActionEntries) {
        const item = entry && entry.item;
        if (!item) continue;
        const path = item.path || item.key || item.name;
        if (!this._selectionKeyIsDirectory(item.key || path, path)) {
          episodeFiles.add(this.normalizePathKey(path));
        }
      }
      return { packs, files: episodeFiles.size, total: this.selectedItems.size };
    },
    selectedSummaryLabel() {
      const { packs, files, total } = this.selectedBreakdown;
      if (total === 0) return "";
      if (packs > 0 && files > 0) {
        return `${total} selected (${packs} pack${packs === 1 ? "" : "s"}, ${files} episode${files === 1 ? "" : "s"})`;
      }
      if (packs > 0) {
        return `${total} selected (${packs} pack${packs === 1 ? "" : "s"})`;
      }
      return `${total} selected (${files} episode${files === 1 ? "" : "s"})`;
    },
    uploadPrepCounters() {
      const singleFiles = /* @__PURE__ */ new Set();
      const seasonalPacks = /* @__PURE__ */ new Set();
      const addPreparedItem = (item) => {
        if (!item) return;
        const path = item.path || item.target_path || item.source_path || item.name || item.key;
        if (!path) return;
        const key = this.normalizePathKey(path);
        const isDir = typeof item.is_dir === "boolean" ? item.is_dir : this._selectionKeyIsDirectory(item.key || key, path);
        if (isDir) {
          if (this.isSeasonalPackItem(item)) seasonalPacks.add(key);
          return;
        }
        singleFiles.add(key);
      };
      const visitSelectedSeasonPack = (node) => {
        if (!node || !node.key) return;
        if (this.selectedItems.has(node.key) && this.isAutoSelectable(node) && this.isSeasonalPackItem(node)) {
          const path = node.path || node.name || node.key;
          seasonalPacks.add(this.normalizePathKey(path));
        }
        (node.children || []).forEach(visitSelectedSeasonPack);
      };
      (this.items.external || []).forEach((group) => {
        (group.items || []).forEach(visitSelectedSeasonPack);
      });
      for (const entry of this._cachedActionEntries) {
        addPreparedItem(entry.item);
      }
      for (const item of this.queueItems || []) {
        addPreparedItem({
          ...item,
          is_dir: item.is_dir,
          path: item.path || item.target_path,
          key: item.key || item.id,
          category: item.category,
          itype: item.itype
        });
      }
      return { singleFiles: singleFiles.size, seasonalPacks: seasonalPacks.size };
    },
    singleFilePrepCount() {
      return this.uploadPrepCounters.singleFiles;
    },
    seasonalPackPrepCount() {
      return this.uploadPrepCounters.seasonalPacks;
    },
    isMoviesEnabled() {
      return true;
    },
    isExternalEnabled() {
      return this.items.external && this.items.external.some((g2) => g2.items && g2.items.length > 0);
    },
    orderedExternalGroups() {
      const groups = (this.items.external || []).slice();
      void this._externalGroupOrderVersion;
      const storedOrder = this.pendingExternalGroupOrderLoaded ? this.pendingExternalGroupOrder : this.loadExternalGroupOrder();
      if (!storedOrder.length) return groups;
      const order = new Map(storedOrder.map((key, index2) => [key, index2]));
      return groups.sort((a2, b2) => {
        const ak = this.externalGroupKey(a2);
        const bk = this.externalGroupKey(b2);
        const ai2 = order.has(ak) ? order.get(ak) : Number.MAX_SAFE_INTEGER;
        const bi2 = order.has(bk) ? order.get(bk) : Number.MAX_SAFE_INTEGER;
        if (ai2 !== bi2) return ai2 - bi2;
        return (a2.folder_name || "").localeCompare(b2.folder_name || "", void 0, { numeric: true, sensitivity: "base" });
      });
    },
    totalExternalItems() {
      if (!this.items.external) return 0;
      return this.items.external.reduce((sum, g2) => sum + (g2.items ? g2.items.length : 0), 0);
    },
    availableCategories() {
      const cats = [{ value: "all", label: "All Categories" }];
      const presentCategories = this._collectPresentCategories(this.items);
      const categoryOptions = /* @__PURE__ */ new Map();
      const folderCatIds = /* @__PURE__ */ new Set();
      if (presentCategories.has("tv")) {
        categoryOptions.set(categoryMeta.tv.id, { value: categoryMeta.tv.id, label: categoryMeta.tv.label });
        folderCatIds.add(categoryMeta.tv.id);
      }
      this.flatCategories.forEach((c2) => {
        if (c2.id === "movies" && !this.isMoviesEnabled) return;
        categoryOptions.set(c2.id, { value: c2.id, label: c2.label });
        folderCatIds.add(c2.id);
      });
      const fallbackCategoryLabels = {
        movies: categoryMeta.movies?.label || "Movies",
        tv: categoryMeta.tv?.label || "TV",
        anime: categoryMeta.anime?.label || "Anime",
        disc: categoryMeta.disc?.label || "DISC",
        music: categoryMeta.music?.label || "Music",
        books: categoryMeta.books?.label || "Books",
        ebooks: "Ebooks",
        audiobooks: "Audiobooks",
        apps: categoryMeta.apps?.label || "Apps",
        misc: categoryMeta.misc?.label || "Misc"
      };
      for (const category of ["movies", "tv", "anime", "disc", "books", "ebooks", "music", "apps", "misc"]) {
        if (category === "movies" && !this.isMoviesEnabled) continue;
        if (!categoryOptions.has(category)) {
          categoryOptions.set(category, {
            value: category,
            label: fallbackCategoryLabels[category] || categoryLabel(category)
          });
        }
      }
      for (const category of presentCategories) {
        if (!category || category === "external" || categoryOptions.has(category)) continue;
        categoryOptions.set(category, {
          value: category,
          label: fallbackCategoryLabels[category] || categoryLabel(category)
        });
      }
      const ITYPE_CATS = [
        { itype: "Anime", label: "Anime", catId: "anime", value: "itype:Anime" },
        { itype: "Audiobook", label: "Audiobooks", catId: "audiobooks", value: "itype:Audiobook" },
        { itype: "Ebook", label: "Ebooks", catId: "ebooks", value: "itype:Ebook" },
        { itype: "Music", label: "Music", catId: "music", value: "itype:Music" }
      ];
      const presentItypes = this._collectPresentItypes(this.items);
      ITYPE_CATS.forEach((ic2) => {
        if (presentItypes.has(ic2.itype) && !folderCatIds.has(ic2.catId)) {
          categoryOptions.set(ic2.value, { value: ic2.value, label: ic2.label });
        }
      });
      const withCounts = [...cats, ...categoryOptions.values()].map((cat) => ({
        ...cat,
        count: this.getAvailableCategoryCount(cat.value)
      }));
      const all = withCounts.filter((c2) => c2.value === "all");
      const rest = withCounts.filter((c2) => c2.value !== "all" && Number(c2.count || 0) > 0).sort((a2, b2) => a2.label.localeCompare(b2.label));
      return [...all, ...rest];
    },
    flatCategories() {
      if (this.categories.length > 0) {
        const byId = new Map(this.categories.filter((c2) => c2.id !== "tv").map((c2) => [c2.id, c2]));
        for (const [category, value] of Object.entries(this.items || {})) {
          if (!Array.isArray(value) || value.length === 0) continue;
          if (!category || category === "tv" || category === "external" || byId.has(category)) continue;
          const meta = categoryMeta[category];
          byId.set(category, {
            id: category,
            label: meta?.label || categoryLabel(category),
            icon: meta?.icon || "folder",
            color: meta?.color || "gray"
          });
        }
        return Array.from(byId.values());
      }
      return [
        { id: categoryMeta.movies.id, label: categoryMeta.movies.label, icon: categoryMeta.movies.icon, color: categoryMeta.movies.color },
        { id: categoryMeta.misc.id, label: categoryMeta.misc.label, icon: categoryMeta.misc.icon, color: categoryMeta.misc.color }
      ];
    },
    cacheAge() {
      if (!this.cachedAt) return "";
      const age = Math.round(Date.now() / 1e3 - this.cachedAt);
      if (age < 2) return "just now";
      if (age < 60) return `${age}s ago`;
      return `${Math.floor(age / 60)}m ago`;
    },
    queueCategoryCounts() {
      const counts = {};
      for (const item of this.queueItems) {
        const cat = (item.category || "other").toLowerCase();
        counts[cat] = (counts[cat] || 0) + 1;
      }
      return Object.entries(counts).sort(([a2], [b2]) => a2.localeCompare(b2)).map(([cat, count]) => ({ cat, count, label: categoryLabel(cat) }));
    },
    finishedPreview() {
      return this.finished.slice(0, 3);
    },
    jobQueueCount() {
      return this.running.length + this.queued.length;
    },
    jobQueueEntries() {
      const activeEntries = this.running.map((job, index2) => ({
        ...job,
        _queueEntryType: "active",
        _queuePosition: index2 + 1,
        _queuedIndex: null
      }));
      const queuedEntries = this.queued.map((job, index2) => ({
        ...job,
        _queueEntryType: "queued",
        _queuePosition: activeEntries.length + index2 + 1,
        _queuedIndex: index2
      }));
      return [...activeEntries, ...queuedEntries];
    },
    jobQueueControlTarget() {
      return this.jobQueueEntries.find((job) => job.job_id === this.jobQueueControlJobId)
        || this.jobQueueEntries.find((job) => job.status === "running")
        || this.jobQueueEntries.find((job) => job.status === "paused")
        || this.jobQueueEntries[0]
        || null;
    },
    hasMoreFinished() {
      return this.finished.length > 3;
    },
    bulkPreviewSummary() {
      return this.bulkPreview && this.bulkPreview.summary || {
        selected: 0,
        planned: 0,
        ready: 0,
        duplicate: 0,
        excluded: 0,
        invalid: 0,
        blocked: 0,
        partial_duplicates: 0,
        ready_bytes: 0
      };
    },
    bulkPreviewNonReadyItems() {
      const items = this.bulkPreview && Array.isArray(this.bulkPreview.items) ? this.bulkPreview.items : [];
      return items.filter((item) => item.outcome !== "ready").slice(0, 50);
    },
    bulkPreviewDestinations() {
      const destinations = this.bulkPreview && this.bulkPreview.destinations || {};
      return Object.entries(destinations).map(([id, counts]) => {
        const indexer = this.activeIndexers.find((item) => item.id === id);
        return {
          id,
          name: indexer && indexer.name || id,
          pending: Number(counts && counts.pending || 0),
          uploaded: Number(counts && counts.uploaded || 0)
        };
      });
    },
    bulkPreviewCanStart() {
      return Number(this.bulkPreviewSummary.ready || 0) > 0 && !this.bulkPreviewLoading;
    },
    queuedJobModalFilteredItems() {
      const q2 = (this.queuedJobModalSearch || "").trim().toLowerCase();
      if (!q2) return this.queuedJobModalItems;
      return this.queuedJobModalItems.filter((item) => {
        return (item.name || "").toLowerCase().includes(q2) || (item.path || "").toLowerCase().includes(q2);
      });
    },
    activeJobModalFilteredItems() {
      const q2 = (this.activeJobModalSearch || "").trim().toLowerCase();
      if (!q2) return this.activeJobModalItems;
      return this.activeJobModalItems.filter((item) => {
        return (item.name || "").toLowerCase().includes(q2) || (item.path || "").toLowerCase().includes(q2);
      });
    },
    completedJobModalFilteredItems() {
      const query = (this.completedJobModalSearch || "").trim().toLowerCase();
      if (!query) return this.completedJobModalItems;
      return this.completedJobModalItems.filter((item) => {
        return (item.name || "").toLowerCase().includes(query) || (item.path || "").toLowerCase().includes(query);
      });
    },
    activeJobModalCanReorder() {
      return this.jobHasExplicitPaths(this.activeJobModalJob) && this.activeJobModalItems.length > 0;
    },
    activeJobModalInstruction() {
      if (this.activeJobModalCanReorder) {
        return "The current item is locked in. You can drag the remaining items below to change what runs next.";
      }
      if (this.jobHasExplicitPaths(this.activeJobModalJob)) {
        return "The current item is locked in. There are no additional queued items left to reorder.";
      }
      return "This job was started as a category scan. The current item is shown below, but there is no remaining queued item list to reorder.";
    },
    activeJobModalEmptyMessage() {
      if (this.activeJobModalItems.length > 0) {
        return "No active job items match this search.";
      }
      if (this.jobHasExplicitPaths(this.activeJobModalJob)) {
        return "There are no remaining items to reorder for this active job.";
      }
      return "This active job does not have a reorderable item list.";
    },
    queuedJobModalNameDirty() {
      const current = (this.queuedJobModalJob && this.queuedJobModalJob.display_name || "").trim();
      const draft = (this.queuedJobModalName || "").trim();
      return current !== draft;
    },
    queuedJobModalScheduleDirty() {
      const currentEpoch = this.scheduleEpoch(this.queuedJobModalJob && this.queuedJobModalJob.run_after || null);
      const draftEpoch = this.scheduleEpoch(this.buildQueuedJobRunAfterIso());
      return currentEpoch !== draftEpoch;
    },
    /**
     * Group queue items by their parent folder so related items
     * (e.g. all episodes of a show) display as a collapsible group
     * instead of a flat list.
     */
    groupedQueueItems() {
      if (this.queueItems.length === 0) return [];
      const groups = /* @__PURE__ */ new Map();
      let globalIdx = 0;
      for (const item of this.queueItems) {
        globalIdx++;
        const parts = (item.path || "").replace(/\\/g, "/").replace(/\/$/, "").split("/");
        const lastSeg = parts[parts.length - 1];
        const fileExtRe = /\.(mkv|mp4|avi|ts|m4v|mov|wmv|nzb|rar|zip|7z|srt|sub|idx|nfo|sfv|jpg|jpeg|png|txt)$/i;
        const itemIsFolder = !fileExtRe.test(lastSeg) || (item.itype || "").toLowerCase().includes("pack");
        const parentPath = itemIsFolder ? parts.join("/") : parts.length > 1 ? parts.slice(0, -1).join("/") : "";
        const parentName = itemIsFolder ? lastSeg : parts.length > 1 ? parts[parts.length - 2] : lastSeg;
        if (!groups.has(parentPath)) {
          groups.set(parentPath, {
            parentPath,
            parentName,
            category: this.getCategoryForItem(item) || item.category,
            items: []
          });
        }
        item._queueIndex = globalIdx;
        groups.get(parentPath).items.push(item);
      }
      return Array.from(groups.values()).sort((a2, b2) => a2.parentPath.localeCompare(b2.parentPath, void 0, { numeric: true, sensitivity: "base" }));
    },
    hasVisiblePendingItems() {
      if (this.flatCategories.some((cat) => {
        if (cat.id === "movies" && !this.isMoviesEnabled) return false;
        return this.visibleCategoryCount(cat.id) > 0;
      })) {
        return true;
      }
      return (this.items.external || []).some((group) => this.visibleExtGroupCount(group) > 0);
    },
    // ============================================================
    //  Memoized lookups (computed = automatic dependency tracking)
    // ============================================================
    // Vue rebuilds these only when their reactive deps change. Because the
    // `items` tree is frozen (see deepFreezePendingTree), the only deps
    // are the small reactive scalars/Sets that actually drive filtering.
    /**
     * Indexers that count toward "completed" status. When at least one
     * indexer is in backfill mode, only backfill indexers count; otherwise
     * all active indexers count. Cached so isItemCompleted doesn't allocate
     * a filter() result on every call (was thousands of times per render).
     */
    _completionCheckList() {
      const idxs = this.activeIndexers || [];
      if (idxs.length === 0) return [];
      const backfill = idxs.filter((idx) => idx.backfill);
      return backfill.length > 0 ? backfill : idxs;
    },
    allCategoryMeta() {
      return Object.values(categoryMeta);
    },
    /**
     * Single-pass tree walk that produces all visible-row arrays at once.
     * Replaces the previous per-render `v-show="passesFilters(item)"` model
     * which paid filter cost N times per row per render. Now Vue mounts only
     * the rows that should actually appear.
     *
     * Key insight: every row's visibility check eventually consults the same
     * filter inputs, so we walk once and cache the result. The computed's
     * deps cover every signal that can change visibility.
     */
    _visibilityIndex() {
      const items = this.items;
      const _filterTriggers = [
        this.filterMode,
        this.searchQuery,
        this.literalSearch,
        this.normalizedSelectedCategories,
        this.ignoredPaths,
        this.unignoredPaths,
        this.queuedPaths,
        this.activeIndexers,
        this.externalCategories,
        this.manualExternalCategories,
        this.skipFiles
      ];
      const flatByCat = this._buildFlatByCatIndex(items);
      const { extTopByGroupKey, extChildrenByKey } = this._buildExtVisibilityIndex(items);
      return { flatByCat, extTopByGroupKey, extChildrenByKey };
    }
  },
  methods: {
    /**
     * Extracted from _visibilityIndex: builds the flat (non-external)
     * category -> visible items map. Same behavior, split out to keep
     * _visibilityIndex's own branching down. Must live in `methods`, not
     * `computed` - it takes an argument and is called imperatively.
     */
    _buildFlatByCatIndex(items) {
      const flatByCat = /* @__PURE__ */ new Map();
      for (const [key, val] of Object.entries(items || {})) {
        if (key === "external" || !Array.isArray(val)) continue;
        for (const it2 of val) {
          if (!this.itemPassesFilters(it2, key)) continue;
          const resolvedCategory = this.getCategoryForItem(it2) || key;
          if (!resolvedCategory || resolvedCategory === "external") continue;
          if (!flatByCat.has(resolvedCategory)) flatByCat.set(resolvedCategory, []);
          flatByCat.get(resolvedCategory).push(it2);
        }
      }
      return flatByCat;
    },
    /**
     * Extracted from _visibilityIndex: builds the external top-level and
     * external-children visibility maps. Same behavior, split out to keep
     * _visibilityIndex's own branching down.
     */
    _buildExtVisibilityIndex(items) {
      const extTopByGroupKey = /* @__PURE__ */ new Map();
      const extChildrenByKey = /* @__PURE__ */ new Map();
      const walkExtChildren = (parent) => {
        const children = parent.children || [];
        if (children.length === 0) {
          extChildrenByKey.set(parent.key, []);
          return;
        }
        const visible = [];
        for (const child of children) {
          if (this.extItemPassesFilters(child)) visible.push(child);
          if (child.is_dir && child.children && child.children.length > 0) {
            walkExtChildren(child);
          }
        }
        extChildrenByKey.set(parent.key, visible);
      };
      for (const [groupIndex, group] of (items?.external || []).entries()) {
        const groupKey = this.externalGroupKey(group, groupIndex);
        const topVisible = [];
        for (const it2 of group.items || []) {
          if (this.extItemPassesFilters(it2)) topVisible.push(it2);
          if (it2.is_dir && it2.children && it2.children.length > 0) {
            walkExtChildren(it2);
          }
        }
        extTopByGroupKey.set(groupKey, topVisible);
      }
      return { extTopByGroupKey, extChildrenByKey };
    },
    destroySortableInstance(instanceKey) {
      const instance = this[instanceKey];
      if (instance) {
        instance.destroy();
        this[instanceKey] = null;
      }
    },
    createSortableInstance(instanceKey, container, options) {
      this.destroySortableInstance(instanceKey);
      if (!container) return null;
      const instance = new Sortable(container, options);
      this[instanceKey] = instance;
      return instance;
    },
    selectAllCategories() {
      this.selectedCategories = ["all"];
    },
    clearCategorySelection() {
      this.selectedCategories = ["all"];
    },
    isCategoryChecked(value) {
      if (value === "all") return this.selectedCategorySet.has("all");
      return this.selectedCategorySet.has(value);
    },
    toggleCategorySelection(value, checked) {
      if (value === "all") {
        this.selectedCategories = ["all"];
        return;
      }
      const next = new Set(this.selectedCategories || []);
      next.delete("all");
      if (checked) next.add(value);
      else next.delete(value);
      this.selectedCategories = next.size > 0 ? Array.from(next) : ["all"];
    },
    _normalizeSelectedCategories() {
      const normalized = this.normalizedSelectedCategories;
      const current = Array.isArray(this.selectedCategories) ? Array.from(new Set(this.selectedCategories.filter(Boolean))) : [];
      if (normalized.join("|") !== current.join("|")) {
        this.selectedCategories = normalized;
      }
    },
    itemMatchesCategorySelection(item, sectionCategory) {
      if (this.selectedCategorySet.has("all")) return true;
      const resolvedCategory = this.getCategoryForItem(item);
      if (resolvedCategory && this.selectedCategorySet.has(resolvedCategory)) return true;
      if (!resolvedCategory && sectionCategory && this.selectedCategorySet.has(sectionCategory)) return true;
      if (item && item.itype && this.selectedCategorySet.has(`itype:${item.itype}`)) return true;
      return false;
    },
    getAvailableCategoryCount(value) {
      if (value === "all") {
        return Number(this.summary?.total || 0);
      }
      const items = this.items || {};
      let count = 0;
      for (const [sectionCategory, sectionItems] of Object.entries(items)) {
        if (sectionCategory === "external" || !Array.isArray(sectionItems)) continue;
        count += sectionItems.filter((item) => this._itemMatchesCategoryValue(item, sectionCategory, value)).length;
      }
      for (const group of items.external || []) {
        count += (group.items || []).filter((item) => this._externalItemMatchesCategoryValue(item, value)).length;
      }
      return count;
    },
    _itemMatchesCategoryValue(item, sectionCategory, value) {
      if (!item) return false;
      const resolvedCategory = this.getCategoryForItem(item);
      if (value === "all") return true;
      if (resolvedCategory && value === resolvedCategory) return true;
      if (!resolvedCategory && sectionCategory && value === sectionCategory) return true;
      if (typeof value === "string" && value.startsWith("itype:")) {
        return item.itype === value.slice(6);
      }
      return false;
    },
    _externalItemMatchesCategoryValue(item, value, visited = /* @__PURE__ */ new WeakSet()) {
      if (!item || typeof item !== "object") return false;
      if (visited.has(item)) return false;
      visited.add(item);
      if (this._itemMatchesCategoryValue(item, "external", value)) return true;
      const children = Array.isArray(item.children) ? item.children : [];
      return children.some((child) => this._externalItemMatchesCategoryValue(child, value, visited));
    },
    isFilterActive(mode) {
      return this.activeFilterModes.has(mode);
    },
    normalizePathKey(path) {
      return (path || "").replace(/\\/g, "/").replace(/\/$/, "").toLowerCase();
    },
    itemPathKey(item) {
      const raw = item && (item.path || item.key || item.name) ? (item.path || item.key || item.name) : "";
      return this.normalizePathKey(raw);
    },
    isAutoIgnoreOverridden(item) {
      const key = this.itemPathKey(item);
      if (!key || !Array.isArray(this.unignoredPaths)) return false;
      const keys = new Set([key]);
      if (item && item.key) keys.add(this.normalizePathKey(item.key));
      if (item && item.path) keys.add(this.normalizePathKey(item.path));
      for (const candidate of keys) {
        if (candidate && this.unignoredPaths.includes(candidate)) return true;
      }
      return false;
    },
    isItemIgnored(item) {
      if (!item) return false;
      const key = this.itemPathKey(item);
      const keys = new Set([key]);
      if (item && item.key) keys.add(this.normalizePathKey(item.key));
      if (item && item.path) keys.add(this.normalizePathKey(item.path));
      const manuallyIgnored = (this.ignoredPaths || []).some((stored) => keys.has(this.normalizePathKey(stored)));
      return !!this.isSelectionIgnored(item) || manuallyIgnored;
    },
    unignoreItem(item) {
      const key = this.itemPathKey(item);
      if (!key) return;
      const next = new Set(this.unignoredPaths || []);
      next.add(key);
      if (item && item.key) next.add(this.normalizePathKey(item.key));
      if (item && item.path) next.add(this.normalizePathKey(item.path));
      this.unignoredPaths = Array.from(next);
      this.ignoredPaths = (this.ignoredPaths || []).filter((path) => {
        const normalized = this.normalizePathKey(path);
        return normalized !== key
          && normalized !== this.normalizePathKey(item && item.key ? item.key : "")
          && normalized !== this.normalizePathKey(item && item.path ? item.path : "");
      });
      this._bumpFilterVersion();
      this.showToast("success", "Unignored", "This item can now be staged if it otherwise passes filters");
    },
    ignoreItem(item) {
      const key = this.itemPathKey(item);
      if (!key) return;
      const merged = new Set(this.ignoredPaths || []);
      merged.add(key);
      if (item && item.key) merged.add(this.normalizePathKey(item.key));
      if (item && item.path) merged.add(this.normalizePathKey(item.path));
      this.ignoredPaths = Array.from(merged);
      this.unignoredPaths = (this.unignoredPaths || []).filter((path) => {
        const normalized = this.normalizePathKey(path);
        return normalized !== key
          && normalized !== this.normalizePathKey(item && item.key ? item.key : "")
          && normalized !== this.normalizePathKey(item && item.path ? item.path : "");
      });
      this._bumpFilterVersion();
      this.showToast("success", "Ignored", "This item is hidden from staging until unignored");
    },
    toggleIgnoredItem(item) {
      if (this.isItemIgnored(item)) {
        this.unignoreItem(item);
      } else {
        this.ignoreItem(item);
      }
    },
    isZeroSize(size2) {
      return Number(size2) === 0;
    },
    sizeToneClass(size2, defaultClass = "text-notion-text-tertiary") {
      return this.isZeroSize(size2) ? "text-notion-error font-semibold" : defaultClass;
    },
    ignoreSelectedItems() {
      const selection = this.buildSelectedActionPayloads();
      if (selection.items.length === 0) return;
      const merged = new Set(this.ignoredPaths || []);
      for (const item of selection.items) {
        if (!item || !item.path) continue;
        merged.add(this.normalizePathKey(item.path));
      }
      this.ignoredPaths = Array.from(merged);
      const count = selection.items.length;
      this.selectedItems = /* @__PURE__ */ new Set();
      this.selectedMeta = /* @__PURE__ */ new Map();
      this.showToast("success", "Ignored", `${count} item(s) added to ignored list`);
    },
    async clearIgnoredItems() {
      const count = this.ignoredCount;
      if (count === 0) return;
      const ok = await this.confirmDialog(`Clear ${count} ignored item(s)?`, {
        title: "Clear Ignored List",
        detail: "Those items will show up in the pending list again.",
        danger: true,
        confirmLabel: "Clear List"
      });
      if (!ok) return;
      this.ignoredPaths = [];
      this.showToast("success", "Cleared", "Ignored list cleared");
    },
    /**
     * Checks whether a pending item passes all active client-side filters.
     */
    itemPassesFilters(item, sectionCategory = null) {
      const categoryKey = this.getCategoryForItem(item) || sectionCategory;
      if (!this.itemMatchesCategorySelection(item, categoryKey)) return false;
      if (this.isFilterActive("hideIgnored") && this.isItemIgnored(item)) return false;
      if (this.isFilterActive("hideQueued") && this.isItemQueued(item)) return false;
      if (this.isFilterActive("onlyQueued") && !this.isItemQueued(item)) return false;
      if (this.isFilterActive("hideCompleted") && this.isItemCompleted(item)) return false;
      if (this.isFilterActive("showCompleted") && !this.isItemCompleted(item)) return false;
      if (this.searchQuery && !this.matchesSearch(item.name)) return false;
      return true;
    },
    /**
     * Client-side name search: same smart-word logic as the server's is_match().
     * Separators (dots, dashes, underscores, brackets) treated as spaces.
     * All words in the query must be present in the normalised target.
     */
    matchesSearch(name) {
      const q2 = this.searchQuery;
      if (!q2) return true;
      const sep = /[\.\-_\[\]()']/g;
      if (this.literalSearch) {
        return name.toLowerCase().includes(q2.toLowerCase());
      }
      const target = name.toLowerCase().replace(sep, " ");
      const words = q2.toLowerCase().replace(sep, " ").split(/\s+/).filter(Boolean);
      return words.every((w2) => target.includes(w2));
    },
    /**
     * Pre-filtered visible-row accessors. They read from the cached
     * `_visibilityIndex` computed (a single tree walk per filter change),
     * so the template can use `v-for` without `v-show` and Vue mounts
     * only the rows that should actually appear.
     */
    visibleFlatItems(catId) {
      const idx = this._visibilityIndex;
      return idx.flatByCat.get(catId) || [];
    },
    visibleExtTopLevel(group) {
      const idx = this._visibilityIndex;
      return idx.extTopByGroupKey.get(this.externalGroupKey(group)) || [];
    },
    visibleExtChildrenOf(item) {
      // 1. Children loaded by lazy API call (reactive store).
      const loaded = this.extLoadedChildren[item.key];
      if (loaded && loaded.length > 0) {
        return loaded.filter((child) => this.extItemPassesFilters(child));
      }
      // 2. Children already present in item from initial API response.
      const direct = this.getRowChildren(item);
      if (direct.length > 0) {
        return direct.filter((child) => this.extItemPassesFilters(child));
      }
      // 3. Pre-computed visibility index (fallback).
      const idx = this._visibilityIndex;
      const mapped = idx.extChildrenByKey.get(item.key);
      return mapped !== undefined ? mapped : [];
    },
    hasRowChildren(item) {
      if (!item || typeof item !== "object") return false;
      if (Array.isArray(item.children) && item.children.length > 0) return true;
      if (Array.isArray(item.files) && item.files.length > 0) return true;
      if (Number(item.child_count || 0) > 0) return true;
      return false;
    },
    getRowChildren(item) {
      if (!item || typeof item !== "object") return [];
      if (Array.isArray(item.children) && item.children.length > 0) return item.children;
      if (Array.isArray(item.files) && item.files.length > 0) return item.files;
      return [];
    },
    getRowChildCount(item) {
      if (!item || typeof item !== "object") return 0;
      const inlineChildren = this.getRowChildren(item);
      if (inlineChildren.length > 0) return inlineChildren.length;
      const declaredCount = Number(item.child_count || 0);
      return Number.isFinite(declaredCount) && declaredCount > 0 ? declaredCount : 0;
    },
    findExternalNodeByKey(targetKey) {
      if (!targetKey) return null;
      const visited = new Set();
      const walk = (node) => {
        if (!node || typeof node !== "object") return null;
        if (visited.has(node)) return null;
        visited.add(node);
        if (node.key === targetKey) return node;
        const directChildren = Array.isArray(node.children) ? node.children : [];
        const loadedChildren = Array.isArray(this.extLoadedChildren[node.key]) ? this.extLoadedChildren[node.key] : [];
        const children = [...directChildren, ...loadedChildren];
        for (const child of children) {
          const found = walk(child);
          if (found) return found;
        }
        return null;
      };
      for (const group of this.items.external || []) {
        for (const item of group.items || []) {
          const found = walk(item);
          if (found) return found;
        }
      }
      return null;
    },
    async ensureExtChildrenLoaded(itemOrKey) {
      const key = typeof itemOrKey === "string" ? itemOrKey : (itemOrKey && itemOrKey.key);
      // Already loaded into reactive store - skip the API call.
      if (key && this.extLoadedChildren[key] && this.extLoadedChildren[key].length > 0) return;
      const node = typeof itemOrKey === "string" ? this.findExternalNodeByKey(itemOrKey) : itemOrKey;
      if (!node || typeof node !== "object") return;
      const expectedChildren = Number(node.child_count || 0);
      if (expectedChildren <= 0) return;
      // Nodes are deep-frozen so node.__childrenLoading can't be written.
      // Track in-flight requests in a mutable Set on `this` instead.
      if (!this._extLoadingKeys) this._extLoadingKeys = new Set();
      if (this._extLoadingKeys.has(key)) return;
      this._extLoadingKeys.add(key);
      try {
        const params = new URLSearchParams();
        if (node.key) params.set("key", node.key);
        if (node.path) params.set("path", node.path);
        const data = await this.apiFetch(`/api/pending/children?${params.toString()}`);
        const children = Array.isArray(data == null ? void 0 : data.children) ? data.children : [];
        const inheritedCategory = node.assigned_category_safe || this.getCategoryForItem(node) || "";
        children.forEach((child) => normalizeExtChild(child, inheritedCategory, (p) => this.normalizePathKey(p)));
        // Write into Vue-reactive store so visibleExtChildrenOf re-evaluates.
        if (node.key) this.extLoadedChildren[node.key] = children;
      } catch (error) {
        console.warn("Failed to load pending children", error);
      } finally {
        if (this._extLoadingKeys) this._extLoadingKeys.delete(key);
      }
    },
    extDisclosureChildPassesFilters(child, parent = null) {
      if (!child) return false;
      const parentCategory = parent ? (parent.assigned_category_safe || this.getCategoryForItem(parent) || "") : "";
      const categoryKey = child.assigned_category_safe || this.getCategoryForItem(child) || parentCategory;
      if (!this.itemMatchesCategorySelection({ ...child, detected_category: child.detected_category || parentCategory }, categoryKey)) {
        return false;
      }
      if (this.searchQuery && !this.matchesSearch(child.name)) {
        const descendants = child.children || [];
        return descendants.some((grandchild) => this.extDisclosureChildPassesFilters(grandchild, child));
      }
      return true;
    },
    findExternalAncestorCategory(item) {
      const key = item?.key || "";
      if (!key.startsWith("ext:")) return "";
      const parts = key.split(":");
      if (parts.length < 3) return "";
      const rootRel = (parts.slice(2).join(":") || "").split("/")[0];
      if (!rootRel) return "";
      const rootKey = `${parts[0]}:${parts[1]}:${rootRel}`;
      for (const group of this.items.external || []) {
        const root = (group.items || []).find((node) => node.key === rootKey);
        if (root) return this.getCategoryForItem(root);
      }
      return "";
    },
    /**
     * Backward-compatible count/has helpers, now O(1) reads from the index.
     */
    hasCategoryVisibleItems(catId) {
      return this.visibleFlatItems(catId).length > 0;
    },
    visibleCategoryCount(catId) {
      return this.visibleFlatItems(catId).length;
    },
    hasExtGroupVisibleItems(group) {
      return this.visibleExtTopLevel(group).length > 0;
    },
    visibleExtGroupCount(group) {
      return this.visibleExtTopLevel(group).length;
    },
    /**
     * External items filter – for directories, also considers queued
     * descendants so folder rows stay visible when a child is queued.
     */
    extItemPassesFilters(item) {
      const categoryKey = this.getCategoryForItem(item);
      if (!item.is_dir) {
        return this.itemPassesFilters(item, categoryKey && categoryKey !== "external" ? categoryKey : null);
      }
      const descendants = item.children || [];
      const folderMatchesCategory = !!(categoryKey && categoryKey !== "external" && this.itemMatchesCategorySelection(item, categoryKey));
      if (!folderMatchesCategory && !descendants.some((child) => this.extItemPassesFilters(child))) return false;
      if (this.isFilterActive("hideIgnored") && this.isItemIgnored(item)) return false;
      if (this.isFilterActive("hideQueued") && this.isItemQueued(item)) return false;
      if (this.isFilterActive("onlyQueued") && !this.isItemOrDescendantQueued(item)) return false;
      if (this.isFilterActive("hideCompleted") && this.isItemCompleted(item)) return false;
      if (this.isFilterActive("showCompleted") && !this.isItemCompleted(item)) return false;
      if (this.searchQuery && !this.matchesSearch(item.name)) {
        const descendants2 = item.children || [];
        if (!descendants2.some((child) => this.extItemPassesFilters(child))) return false;
      }
      return true;
    },
    /**
     * Check if an item or any of its filesystem descendants is queued.
     */
    isItemOrDescendantQueued(item) {
      if (this.isItemQueued(item)) return true;
      if (!item.path) return false;
      const p2 = item.path.replace(/\\/g, "/").replace(/\/$/, "");
      for (const qp of this.queuedPaths) {
        const norm = qp.replace(/\\/g, "/").replace(/\/$/, "");
        if (norm.startsWith(p2 + "/")) return true;
      }
      return false;
    },
    // ============================================================
    //  Settings
    // ============================================================
    async loadProcessingSettings() {
      try {
        const settings = await this.apiFetch("/api/settings");
        if (settings && settings.processing) {
          this.processingFilters = {};
          if (this.categorySelectionReady) {
            this._normalizeSelectedCategories();
          }
        }
      } catch (e2) {
        console.error("Failed to load processing settings:", e2);
      }
    },
    // ============================================================
    //  Queue Awareness
    // ============================================================
    _collectQueuedPathStats(items) {
      const paths = /* @__PURE__ */ new Set();
      const normalizedSet = /* @__PURE__ */ new Set();
      const normalizedPrefixes = [];
      let movieCount = 0, tvCount = 0;
      for (const qi2 of items || []) {
        if (qi2.path) {
          paths.add(qi2.path);
          const norm = qi2.path.replace(/\\/g, "/").replace(/\/$/, "").toLowerCase();
          if (norm) {
            normalizedSet.add(norm);
            normalizedPrefixes.push(norm + "/");
          }
        }
        const cat = (qi2.category || "").toLowerCase();
        if (cat === "movies") movieCount++;
        else if (cat === "tv") tvCount++;
      }
      return { paths, normalizedSet, normalizedPrefixes, movieCount, tvCount };
    },
    async loadQueuedPaths() {
      if (this._loadQueuedPathsPromise) return this._loadQueuedPathsPromise;
      this._loadQueuedPathsPromise = (async () => {
        try {
          // Hard safety: never let /api/uploads/queue/items failures destabilize queue page.
          // We only derive queued paths from already-loaded in-memory queueItems here.
          const data = { items: Array.isArray(this.queueItems) ? this.queueItems : [] };
          const { paths, normalizedSet, normalizedPrefixes, movieCount, tvCount } = this._collectQueuedPathStats(data.items);
          this._queuedNormalizedSet = normalizedSet;
          this._queuedNormalizedPrefixes = normalizedPrefixes;
          this.queuedPaths = paths;
          this.queueStats = { total: paths.size, movies: movieCount, tv: tvCount };
        } catch (e2) {
          if (!e2?.isOffline && !e2?.isTimeout) {
            console.debug("loadQueuedPaths soft-failed:", e2?.message || e2);
          }
        } finally {
          this._loadQueuedPathsPromise = null;
        }
      })();
      return this._loadQueuedPathsPromise;
    },
    isItemQueued(item) {
      if (!item || !item.path) return false;
      if (this.queuedPaths.has(item.path)) return true;
      const set = this._queuedNormalizedSet;
      if (!set || set.size === 0) return false;
      const p2 = item.path.replace(/\\/g, "/").replace(/\/$/, "").toLowerCase();
      return set.has(p2);
    },
    /**
     * An item is "effectively complete" if all backfill-mode indexers have
     * uploaded it.  Non-backfill (mode=on) indexers only process new items
     * going forward and will never queue old content, so their absence does
     * not make an already-existing item incomplete.
     * Falls back to server-side `completed` flag when indexer data is absent.
     */
    isItemCompleted(item) {
      if (!item) return false;
      if (this.isSelectionIgnored(item) || this.isItemIgnored(item)) return false;
      if (item.indexers && this.activeIndexers.length > 0) {
        const checkList = this._completionCheckList;
        for (let i2 = 0; i2 < checkList.length; i2++) {
          if (item.indexers[checkList[i2].id] !== true) return false;
        }
        return true;
      }
      return item.completed === true;
    },
    /**
     * Return the ring color CSS variable for an indexer status on an item.
     * Green = success, Yellow = failed (retryable), Red = pending.
     */
    indexerRingColor(item, idxId) {
      if (item.indexers && item.indexers[idxId]) return "var(--notion-success-ring)";
      if (item.indexer_errors && item.indexer_errors[idxId]) return "var(--notion-warning)";
      return "var(--notion-error-ring)";
    },
    /**
     * Return the tooltip label for an indexer on an item.
     */
    indexerTooltip(item, idxName, idxId) {
      if (item.indexers && item.indexers[idxId]) return `${idxName}: Done`;
      if (item.indexer_errors && item.indexer_errors[idxId]) return `${idxName}: Failed \u2014 ${item.indexer_errors[idxId]}`;
      return `${idxName}: Pending`;
    },
    // ============================================================
    //  Pending Data Loading
    // ============================================================
    async loadSummary() {
      try {
        const data = await this.apiFetch("/api/pending/summary");
        if (data && data.summary && data.summary.total > 0) {
          this.summary = data.summary;
          this.summaryReady = true;
          if (Array.isArray(data.categories) && data.categories.length > 0) {
            this.categories = data.categories;
          }
          if (Array.isArray(data.indexers) && data.indexers.length > 0) {
            this.activeIndexers = data.indexers.slice();
            const sel = {};
            data.indexers.forEach((idx) => {
              sel[idx.id] = true;
            });
            this.markIndexerSelection = sel;
          }
        }
      } catch (e2) {
      }
    },
    async loadPending(forceRefresh = false, silent = false) {
      if (this._loadPendingPromise) return this._loadPendingPromise;
      if (!silent && !forceRefresh && this.loading && Object.values(this.items).every((v2) => !Array.isArray(v2) || v2.length === 0)) {
        this._restoreSessionCache();
      }
      this._loadPendingPromise = (async () => {
        if (!silent) {
          this.loading = true;
          this.loadingElapsed = 0;
          clearInterval(this._loadingTimer);
          this._loadingTimer = setInterval(() => {
            this.loadingElapsed++;
          }, 1e3);
        }
        try {
          const selectedSig = this.normalizedSelectedCategories.slice().sort().join(",");
          const sig = `${selectedSig}|${this.literalSearch ? "1" : "0"}|${this.searchQuery || ""}`;
          const params = new URLSearchParams({ category: "all" });
          if (this.searchQuery) params.append("search", this.searchQuery);
          if (this.literalSearch) params.append("literal", "true");
          if (forceRefresh) params.append("refresh", "true");
          if (!forceRefresh && this._lastLoadSig === sig && this.cachedAt != null) {
            params.append("known_cached_at", String(this.cachedAt));
          }
          const data = await this.apiFetch(`/api/pending/items?${params}`, { timeoutMs: 15e3 });
          if (data?.not_modified) {
            const wasDetecting = this.animeDetecting;
            this.animeDetecting = !!data.anime_detecting;
            if (this.animeDetecting && !this._animeWatcher) {
              this._animeWatcher = setInterval(() => this.loadPending(false, true), 5e3);
            } else if (!this.animeDetecting && this._animeWatcher) {
              clearInterval(this._animeWatcher);
              this._animeWatcher = null;
              if (wasDetecting) {
                this.showToast("success", "Anime Check Complete", "Titles identified - badges updated");
              }
            }
            return;
          }
          if (!forceRefresh && this._lastLoadSig === sig && data?.cached_at && this.cachedAt && data.cached_at === this.cachedAt) {
            return;
          }
          const rawItems = data.items || { movies: [], misc: [], external: [] };
          this._lastRawItems = rawItems;
          this.skipFiles = data.skip_files || { enabled: false, display_mode: "disabled" };
          const itemsForState = this.skipFiles.enabled && this.skipFiles.display_mode === "hidden" ? this._stripHiddenSkippedItems(rawItems) : rawItems;
          this._normalizePendingItemsForState(itemsForState);
          this.items = deepFreezePendingTree(itemsForState);
          if (forceRefresh) this.extLoadedChildren = {};
          this.activeIndexers = (data.indexers || []).slice();
          this.summary = data.summary || { movies: 0, misc: 0, external: 0, total: 0 };
          this.cachedAt = data.cached_at || null;
          this._lastLoadSig = sig;
      if (Array.isArray(data.categories) && data.categories.length > 0) {
        this.categories = data.categories;
      }
      await this.loadExternalGroupOrderState();
      this._applyDetectedCategories();
      this.syncExternalGroupOrder();
          this.$nextTick(() => this.initPendingExternalGroupsSortable());
          this.categorySelectionReady = true;
          this._normalizeSelectedCategories();
          const wasDetecting = this.animeDetecting;
          this.animeDetecting = !!data.anime_detecting;
          if (this.animeDetecting && !this._animeWatcher) {
            this._animeWatcher = setInterval(() => this.loadPending(false, true), 5e3);
          } else if (!this.animeDetecting && this._animeWatcher) {
            clearInterval(this._animeWatcher);
            this._animeWatcher = null;
            if (wasDetecting) {
              this.showToast("success", "Anime Check Complete", "Titles identified \u2014 badges updated");
            }
          }
          const sel = {};
          this.activeIndexers.forEach((idx) => {
            sel[idx.id] = true;
          });
          this.markIndexerSelection = sel;
          this._saveSessionCache(data);
        } catch (e2) {
          if (!e2.isOffline) {
            this.showToast("error", "Error", "Failed to load pending items");
          }
        } finally {
          if (!silent) {
            this.loading = false;
            clearInterval(this._loadingTimer);
            this.loadingElapsed = 0;
          }
          this._loadPendingPromise = null;
        }
      })();
      return this._loadPendingPromise;
    },
    _normalizePendingItemsForState(items) {
      if (!items || typeof items !== "object") return items;
      const normalizeCategory = (value) => {
        const raw = (value || "").toString().trim().toLowerCase();
        if (!raw) return "";
        if (/^tv\d*$/.test(raw) || /^series\d*$/.test(raw) || /^shows?\d*$/.test(raw)) return "tv";
        if (/^movies?\d*$/.test(raw) || /^films?\d*$/.test(raw)) return "movies";
        if (/^anime\d*$/.test(raw)) return "anime";
        if (/^ebooks?\d*$/.test(raw)) return "ebooks";
        if (/^audiobooks?\d*$/.test(raw)) return "audiobooks";
        if (/^books?\d*$/.test(raw)) return "books";
        if (/^music\d*$/.test(raw)) return "music";
        if (/^apps?\d*$/.test(raw) || /^games?\d*$/.test(raw)) return "apps";
        if (/^disc\d*$/.test(raw)) return "disc";
        if (/^misc\d*$/.test(raw) || /^other\d*$/.test(raw)) return "misc";
        return raw;
      };
      const isSyntheticRootFilesNode = (node) => {
        if (!node || typeof node !== "object") return false;
        const rawName = String(node.name || node.title || "").toLowerCase();
        if (!rawName.includes("(root files)")) return false;
        const hasPath = !!node.path;
        const hasNested = Array.isArray(node.children) && node.children.length > 0 || Array.isArray(node.files) && node.files.length > 0;
        return hasPath && hasNested;
      };
      const normalizeNode = (node, parentCategory = "") => {
        if (!node || typeof node !== "object") return;
        if (node.__normalizing) return;
        node.__normalizing = true;
        if (!node.key && node.path) {
          node.key = `path:${this.normalizePathKey(node.path)}`;
        }
        const inferredSelfCategory = normalizeCategory(node.itype ? this.itypeToCategory(node.itype) : "");
        const detectedSelfCategory = normalizeCategory(node.detected_category);
        const explicitSelfCategory = normalizeCategory(node.category);
        const inheritedCategory = normalizeCategory(parentCategory);
        const safeCategory = detectedSelfCategory || explicitSelfCategory || inheritedCategory || inferredSelfCategory || "";
        node.assigned_category_safe = safeCategory;
        if (!node.detected_category && safeCategory) node.detected_category = safeCategory;
        if ((!Array.isArray(node.children) || node.children.length === 0) && Array.isArray(node.files) && node.files.length > 0) {
          node.children = node.files;
          if (typeof node.is_dir === "undefined") node.is_dir = true;
        }
        if (Array.isArray(node.children)) {
          node.children.forEach((child) => normalizeNode(child, safeCategory));
        }
        delete node.__normalizing;
      };
      if (Array.isArray(items.external)) {
        items.external.forEach((group, index2) => {
          if (!group || typeof group !== "object") return;
          const firstPath = group.items && group.items[0] && group.items[0].path ? String(group.items[0].path).replace(/\\/g, "/") : "";
          const inferredFolder = firstPath ? firstPath.split("/").slice(0, -1).join("/") : "";
          const rawKey = group.key || group.id || group.label || group.folder_name || inferredFolder || `external-${index2}`;
          group.__ui_key = `external:${index2}:${this.normalizePathKey(rawKey)}`;
          if (!group.folder_name) {
            group.folder_name = group.label || (inferredFolder.split("/").pop() || `External ${index2 + 1}`);
          }
          group.items = group.items || [];
          const groupCatHint = "";
          (group.items || []).forEach((node) => normalizeNode(node, groupCatHint));
          if (group.items.length === 1 && isSyntheticRootFilesNode(group.items[0])) {
            const synthetic = group.items[0];
            const extracted = Array.isArray(synthetic.children) && synthetic.children.length > 0 ? synthetic.children : Array.isArray(synthetic.files) ? synthetic.files : [];
            if (extracted.length > 0) {
              group.items = extracted;
              group.items.forEach((node) => normalizeNode(node, groupCatHint));
            }
          }
        });
      }
      for (const [key, val] of Object.entries(items)) {
        if (key === "external" || !Array.isArray(val)) continue;
        val.forEach((node) => normalizeNode(node));
      }
      return items;
    },
    externalGroupKey(group, fallbackIndex = null) {
      if (!group) return fallbackIndex === null ? "external:unknown" : `external:${fallbackIndex}`;
      if (group.__ui_key) return group.__ui_key;
      const rawKey = group.key || group.id || group.label || group.folder_name || "";
      if (rawKey) return `external:${this.normalizePathKey(rawKey)}`;
      return fallbackIndex === null ? "external:unknown" : `external:${fallbackIndex}`;
    },
    externalGroupCollapseKey(group) {
      return `ext_${this.externalGroupKey(group)}`;
    },
    loadExternalGroupOrder() {
      try {
        const raw = localStorage.getItem(EXTERNAL_GROUP_ORDER_KEY);
        const parsed = JSON.parse(raw || "[]");
        return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
      } catch (_e2) {
        return [];
      }
    },
    async loadExternalGroupOrderState(forceRefresh = false) {
      if (this.pendingExternalGroupOrderLoaded && !forceRefresh) {
        return this.pendingExternalGroupOrder;
      }
      try {
        const data = await this.apiFetch("/api/pending/order");
        const serverOrder = Array.isArray(data == null ? void 0 : data.order) ? data.order.filter(Boolean).map(String) : [];
        const localOrder = this.loadExternalGroupOrder();
        const order = serverOrder.length ? serverOrder : localOrder;
        this.pendingExternalGroupOrder = order;
        this.pendingExternalGroupOrderLocked = !!(data == null ? void 0 : data.locked);
        this.pendingExternalGroupOrderLoaded = true;
        try {
          localStorage.setItem(EXTERNAL_GROUP_ORDER_KEY, JSON.stringify(order));
          localStorage.setItem(EXTERNAL_GROUP_LOCK_KEY, this.pendingExternalGroupOrderLocked ? "true" : "false");
        } catch (_e2) {
        }
        if (!serverOrder.length && order.length) {
          void this.apiPut("/api/pending/order", { order }).catch(() => {
          });
        }
        return order;
      } catch (_e2) {
        const fallbackOrder = this.loadExternalGroupOrder();
        this.pendingExternalGroupOrder = fallbackOrder;
        this.pendingExternalGroupOrderLocked = this.loadExternalGroupOrderLocked();
        this.pendingExternalGroupOrderLoaded = true;
        return fallbackOrder;
      }
    },
    saveExternalGroupOrder(order) {
      const normalized = Array.isArray(order) ? order.filter(Boolean).map(String) : [];
      this.pendingExternalGroupOrder = normalized;
      this.pendingExternalGroupOrderLoaded = true;
      try {
        localStorage.setItem(EXTERNAL_GROUP_ORDER_KEY, JSON.stringify(normalized));
      } catch (_e2) {
      }
      void this.apiPut("/api/pending/order", { order: normalized }).catch(() => {
      });
      this._externalGroupOrderVersion += 1;
    },
    loadExternalGroupOrderLocked() {
      try {
        return localStorage.getItem(EXTERNAL_GROUP_LOCK_KEY) === "true";
      } catch (_e2) {
        return false;
      }
    },
    saveExternalGroupOrderLocked(locked) {
      this.pendingExternalGroupOrderLocked = !!locked;
      this.pendingExternalGroupOrderLoaded = true;
      try {
        localStorage.setItem(EXTERNAL_GROUP_LOCK_KEY, this.pendingExternalGroupOrderLocked ? "true" : "false");
      } catch (_e2) {
      }
      void this.apiPut("/api/pending/order/locked", { locked: this.pendingExternalGroupOrderLocked }).catch(() => {
      });
      if (this.pendingExternalGroupOrderLocked) {
        this.destroyPendingExternalGroupsSortable();
      } else {
        this.$nextTick(() => this.initPendingExternalGroupsSortable());
      }
    },
    syncExternalGroupOrder() {
      const groups = this.items.external || [];
      const current = groups.map((group) => this.externalGroupKey(group));
      if (!current.length) return;
      const currentSet = new Set(current);
      const existing = (this.pendingExternalGroupOrderLoaded ? this.pendingExternalGroupOrder : this.loadExternalGroupOrder()).filter((key) => currentSet.has(key));
      const existingSet = new Set(existing);
      const merged = existing.concat(current.filter((key) => !existingSet.has(key)));
      if (merged.length !== existing.length || merged.some((key, index2) => key !== existing[index2])) {
        this.saveExternalGroupOrder(merged);
      }
      groups.forEach((group) => {
        const collapseKey = this.externalGroupCollapseKey(group);
        if (collapseKey && !Object.prototype.hasOwnProperty.call(this.collapsedCategories, collapseKey)) {
          this.collapsedCategories[collapseKey] = true;
        }
      });
    },
    lockPendingDirectoryOrder() {
      const groups = this.orderedExternalGroups || [];
      if (!groups.length) return;
      this.saveExternalGroupOrder(groups.map((group) => this.externalGroupKey(group)));
      const nextLocked = !this.pendingExternalGroupOrderLocked;
      if (nextLocked) {
        groups.forEach((group) => {
          const collapseKey = this.externalGroupCollapseKey(group);
          if (collapseKey) this.collapsedCategories[collapseKey] = true;
        });
      }
      this.saveExternalGroupOrderLocked(nextLocked);
      this.showToast(
        "success",
        nextLocked ? "Order Locked" : "Order Unlocked",
        nextLocked ? "Directory order saved, compacted, and locked." : "Directory drag ordering is enabled."
      );
    },
    destroyPendingExternalGroupsSortable() {
      this.destroySortableInstance("_pendingExternalGroupsSortable");
    },
    initPendingExternalGroupsSortable() {
      this.destroyPendingExternalGroupsSortable();
      if (this.pendingExternalGroupOrderLocked) return;
      const container = this.$refs.pendingExternalGroupsSortable;
      if (!container) return;
      const groups = container.querySelectorAll(".pending-ext-group[data-key]");
      if (groups.length <= 1) return;
      this.createSortableInstance("_pendingExternalGroupsSortable", container, {
        handle: ".pending-ext-drag-handle",
        draggable: ".pending-ext-group",
        animation: 160,
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        dragClass: "sortable-drag",
        onEnd: () => {
          const order = Array.from(container.querySelectorAll(".pending-ext-group[data-key]")).map((el2) => el2.dataset.key).filter(Boolean);
          this.saveExternalGroupOrder(order);
          this.$nextTick(() => this.initPendingExternalGroupsSortable());
        }
      });
    },
    _saveSessionCache(data) {
      try {
        const payload = JSON.stringify({
          items: data.items,
          indexers: data.indexers,
          summary: data.summary,
          cached_at: data.cached_at,
          ts: Date.now()
        });
        if (payload.length > SESSION_CACHE_MAX_BYTES) {
          sessionStorage.removeItem(CACHE_KEY);
          return;
        }
        sessionStorage.setItem(CACHE_KEY, payload);
      } catch (e2) {
      }
    },
    _restoreSessionCache() {
      try {
        const raw = sessionStorage.getItem(CACHE_KEY);
        if (!raw) return;
        const cached = JSON.parse(raw);
        if (Date.now() - cached.ts > 3e5) return;
        const cachedItems = cached.items || { movies: [], misc: [], external: [] };
        this._normalizePendingItemsForState(cachedItems);
        this.items = deepFreezePendingTree(cachedItems);
        this.activeIndexers = (cached.indexers || []).slice();
        this.summary = cached.summary || {};
        this.cachedAt = cached.cached_at || null;
        const sel = {};
        this.activeIndexers.forEach((idx) => {
          sel[idx.id] = true;
        });
        this.markIndexerSelection = sel;
        this.syncExternalGroupOrder();
        this.$nextTick(() => this.initPendingExternalGroupsSortable());
      } catch (e2) {
      }
    },
    _applyDetectedCategories() {
      const groups = this.items.external || [];
      const manual = { ...this.manualExternalCategories || {} };
      const nextCategories = {};
      const nextManual = {};
      const visitItem = (item) => {
        if (!item || !item.key) return;
        const manualCategory = manual[item.key];
        if (manualCategory) {
          nextManual[item.key] = manualCategory;
        }
        nextCategories[item.key] = manualCategory || this.serverCategoryForItem(item);
        for (const child of item.children || []) {
          visitItem(child);
        }
      };
      for (const group of groups) {
        for (const item of group.items || []) {
          visitItem(item);
        }
      }
      this.manualExternalCategories = nextManual;
      this.externalCategories = nextCategories;
    },
    seriesSignature(value) {
      return String(value || "").replace(/\.[a-z0-9]{2,5}$/i, " ").replace(/\bS\d{1,2}[.\s_-]*E\d{1,3}\b/gi, " ").replace(/\bS\d{1,2}\b/gi, " ").replace(/\bE\d{1,3}\b/gi, " ").replace(/\b(?:19|20)\d{2}\b/g, " ").replace(/\b(?:2160p|1080p|1080i|720p|576p|480p|blu[ ._-]?ray|bdrip|brrip|remux|web[ ._-]?dl|webrip|hdtv|dvd|dvdrip|x26[45]|h\.?26[45]|avc|hevc|aac|flac|dts|dual|audio|nano|10bit)\b/gi, " ").replace(/[^a-z0-9]+/gi, " ").replace(/\b\d{1,3}\b/g, " ").trim().toLowerCase();
    },
    /**
     * Collect all unique `itype` strings present anywhere in the items tree.
     * Used to build the synthetic itype-based filter entries in availableCategories.
     */
    _collectPresentItypes(items) {
      const itypes = /* @__PURE__ */ new Set();
      if (!items) return itypes;
      const visited = /* @__PURE__ */ new WeakSet();
      const visitExternalItem = (item) => {
        if (!item || typeof item !== "object") return;
        if (visited.has(item)) return;
        visited.add(item);
        if (item.itype) itypes.add(item.itype);
        for (const child of item.children || []) {
          visitExternalItem(child);
        }
      };
      for (const group of items.external || []) {
        for (const it2 of group.items || []) {
          visitExternalItem(it2);
        }
      }
      for (const [key, val] of Object.entries(items)) {
        if (key === "external" || !Array.isArray(val)) continue;
        for (const it2 of val) {
          if (it2.itype) itypes.add(it2.itype);
        }
      }
      return itypes;
    },
    _collectPresentCategories(items) {
      const categories = /* @__PURE__ */ new Set();
      if (!items) return categories;
      const visited = /* @__PURE__ */ new WeakSet();
      for (const [key, val] of Object.entries(items)) {
        if (key === "external" || !Array.isArray(val) || val.length === 0) continue;
        categories.add(key);
      }
      const visitExternalItem = (item) => {
        if (!item || typeof item !== "object") return;
        if (visited.has(item)) return;
        visited.add(item);
        const category = this.getCategoryForItem(item);
        if (category && category !== "external") categories.add(category);
        for (const child of item.children || []) {
          visitExternalItem(child);
        }
      };
      for (const group of items.external || []) {
        for (const item of group.items || []) {
          visitExternalItem(item);
        }
      }
      return categories;
    },
    /**
     * Return a copy of `items` filtered to only contain entries whose
     * `itype` matches the given string.  Preserves the structural shape
     * (external group nesting, flat arrays).
     */
    _filterItemsByItype(items, itype) {
      if (!items || !itype) return items;
      const result = {};
      result.external = (items.external || []).map((g2) => {
        const fitems = (g2.items || []).filter((it2) => it2.itype === itype);
        return fitems.length ? { ...g2, items: fitems } : null;
      }).filter(Boolean);
      for (const [key, val] of Object.entries(items)) {
        if (key === "external") continue;
        result[key] = Array.isArray(val) ? val.filter((it2) => it2.itype === itype) : val;
      }
      return result;
    },
    /**
     * Map an itype string to the best matching upload category id.
     * Delegates to the shared itypeToCategory utility from page-base.
     */
    itypeToCategory(itype) {
      return sharedItypeToCategory(itype, this.flatCategories);
    },
    resolveUploadItype(itype, category) {
      if ((category || "").toString().toLowerCase() === "anime") return "Anime";
      return !itype || itype === "External" ? categoryToItype(category, "Misc") : itype;
    },
    isSelectionIgnored(item) {
      if (!item) return false;
      if (this.isAutoIgnoreOverridden(item)) return false;
      if (this.getCategoryForItem(item) === "disc") return false;
      if ((item.itype || "").toString().toUpperCase() === "DISC") return false;
      const status = String(item.status || "").toUpperCase();
      return item.ignored === true
        || item.auto_select_ignored === true
        || status === "IGNORED"
        || status === "SKIPPED";
    },
    isAutoSelectable(item) {
      if (!item || this.isItemSkipped(item) || this.isItemExcluded(item)) return false;
      if (String(item.status || "").toUpperCase() === "VALID" && item.eligible !== false && item.ignored !== true) return true;
      if (this.getCategoryForItem(item) === "disc") return true;
      if ((item.itype || "").toString().toUpperCase() === "DISC") return true;
      if (this.isSelectionIgnored(item)) return false;
      return item.auto_selectable !== false;
    },
    isPartiallyIgnored(item) {
      if (!item || !item.is_dir || !Array.isArray(item.children) || item.children.length === 0) return false;
      if (this.getCategoryForItem(item) === "disc") return false;
      if ((item.itype || "").toString().toUpperCase() === "DISC") return false;
      let hasIgnored = false;
      let hasSelectable = false;
      const visit = (node) => {
        if (!node || hasIgnored && hasSelectable) return;
        if (this.isSelectionIgnored(node)) hasIgnored = true;
        if (this.isAutoSelectable(node)) hasSelectable = true;
        for (const child of node.children || []) {
          visit(child);
          if (hasIgnored && hasSelectable) return;
        }
      };
      for (const child of item.children) {
        visit(child);
        if (hasIgnored && hasSelectable) break;
      }
      return hasIgnored && hasSelectable;
    },
    isFullyIgnoredTree(item) {
      if (!item) return false;
      if (!item.is_dir || !Array.isArray(item.children) || item.children.length === 0) {
        return this.isSelectionIgnored(item);
      }
      let leafCount = 0;
      let ignoredLeafCount = 0;
      const visit = (node) => {
        if (!node) return;
        const kids = Array.isArray(node.children) ? node.children : [];
        if (kids.length === 0 || !node.is_dir) {
          leafCount += 1;
          if (this.isSelectionIgnored(node)) ignoredLeafCount += 1;
          return;
        }
        for (const child of kids) {
          visit(child);
        }
      };
      visit(item);
      return leafCount > 0 && ignoredLeafCount === leafCount;
    },
    shouldShowYieldBubble(item) {
      if (!item) return false;
      if (item.is_dir) return this.isSelectionIgnored(item) || this.isPartiallyIgnored(item) || this.isFullyIgnoredTree(item);
      return this.isSelectionIgnored(item) && !this.isPartiallyIgnored(item);
    },
    getDetectionLabel(item) {
      if (!item) return "";
      const category = item.detected_category || this.getCategoryForItem(item);
      const method = item.detection_method || "Folder fallback";
      const flags = Array.isArray(item.detection_flags) && item.detection_flags.length ? ` [${item.detection_flags.map((flag) => String(flag).toUpperCase()).join(", ")}]` : "";
      const override = item.detection_override ? ` - ${item.detection_override}` : "";
      return category ? `${category}${flags} via ${method}${override}` : `${method}${flags}${override}`;
    },
    getSelectionTitle(item) {
      if (!item) return "";
      if (this.isSelectionIgnored(item)) return item.auto_select_reason || "Ignored";
      if (this.isPartiallyIgnored(item)) return item.auto_select_reason || "Mixed folder: some children are ignored and some remain selectable";
      if (!this.isAutoSelectable(item) && item.children && item.children.length > 0) {
        return item.auto_select_reason || "Selectable descendants only";
      }
      return "Select item";
    },
    getVisibleSelectableItemsMap() {
      const visible = /* @__PURE__ */ new Map();
      this.flatCategories.forEach((cat) => {
        for (const item of this.getVisibleSelectableFlatItems(cat.id)) {
          visible.set(item.key, this._buildSelectionMeta(item, cat.id));
        }
      });
      for (const [, selectable] of this._groupSelectableItems) {
        for (const node of selectable) {
          visible.set(node.key, this._buildSelectionMeta(node));
        }
      }
      return visible;
    },
    syncSelectionToVisible() {
      if (this.selectedItems.size === 0 && this.selectedMeta.size === 0) return;
      const visible = this.getVisibleSelectableItemsMap();
      const nextSet = /* @__PURE__ */ new Set();
      const nextMeta = /* @__PURE__ */ new Map();
      for (const key of this.selectedItems) {
        const meta = visible.get(key);
        if (!meta) continue;
        nextSet.add(key);
        nextMeta.set(key, meta);
      }
      const changed = nextSet.size !== this.selectedItems.size || nextMeta.size !== this.selectedMeta.size || Array.from(nextSet).some((key) => !this.selectedItems.has(key));
      if (!changed) {
        return;
      }
      this.selectedItems = nextSet;
      this.selectedMeta = nextMeta;
    },
    _buildSelectionMeta(item, categoryOverride = null) {
      const category = categoryOverride || this.getCategoryForItem(item);
      return {
        path: item.path,
        category,
        itype: item.itype || "External",
        is_dir: !!item.is_dir,
        manual_category: item.key ? this.manualExternalCategories[item.key] || "" : "",
        detected_category: item.detected_category || category || "",
        detection_method: item.detection_method || "",
        detection_flags: item.detection_flags || [],
        detection_override: item.detection_override || "",
        selection_reason: item.auto_select_reason || ""
      };
    },
    _selectionKeyIsDirectory(key, fallbackPath = "") {
      const meta = this.selectedMeta.get(key);
      if (meta && typeof meta.is_dir === "boolean") return meta.is_dir;
      const path = fallbackPath || meta && meta.path || "";
      const lastSeg = String(path).replace(/\\/g, "/").split("/").pop() || "";
      return !/\.(mkv|mp4|avi|ts|m4v|mov|wmv|rar|zip|7z|nzb|iso|img|epub|m4b|mp3|flac|pdf)$/i.test(lastSeg);
    },
    isSeasonalPackItem(item) {
      if (!item) return false;
      const path = item.path || item.target_path || item.source_path || "";
      const name = item.name || String(path).replace(/\\/g, "/").split("/").pop() || "";
      const isDir = typeof item.is_dir === "boolean" ? item.is_dir : this._selectionKeyIsDirectory(item.key || path, path);
      if (!isDir) return false;
      const category = item.category || item.detected_category || this.getCategoryForItem(item);
      const itype = item.itype || "";
      const label = `${name} ${path}`;
      if (/\bS\d{1,2}\s*[-–]\s*S\d{1,2}\b/i.test(label)) {
        return false;
      }
      const seasonLike = /(?:^|[.\s_(-])S\d{1,2}(?:[.\s_)-]|$)|Season[.\s_-]*\d{1,2}|Series[.\s_-]*\d{1,2}/i.test(label);
      return category === "tv" || itype === "TV Show" || seasonLike;
    },
    isMultiSeasonRangeItem(item) {
      if (!item) return false;
      const path = item.path || item.target_path || item.source_path || "";
      const name = item.name || String(path).replace(/\\/g, "/").split("/").pop() || "";
      const label = `${name} ${path}`;
      return /\bS\d{1,2}\s*[-–]\s*S\d{1,2}\b/i.test(label);
    },
    getVisibleSelectableFlatItems(catKey) {
      return (this.items[catKey] || []).filter((item) => this.itemPassesFilters(item, catKey) && this.isAutoSelectable(item));
    },
    collectExternalLeafItems(item) {
      if (!item) return [];
      if (!item.is_dir || !item.children || item.children.length === 0) {
        return [item];
      }
      return item.children.flatMap((child) => this.collectExternalLeafItems(child));
    },
    visitVisibleExternalNodes(item, visitor) {
      if (!item || !this.extItemPassesFilters(item)) return;
      visitor(item);
      for (const child of item.children || []) {
        this.visitVisibleExternalNodes(child, visitor);
      }
    },
    collectVisibleExternalNodes(item) {
      const nodes = [];
      this.visitVisibleExternalNodes(item, (node) => nodes.push(node));
      return nodes;
    },
    collectVisibleSelectableExternalNodes(item) {
      const nodes = [];
      this.visitVisibleExternalNodes(item, (node) => {
        if (this.isAutoSelectable(node)) {
          nodes.push(node);
        }
      });
      return nodes;
    },
    _dedupeSelectionEntries(entries) {
      const seen = /* @__PURE__ */ new Set();
      const deduped = [];
      for (const entry of entries) {
        const key = entry?.item?.key;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        deduped.push(entry);
      }
      return deduped;
    },
    _externalNodeHasHiddenDescendants(item) {
      const visibleKeys = new Set(this.collectVisibleExternalNodes(item).map((node) => node.key));
      let hidden = false;
      const visit = (node) => {
        if (!node || hidden) return;
        if (node !== item && !visibleKeys.has(node.key)) {
          hidden = true;
          return;
        }
        for (const child of node.children || []) {
          visit(child);
        }
      };
      visit(item);
      return hidden;
    },
    collectSelectedExternalActionEntries(item) {
      if (!item || !this.extItemPassesFilters(item)) return [];
      const visibleChildren = (item.children || []).filter((child) => this.extItemPassesFilters(child));
      const itemSelected = this.selectedItems.has(item.key) && this.isAutoSelectable(item);
      const expandSeasonPack = (this.isSeasonalPackItem(item) || this.isMultiSeasonRangeItem(item)) && visibleChildren.length > 0;
      if (visibleChildren.length === 0) {
        return itemSelected ? [{ item, categoryOverride: null }] : [];
      }
      if (itemSelected && expandSeasonPack) {
        return this.collectVisibleExternalActionEntries(item);
      }
      const entries = [];
      if (itemSelected) {
        entries.push({ item, categoryOverride: null });
      }
      for (const child of visibleChildren) {
        entries.push(...this.collectSelectedExternalActionEntries(child));
      }
      return this._dedupeSelectionEntries(entries);
    },
    collectVisibleExternalActionEntries(item) {
      if (!item || !this.extItemPassesFilters(item)) return [];
      const visibleChildren = (item.children || []).filter((child) => this.extItemPassesFilters(child));
      if (visibleChildren.length === 0) {
        return this.isAutoSelectable(item) ? [{ item, categoryOverride: null }] : [];
      }
      const expandSeasonPack = this.isSeasonalPackItem(item) || this.isMultiSeasonRangeItem(item);
      if (!expandSeasonPack && this.isAutoSelectable(item) && !this._externalNodeHasHiddenDescendants(item)) {
        return [{ item, categoryOverride: null }];
      }
      const entries = [];
      for (const child of visibleChildren) {
        entries.push(...this.collectVisibleExternalActionEntries(child));
      }
      return this._dedupeSelectionEntries(entries);
    },
    getSelectedActionEntries() {
      const entries = [];
      this.flatCategories.forEach((cat) => {
        this.getVisibleSelectableFlatItems(cat.id).forEach((item) => {
          if (!this.selectedItems.has(item.key)) return;
          entries.push({ item, categoryOverride: cat.id });
        });
      });
      for (const [, selectable] of this._groupSelectableItems) {
        for (const node of selectable) {
          if (this.selectedItems.has(node.key)) {
            entries.push({ item: node, categoryOverride: null });
          }
        }
      }
      return this._dedupeSelectionEntries(entries);
    },
    getActionEntriesForItem(item, categoryOverride = null) {
      if (!item) return [];
      if (categoryOverride && categoryOverride !== "external") {
        return this.itemPassesFilters(item, categoryOverride) && this.isAutoSelectable(item) ? [{ item, categoryOverride }] : [];
      }
      if (!item.is_dir || !item.children || item.children.length === 0) {
        return this.extItemPassesFilters(item) && this.isAutoSelectable(item) ? [{ item, categoryOverride: null }] : [];
      }
      return this.collectVisibleExternalActionEntries(item);
    },
    buildActionPayloadsFromEntries(entries) {
      const items = [];
      let uncategorizedExternal = 0;
      for (const entry of entries) {
        const item = entry.item;
        const meta = this._buildSelectionMeta(item, entry.categoryOverride);
        let cat = meta.category;
        if (cat === "external") {
          cat = this._resolveExtCategory(item.key);
        }
        if (!cat || cat === "external") {
          uncategorizedExternal++;
          continue;
        }
        items.push({
          key: item.key,
          path: meta.path,
          category: cat,
          manual_category: meta.manual_category || "",
          itype: this.resolveUploadItype(meta.itype, cat),
          detected_category: meta.detected_category || cat,
          detection_method: meta.detection_method || "",
          selection_reason: meta.selection_reason || "",
          name: item.name || (meta.path || item.key || "").replace(/\\/g, "/").split("/").pop() || item.key
        });
      }
      return {
        entries,
        items,
        selectedVisibleCount: entries.length,
        selectedRawCount: this.selectedItems.size,
        visibleSelectableCount: this.getVisibleSelectableItemsMap().size,
        excludedCount: entries.length - items.length,
        uncategorizedExternal
      };
    },
    buildSelectedActionPayloads() {
      return this.buildActionPayloadsFromEntries(this.getSelectedActionEntries());
    },
    serverCategoryForItem(item) {
      if (!item) return "";
      const normalizeCategory = (value) => {
        const raw = (value || "").toString().trim().toLowerCase();
        if (!raw || raw === "external") return "";
        if (/^tv\d*$/.test(raw) || /^series\d*$/.test(raw) || /^shows?\d*$/.test(raw)) return "tv";
        if (/^movies?\d*$/.test(raw) || /^films?\d*$/.test(raw) || raw === "movie") return "movies";
        if (/^anime\d*$/.test(raw)) return "anime";
        if (/^disc\d*$/.test(raw)) return "disc";
        if (/^ebooks?\d*$/.test(raw) || raw === "ebook") return "ebooks";
        if (/^books?\d*$/.test(raw) || raw === "book") return "books";
        if (/^audiobooks?\d*$/.test(raw) || raw === "audiobook") return "audiobooks";
        if (/^music\d*$/.test(raw)) return "music";
        if (/^apps?\d*$/.test(raw) || /^games?\d*$/.test(raw) || raw === "app" || raw === "game") return "apps";
        if (/^misc\d*$/.test(raw) || raw === "other") return "misc";
        return raw;
      };
      const directCategory = normalizeCategory(item.detected_category || item.category || item.assigned_category_safe || "");
      if (directCategory) return directCategory;
      return normalizeCategory(item.itype ? this.itypeToCategory(item.itype) : "");
    },
    setExternalCategory(key, value) {
      const nextManual = { ...this.manualExternalCategories || {} };
      if (value) nextManual[key] = value;
      else delete nextManual[key];
      this.manualExternalCategories = nextManual;
      this._applyDetectedCategories();
      const resolvedCategory = this._resolveExtCategory(key) || "external";
      if (this.selectedMeta.size === 0) return;
      const nextMeta = new Map(this.selectedMeta);
      let changed = false;
      for (const [metaKey, meta] of nextMeta.entries()) {
        if (metaKey === key || metaKey.startsWith(`${key}/`)) {
          nextMeta.set(metaKey, { ...meta, category: resolvedCategory });
          changed = true;
        }
      }
      if (changed) this.selectedMeta = nextMeta;
    },
    async correctAnimeCache(item, isAnime) {
      if (!item || !item.name) return;
      try {
        const correction = await this.apiPost("/api/pending/anime-cache", {
          name: item.name,
          is_anime: isAnime
        });
        const correctedCategory = correction.category || (isAnime ? "anime" : "misc");
        this.setExternalCategory(item.key, correctedCategory);
        this.showToast("success", "Anime Detection Corrected", isAnime ? "Marked as anime" : "Marked as not anime");
      } catch (e2) {
        this.showToast("error", "Anime Correction Failed", "Could not save the anime detection correction");
      }
    },
    // ============================================================
    //  Skip Files
    // ============================================================
    /**
     * Return a shallow-restructured copy of the items tree with skipped
     * leaves removed. Used when display_mode === 'hidden'. We must work on
     * a fresh structure (rather than mutate) because `this.items` is later
     * deep-frozen for performance.
     */
    _stripHiddenSkippedItems(rawItems) {
      const result = { ...rawItems };
      for (const [key, val] of Object.entries(rawItems)) {
        if (key === "external" || !Array.isArray(val)) continue;
        result[key] = val.filter((it2) => !it2.skipped);
      }
      if (Array.isArray(rawItems.external)) {
        result.external = rawItems.external.map((group) => ({
          ...group,
          items: (group.items || []).filter((it2) => !it2.skipped)
        }));
      }
      return result;
    },
    isItemSkipped(item) {
      if (this.isAutoIgnoreOverridden(item)) return false;
      return this.skipFiles.enabled && this.skipFiles.display_mode === "disabled" && item && item.skipped;
    },
    isItemExcluded(item) {
      return item && item.excluded === true;
    },
    // ============================================================
    //  Expand / Collapse
    // ============================================================
    async toggleExtItem(key) {
      if (!key) return;
      const node = this.findExternalNodeByKey(key);
      const next = !this.expandedExtItems[key];
      if (next && node) {
        await this.ensureExtChildrenLoaded(node);
      }
      if (typeof this.$set === "function") {
        this.$set(this.expandedExtItems, key, next);
      } else {
        this.expandedExtItems[key] = next;
      }
    },
    collapseAll() {
      this.allExpanded = false;
      this.collapsedCategories = {
        ...this.collapsedCategories,
        movies: true,
        misc: true
      };
      this.flatCategories.forEach((cat) => {
        this.collapsedCategories[cat.id] = true;
      });
      (this.items.external || []).forEach((group) => {
        this.collapsedCategories[this.externalGroupCollapseKey(group)] = true;
      });
      this.expandedExtItems = {};
      this.expandedQueueGroups = {};
    },
    toggleCategoryCollapse(cat) {
      this.collapsedCategories[cat] = !this.collapsedCategories[cat];
    },
    toggleQueueGroup(parentPath) {
      this.expandedQueueGroups[parentPath] = !this.expandedQueueGroups[parentPath];
    },
    isQueueGroupCollapsed(parentPath) {
      return !this.expandedQueueGroups[parentPath];
    },
    /**
     * Determine the upload plan for a queue group.
     * Returns { label, classes } or null.
     */
    groupUploadPlan(group) {
      if (group.category !== "tv" || group.items.length <= 1) return null;
      let hasPack = false;
      let hasEpisodes = false;
      for (const item of group.items) {
        const type = this.resolvedItemType(item);
        if (type === "pack") {
          hasPack = true;
        } else if (type === "episode") {
          hasEpisodes = true;
        }
        if (hasPack && hasEpisodes) break;
      }
      if (hasPack && hasEpisodes) return { label: "Pack + Episodes", classes: "bg-violet-500/15 text-violet-400" };
      if (hasPack) return { label: "Pack Only", classes: "bg-violet-500/15 text-violet-400" };
      if (hasEpisodes) return { label: "Episodes Only", classes: "bg-sky-500/15 text-sky-400" };
      return null;
    },
    /**
     * Resolve the visual type badge for a queue item based on its path.
     * Returns 'pack', 'episode', or null.
     */
    resolvedItemType(item) {
      const itype = (item.itype || "").toLowerCase();
      if (itype.includes("pack") || itype === "tv show") return "pack";
      if (itype === "tv episode") return "episode";
      if (item.category === "tv") {
        const lastSeg = (item.path || "").replace(/\\/g, "/").split("/").pop() || "";
        const fileExtRe = /\.(mkv|mp4|avi|ts|m4v|mov|wmv|rar|zip|7z|nzb)$/i;
        if (!fileExtRe.test(lastSeg)) return "pack";
        return "episode";
      }
      return null;
    },
    async removeGroup(group) {
      if (!this._deletingGroupPaths) this._deletingGroupPaths = /* @__PURE__ */ new Set();
      const groupKey = group.parentPath || group.items.map((i2) => i2.id).join(",");
      if (this._deletingGroupPaths.has(groupKey)) return;
      this._deletingGroupPaths.add(groupKey);
      const items = [...group.items];
      const removed = items.map((item) => ({
        item,
        idx: this.queueItems.findIndex((qi2) => qi2.id === item.id)
      }));
      removed.slice().sort((a2, b2) => b2.idx - a2.idx).forEach(({ idx }) => {
        if (idx >= 0) this.queueItems.splice(idx, 1);
      });
      const results = await Promise.allSettled(
        items.map((item) => this.apiFetch(`/api/uploads/queue/items/${item.id}`, { method: "DELETE" }))
      );
      let failCount = 0;
      results.forEach((result, i2) => {
        if (result.status === "rejected") {
          failCount++;
          const { item, idx } = removed[i2];
          if (idx >= 0) {
            const safeIdx = Math.min(idx, this.queueItems.length);
            this.queueItems.splice(safeIdx, 0, item);
          }
        }
      });
      if (failCount > 0) {
        this.showToast("error", "Error", `Failed to remove ${failCount} item(s) from queue`);
      }
      clearTimeout(this._queuedPathsTimer);
      this._queuedPathsTimer = setTimeout(() => this.loadQueuedPaths(), 1500);
      this._deletingGroupPaths.delete(groupKey);
    },
    // ============================================================
    //  External Folder ↔ Children Selection
    // ============================================================
    toggleExtFolderSelection(item, checked) {
      const nextSet = new Set(this.selectedItems);
      const visitItem = (node) => {
        if (!node || !node.key || !this.extItemPassesFilters(node)) return;
        if (checked) {
          if (this.isAutoSelectable(node)) nextSet.add(node.key);
        } else {
          nextSet.delete(node.key);
        }
        (node.children || []).forEach(visitItem);
      };
      (item.children || []).forEach(visitItem);
      if (checked) {
        if (this.isAutoSelectable(item)) nextSet.add(item.key);
      } else {
        nextSet.delete(item.key);
      }
      this.selectedItems = nextSet;
    },
    isExtFolderAllSelected(item) {
      const selectable = this.collectVisibleSelectableExternalNodes(item);
      if (selectable.length === 0) return false;
      return selectable.every((node) => this.selectedItems.has(node.key));
    },
    toggleExtChildSelection(child, _parentItem, checked) {
      const nextSet = new Set(this.selectedItems);
      const visitItem = (node) => {
        if (!node || !node.key || !this.extItemPassesFilters(node)) return;
        if (checked) {
          if (this.isAutoSelectable(node)) nextSet.add(node.key);
        } else {
          nextSet.delete(node.key);
        }
        (node.children || []).forEach(visitItem);
      };
      visitItem(child);
      this.selectedItems = nextSet;
    },
    forceUploadExtChild(child, event) {
      const cat = this.getCategoryForItem(child);
      const overridden = { ...child, itype: this.resolveUploadItype(child.itype, cat) };
      this.openForceUploadMenu(overridden, event);
    },
    // ============================================================
    //  Selection
    // ============================================================
    toggleItemSelection(item, checked) {
      if (checked && this.isAutoSelectable(item)) {
        this.selectedItems.add(item.key);
      } else {
        this.selectedItems.delete(item.key);
      }
    },
    toggleFlatCategorySelection(catKey, checked) {
      const nextSet = new Set(this.selectedItems);
      this.getVisibleSelectableFlatItems(catKey).forEach((item) => {
        if (checked && this.isAutoSelectable(item)) {
          nextSet.add(item.key);
        } else {
          nextSet.delete(item.key);
        }
      });
      this.selectedItems = nextSet;
    },
    areAllFlatCategorySelected(catKey) {
      const list = this.getVisibleSelectableFlatItems(catKey);
      if (list.length === 0) return false;
      return list.every((item) => this.selectedItems.has(item.key));
    },
    getCategoryIcon(catId) {
      const cat = this.categories.find((c2) => c2.id === catId);
      if (cat && cat.icon) return cat.icon;
      const meta = categoryMeta[catId];
      if (meta && meta.icon) return meta.icon;
      const map = { movies: "film", tv: "tv", misc: "package", external: "folder-input" };
      return map[catId] || "folder";
    },
    getCategoryColor(catId) {
      const cat = this.categories.find((c2) => c2.id === catId);
      const raw = (cat && cat.color) || (categoryMeta[catId] && categoryMeta[catId].color) || { movies: "purple", tv: "cyan", misc: "orange", external: "emerald" }[catId] || "gray";
      return raw.replace(/-\d+$/, "");
    },
    /**
     * Returns label + Tailwind classes for a content-type badge,
     * or null if the itype has no dedicated badge style.
     */
    itypeBadge(itype) {
      const map = {
        "TV Show": { label: "TV", classes: "bg-cyan-500/15 text-cyan-400" },
        "Movie": { label: "Movie", classes: "bg-purple-500/15 text-purple-400" },
        "Anime": { label: "Anime", classes: "bg-pink-500/15 text-pink-400" },
        "Music": { label: "Music", classes: "bg-green-500/15 text-green-400" },
        "Audiobook": { label: "Audiobook", classes: "bg-indigo-500/15 text-indigo-400" },
        "Ebook": { label: "Ebook", classes: "bg-orange-500/15 text-orange-400" },
        "Misc": { label: "Misc", classes: "bg-slate-500/15 text-slate-400" }
      };
      return map[itype] || null;
    },
    toggleExternalSelection(checked) {
      const nextSet = new Set(this.selectedItems);
      (this.items.external || []).forEach((group) => {
        (group.items || []).forEach((item) => {
          const visit = (node) => {
            if (!node || !node.key || !this.extItemPassesFilters(node)) return;
            if (checked) {
              if (this.isAutoSelectable(node)) nextSet.add(node.key);
            } else {
              nextSet.delete(node.key);
            }
            (node.children || []).forEach(visit);
          };
          visit(item);
        });
      });
      this.selectedItems = nextSet;
    },
    areAllExternalSelected() {
      if (!this.items.external || this.totalExternalItems === 0) return false;
      const selectable = [];
      this.items.external.forEach((group) => {
        (group.items || []).forEach((item) => {
          selectable.push(...this.collectVisibleSelectableExternalNodes(item));
        });
      });
      if (selectable.length === 0) return false;
      return selectable.every((item) => this.selectedItems.has(item.key));
    },
    toggleExtGroupSelection(groupOrIdx, checked) {
      const group = typeof groupOrIdx === "number" ? (this.items.external || [])[groupOrIdx] : groupOrIdx;
      if (!group) return;
      const selectable = this._groupSelectableItems.get(this.externalGroupKey(group)) || [];
      const nextSet = new Set(this.selectedItems);
      for (const node of selectable) {
        if (checked) nextSet.add(node.key);
        else nextSet.delete(node.key);
      }
      this.selectedItems = nextSet;
    },
    areAllExtGroupSelected(groupOrIdx) {
      const group = typeof groupOrIdx === "number" ? (this.items.external || [])[groupOrIdx] : groupOrIdx;
      if (!group) return false;
      return this._extGroupAllSelectedMap.get(this.externalGroupKey(group)) || false;
    },
    getAutoCategoryLabel(item) {
      return categoryLabel(this.getExternalItemCategory(item));
    },
    getExternalItemCategory(item) {
      const cat = this.getCategoryForItem(item);
      return cat && cat !== "external" ? cat : "";
    },
    getUploadCategoryForItem(item) {
      if (this.isSelectionIgnored(item)) return "";
      const cat = this.getCategoryForItem(item);
      return cat && cat !== "external" ? cat : "";
    },
    getExternalItemColor(item) {
      return this.getCategoryColor(this.getExternalItemCategory(item));
    },
    getExternalItemIcon(item) {
      return this.getCategoryIcon(this.getExternalItemCategory(item));
    },
    getFolderStateTooltip(item) {
      if (!item || !item.is_dir) return "";
      if (this.isPartiallyIgnored(item)) {
        return "This folder is not uploadable on its own, but some child files still qualify.";
      }
      if (this.isSelectionIgnored(item)) {
        return "This folder is not uploadable on its own. Its child files are still checked individually.";
      }
      return "";
    },
    getCategoryForItem(item) {
      if (!item) return "";
      const normalizeCategory = (value) => {
        const raw = (value || "").toString().trim().toLowerCase();
        if (!raw) return "";
        if (/^tv\d*$/.test(raw) || /^series\d*$/.test(raw) || /^shows?\d*$/.test(raw)) return "tv";
        if (/^movies?\d*$/.test(raw) || /^films?\d*$/.test(raw)) return "movies";
        if (/^anime\d*$/.test(raw)) return "anime";
        if (/^ebooks?\d*$/.test(raw)) return "ebooks";
        if (/^audiobooks?\d*$/.test(raw)) return "audiobooks";
        if (/^books?\d*$/.test(raw)) return "books";
        if (/^music\d*$/.test(raw)) return "music";
        if (/^apps?\d*$/.test(raw) || /^games?\d*$/.test(raw)) return "apps";
        if (/^disc\d*$/.test(raw)) return "disc";
        if (/^misc\d*$/.test(raw) || /^other\d*$/.test(raw)) return "misc";
        const map = {
          movie: "movies",
          movies: "movies",
          tv: "tv",
          television: "tv",
          anime: "anime",
          disc: "disc",
          music: "music",
          book: "books",
          books: "books",
          ebook: "ebooks",
          ebooks: "ebooks",
          audiobook: "audiobooks",
          audiobooks: "audiobooks",
          app: "apps",
          apps: "apps",
          game: "apps",
          games: "apps",
          misc: "misc",
          other: "misc"
        };
        return map[raw] || raw;
      };
      const directManualCategory = item.key ? normalizeCategory(this.manualExternalCategories[item.key]) : "";
      if (directManualCategory && directManualCategory !== "external") return directManualCategory;
      const detectedCategory = normalizeCategory(item.detected_category);
      if (detectedCategory && detectedCategory !== "external") return detectedCategory;
      const explicitCategory = normalizeCategory(item.category);
      if (explicitCategory && explicitCategory !== "external") return explicitCategory;
      const safeAssigned = normalizeCategory(item.assigned_category_safe);
      if (safeAssigned && safeAssigned !== "external") return safeAssigned;
      const itype = (item.itype || "").toLowerCase();
      if (itype === "external") {
        return detectedCategory || "";
      }
      if (itype.includes("season pack") || itype.includes("tv show")) return "tv";
      if (itype.includes("tv") || itype.includes("episode")) return "tv";
      if (itype.includes("movie")) return "movies";
      const itypeCategory = this.itypeToCategory(item.itype);
      if (itypeCategory) return itypeCategory;
      const resolvedExternal = item.key ? this._resolveExtCategory(item.key) : "";
      if (resolvedExternal) return resolvedExternal;
      return detectedCategory || "";
    },
    _resolveExtCategory(key) {
      if (!key) return "";
      let cursor = key;
      while (cursor) {
        if (this.manualExternalCategories[cursor]) return this.manualExternalCategories[cursor];
        if (this.externalCategories[cursor]) return this.externalCategories[cursor];
        const slashIdx = cursor.lastIndexOf("/");
        if (slashIdx <= 0) break;
        cursor = cursor.substring(0, slashIdx);
      }
      return "";
    },
    forceUploadExternal(item, event) {
      const selection = this.buildActionPayloadsFromEntries(this.getActionEntriesForItem(item, "external"));
      if (selection.items.length === 0) {
        this.showToast("warning", "Ignored", item.auto_select_reason || "No valid uploadable files in this folder");
        return;
      }
      if (selection.items.length <= 1 && !this.isSeasonalPackItem(item)) {
        const cat = this.getCategoryForItem(item);
        const overridden = { ...item, itype: this.resolveUploadItype(item.itype, cat) };
        this.openForceUploadMenu(overridden, event);
        return;
      }
      this.openForceUploadMenu({
        kind: "itemList",
        items: selection.items,
        label: item.name || "selected folder"
      }, event);
    },
    // ============================================================
    //  Mark as Uploaded
    // ============================================================
    toggleMarkAll() {
      const val = this.markAllIndexers;
      const sel = {};
      this.activeIndexers.forEach((idx) => {
        sel[idx.id] = val;
      });
      this.markIndexerSelection = sel;
    },
    async markSingleUploaded(item) {
      const entries = item && item.key && item.key.startsWith("ext:") ? this.getActionEntriesForItem(item, "external") : this.getActionEntriesForItem(item, this.getCategoryForItem(item));
      const keys = entries.map((entry) => entry?.item?.key).filter(Boolean);
      if (keys.length === 0) {
        this.showToast("warning", "Warning", "No visible items are available for marking");
        return;
      }
      const ok = await this.confirmDialog(`Mark "${item.name}" as uploaded to all indexers?`, {
        title: "Mark As Uploaded",
        detail: `${keys.length} item(s) will be recorded as uploaded without actually uploading them.`,
        confirmLabel: "Mark Uploaded"
      });
      if (!ok) return;
      const indexerIds = this.activeIndexers.map((idx) => idx.id);
      try {
        await this.apiPost("/api/pending/mark-uploaded", {
          item_keys: keys,
          indexer_ids: indexerIds
        });
        this.showToast("success", "Marked", `${keys.length} item(s) marked as uploaded`);
        await this.loadPending(true);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to mark as uploaded");
      }
    },
    async confirmMarkUploaded() {
      const selectedIds = Object.entries(this.markIndexerSelection).filter(([, v2]) => v2).map(([k2]) => k2);
      if (selectedIds.length === 0) {
        this.showToast("warning", "Warning", "Select at least one indexer");
        return;
      }
      const keys = this.getSelectedActionEntries().map((entry) => entry?.item?.key).filter(Boolean);
      if (keys.length === 0) {
        this.showToast("warning", "Warning", "No visible selected items are available");
        return;
      }
      try {
        const res = await this.apiPost("/api/pending/mark-uploaded", {
          item_keys: keys,
          indexer_ids: selectedIds
        });
        this.showToast("success", "Marked", `${res.records_created} record(s) created`);
        this.showMarkModal = false;
        this.selectedItems = /* @__PURE__ */ new Set();
        this.selectedMeta = /* @__PURE__ */ new Map();
        await this.loadPending(true);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to mark as uploaded");
      }
    },
    // ============================================================
    //  Force Upload Flyout
    // ============================================================
    openCategoryFilter(event) {
      const rect = (event.target.closest("button") || event.target).getBoundingClientRect();
      this.categoryFilterPos = { x: rect.left, y: rect.bottom + 4 };
      this.categoryFilterOpen = !this.categoryFilterOpen;
      this.bulkSelectOpen = false;
      this.filterModeOpen = false;
    },
    openFilterMode(event) {
      const rect = (event.target.closest("button") || event.target).getBoundingClientRect();
      this.filterModePos = { x: rect.left, y: rect.bottom + 4 };
      this.filterModeOpen = !this.filterModeOpen;
      this.bulkSelectOpen = false;
      this.categoryFilterOpen = false;
    },
    openBulkSelect(event) {
      const rect = (event.target.closest("button") || event.target).getBoundingClientRect();
      this.bulkSelectPos = { x: rect.left, y: rect.bottom + 4 };
      this.bulkSelectOpen = !this.bulkSelectOpen;
      this.categoryFilterOpen = false;
      this.filterModeOpen = false;
    },
    _normalizeBulkSelectCategories() {
      const values = Array.isArray(this.bulkSelectCategoriesSelected) ? Array.from(new Set(this.bulkSelectCategoriesSelected.filter(Boolean))) : [];
      if (values.includes("all")) {
        this.bulkSelectCategoriesSelected = ["all"];
        return;
      }
      const valid = new Set(this.bulkSelectCategories.map((cat) => cat.value));
      this.bulkSelectCategoriesSelected = values.filter((v) => valid.has(v));
    },
    _bulkSelectMatchesCategory(item, value, sectionCategory = null) {
      if (!item) return false;
      if (value === "all") return true;
      if (typeof value === "string" && value.startsWith("itype:")) {
        return (item.itype || "") === value.slice(6);
      }
      const resolvedCategory = this.getCategoryForItem(item);
      if (resolvedCategory && resolvedCategory === value) return true;
      if (!resolvedCategory && sectionCategory && sectionCategory === value) return true;
      const detectedCategory = (item.detected_category || "").toString().toLowerCase();
      return detectedCategory === value;
    },
    collectBulkSelectableEntries(valueOrValues) {
      const entries = [];
      const seen = new Set();
      const values = Array.isArray(valueOrValues) ? valueOrValues : [valueOrValues];
      const normalizedValues = Array.from(new Set(values.filter(Boolean)));
      if (normalizedValues.length === 0) return entries;
      const pushItem = (item, sectionCategory = null) => {
        if (!item || !item.key || seen.has(item.key)) return;
        if (!this.isAutoSelectable(item) || this.isItemCompleted(item)) return;
        if (!normalizedValues.some((value) => this._bulkSelectMatchesCategory(item, value, sectionCategory))) return;
        seen.add(item.key);
        entries.push({ item, categoryOverride: sectionCategory && sectionCategory !== "external" ? sectionCategory : null });
      };
      for (const cat of this.flatCategories) {
        const list = this.items[cat.id] || [];
        for (const item of list) {
          pushItem(item, cat.id);
        }
      }
      for (const group of this.items.external || []) {
        for (const item of group.items || []) {
          const walk = (node) => {
            if (!node) return;
            pushItem(node, null);
            (node.children || []).forEach(walk);
          };
          walk(item);
        }
      }
      return entries;
    },
    selectAllBulkSelectCategories() {
      const values = this.bulkSelectCategories.map((cat) => cat.value);
      this.bulkSelectCategoriesSelected = values.includes("all") ? ["all"] : values;
      this._normalizeBulkSelectCategories();
    },
    clearBulkSelectSelection() {
      this.bulkSelectCategoriesSelected = [];
    },
    isBulkSelectCategoryChecked(value) {
      return this.bulkSelectSelectedSet.has(value);
    },
    toggleBulkSelectCategory(value, checked) {
      const next = new Set((this.bulkSelectCategoriesSelected || []).filter((v) => v !== "all"));
      if (value === "all") {
        this.bulkSelectCategoriesSelected = checked ? ["all"] : [];
        this._normalizeBulkSelectCategories();
        return;
      }
      if (checked) next.add(value);
      else next.delete(value);
      this.bulkSelectCategoriesSelected = Array.from(next);
      this._normalizeBulkSelectCategories();
    },
    applyBulkSelectSelection(action = "select") {
      const values = this.normalizedBulkSelectCategories;
      if (values.length === 0) {
        this.showToast("warning", "No Categories", "Select one or more categories first");
        this.bulkSelectOpen = false;
        return;
      }
      const entries = this.collectBulkSelectableEntries(values);
      if (entries.length === 0) {
        this.showToast("warning", "No Items", "No uploadable items were found for the selected categories");
        this.bulkSelectOpen = false;
        return;
      }
      const nextSet = /* @__PURE__ */ new Set();
      const nextMeta = /* @__PURE__ */ new Map();
      for (const entry of entries) {
        const key = entry.item.key;
        nextSet.add(key);
        nextMeta.set(key, this._buildSelectionMeta(entry.item, entry.categoryOverride));
      }
      this.selectedItems = nextSet;
      this.selectedMeta = nextMeta;
      this.bulkSelectOpen = false;
      const label = values.includes("all") ? "All Uploadable" : values.length === 1 ? this.bulkSelectCategories.find((cat) => cat.value === values[0])?.label || values[0] : `${values.length} categories`;
      if (action === "stage") {
        this.showToast("success", "Selected", `${entries.length} uploadable item(s) selected from ${label}`);
        void this.$nextTick(() => this.addToQueue());
        return;
      }
      if (action === "force") {
        this.showToast("success", "Selected", `${entries.length} uploadable item(s) selected from ${label}`);
        void this.$nextTick(() => this.openForceUploadMenu("bulk", { target: this.$refs.bulkSelectButton || null }));
        return;
      }
      this.showToast("success", "Selected", `${entries.length} uploadable item(s) selected from ${label}`);
    },
    async openForceUploadMenu(target, event) {
      const btn = event.target.closest("button") || event.target;
      this.flyoutTarget = target;
      this.flyoutOpen = true;
      await this.$nextTick();
      const flyoutEl = this.$refs.forceUploadFlyout;
      if (flyoutEl && btn) {
        const { x: x2, y: y2 } = await this.positionFloatingEl(btn, flyoutEl, {
          placement: "bottom-start",
          offset: 4,
          padding: 8
        });
        this.flyoutPos = { x: x2, y: y2 };
      } else {
        const rect = btn.getBoundingClientRect();
        this.flyoutPos = {
          x: Math.min(rect.left, window.innerWidth - 220),
          y: rect.bottom + 4
        };
      }
    },
    closeForceUploadMenu() {
      this.flyoutOpen = false;
      this.flyoutTarget = null;
      this.forceSkipDupeCheck = false;
    },
    executeForceUpload(indexerId) {
      const target = this.flyoutTarget;
      const skipDupeCheck = this.forceSkipDupeCheck;
      this.closeForceUploadMenu();
      if (target === "bulk") {
        this._doForceUploadBulk(indexerId, skipDupeCheck);
      } else if (target && target.kind === "itemList") {
        this._doForceUploadItemList(
          target.items || [],
          indexerId,
          target.label || "selected items",
          skipDupeCheck
        );
      } else if (target) {
        this._doForceUploadSingle(target, indexerId, skipDupeCheck);
      }
    },
    async _doForceUploadSingle(item, indexerId, skipDupeCheck = false) {
      if (this.isSelectionIgnored(item)) {
        this.showToast("warning", "Ignored", item.auto_select_reason || "This item is ignored for upload");
        return;
      }
      const cat = this.getCategoryForItem(item);
      if (!cat || cat === "external") {
        this.showToast("warning", "Category Required", "Select a valid category before uploading");
        return;
      }
      const idxName = indexerId ? this.activeIndexers.find((i2) => i2.id === indexerId)?.name || indexerId : "all indexers";
      try {
        const res = await this.apiPost("/api/pending/force-upload", {
          items: [{
            path: item.path,
            category: cat,
            itype: this.resolveUploadItype(item.itype, cat),
            name: item.name || (item.path || item.key).replace(/\\/g, "/").split("/").pop() || item.key,
            detected_category: item.detected_category || cat,
            detection_method: item.detection_method || "",
            selection_reason: item.auto_select_reason || ""
          }],
          enable_duplicate_check: !skipDupeCheck,
          indexer_id: indexerId || null
        });
        const jobIds = res.job_ids || [];
        const stopActions = jobIds.map((jid) => ({
          label: "Stop Job",
          icon: "square",
          callback: () => {
            this.apiPost(`/api/uploads/jobs/${jid}/stop`).then(() => {
              this.showToast("info", "Stopped", "Job stop signal sent");
            }).catch(() => {
            });
          }
        }));
        this.showToast("success", "Upload Started", `Job started for "${item.name}" \u2192 ${idxName}`, 15e3, stopActions);
        this.startTimeout(() => this.loadJobs(), 500);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to start upload");
      }
    },
    async _doForceUploadBulk(indexerId, skipDupeCheck = false) {
      if (this.selectedItems.size === 0) return;
      const selection = this.buildSelectedActionPayloads();
      const items = selection.items;
      const uncategorizedExternal = selection.uncategorizedExternal;
      console.info(
        `[QUEUE UI] force-upload selection visible=${selection.visibleSelectableCount} selected=${selection.selectedRawCount} queued=${items.length} excluded=${selection.excludedCount}`
      );
      if (uncategorizedExternal > 0) {
        this.showToast("warning", "Skipped", `${uncategorizedExternal} external item(s) skipped \u2014 select a category first`);
      }
      if (items.length === 0) return;
      const idxName = indexerId ? this.activeIndexers.find((i2) => i2.id === indexerId)?.name || indexerId : "all indexers";
      const request = {
        items,
        enable_duplicate_check: !skipDupeCheck,
        indexer_id: indexerId || null,
        bulk_selection: true
      };
      try {
        const res = await this.apiPost("/api/pending/force-upload", request);
        const jobIds = res.job_ids || [];
        const stopActions = jobIds.map((jid) => ({
          label: "Stop Job",
          icon: "square",
          callback: () => this.apiPost(`/api/uploads/jobs/${jid}/stop`).catch(() => {})
        }));
        this.showToast(
          "success",
          "Upload Started",
          `${jobIds.length} job(s) started for ${res.items_count || items.length} item(s) → ${idxName}`,
          15e3,
          stopActions
        );
        this.selectedItems = new Set();
        this.selectedMeta = new Map();
        this.startTimeout(() => this.loadJobs(), 500);
      } catch (e2) {
        this.showToast("error", "Error", e2?.message || "Failed to start upload");
      }
    },
    closeBulkPreviewModal() {
      if (this.bulkPreviewStarting) return;
      this.showBulkPreviewModal = false;
      this.bulkPreview = null;
      this.bulkPreviewRequest = null;
      this.bulkPreviewMode = "bulk";
      this.bulkPreviewIndexerName = "";
      this.bulkPreviewLoading = false;
    },
    async confirmBulkPreviewUpload() {
      if (!this.bulkPreviewRequest || !this.bulkPreviewCanStart || this.bulkPreviewStarting) return;
      this.bulkPreviewStarting = true;
      try {
        const endpoint = this.bulkPreviewMode === "staging" ? "/api/uploads/queue/start" : "/api/pending/force-upload";
        const res = await this.apiPost(endpoint, this.bulkPreviewRequest);
        const jobIds = res.job_ids || [];
        const stopActions = jobIds.map((jid) => ({
          label: "Stop Job",
          icon: "square",
          callback: () => {
            this.apiPost(`/api/uploads/jobs/${jid}/stop`).then(() => {
              this.showToast("info", "Stopped", "Job stop signal sent");
            }).catch(() => {
            });
          }
        }));
        if (this.bulkPreviewMode === "staging") {
          const startedItems = res.started_items || 0;
          const skippedItems = res.skipped_items || 0;
          const holdMsg = this.queueControl && this.queueControl.paused ? " Queue processing is paused, so the job will wait until resumed." : " Processing will begin automatically.";
          const skippedMsg = skippedItems > 0 ? ` ${skippedItems} staged item(s) were skipped and left in staging.` : "";
          this.showToast("success", "Job Created", `${startedItems} staged item(s) queued.${skippedMsg}${holdMsg}`, 15e3, stopActions);
          this.loadQueueItems();
          this.loadQueuedPaths();
        } else {
          this.showToast(
            "success",
            "Upload Started",
            `${jobIds.length} job(s) started for ${res.items_count} planned item(s) \u2192 ${this.bulkPreviewIndexerName}`,
            15e3,
            stopActions
          );
          this.selectedItems = /* @__PURE__ */ new Set();
          this.selectedMeta = /* @__PURE__ */ new Map();
        }
        this.showBulkPreviewModal = false;
        this.bulkPreview = null;
        this.bulkPreviewRequest = null;
        this.bulkPreviewMode = "bulk";
        this.bulkPreviewIndexerName = "";
        this.startTimeout(() => this.loadJobs(), 500);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to start upload");
      } finally {
        this.bulkPreviewStarting = false;
      }
    },
    async _doForceUploadItemList(rawItems, indexerId, label, skipDupeCheck = false) {
      if (!Array.isArray(rawItems) || rawItems.length === 0) return;
      const items = rawItems.map((item) => ({
        path: item.path,
        category: this.getCategoryForItem(item) || item.category,
        manual_category: item.key ? this.manualExternalCategories[item.key] || "" : "",
        itype: this.resolveUploadItype(item.itype, this.getCategoryForItem(item) || item.category),
        name: item.name || (item.path || "").replace(/\\/g, "/").split("/").pop() || "",
        detected_category: item.detected_category || this.getCategoryForItem(item) || item.category,
        detection_method: item.detection_method || "",
        selection_reason: item.auto_select_reason || ""
      })).filter((item) => !!item.path && !!item.category && item.category !== "external");
      if (items.length === 0) {
        this.showToast("warning", "Category Required", "No actionable items with a valid category were found");
        return;
      }
      const idxName = indexerId ? this.activeIndexers.find((i2) => i2.id === indexerId)?.name || indexerId : "all indexers";
      try {
        const res = await this.apiPost("/api/pending/force-upload", {
          items,
          enable_duplicate_check: !skipDupeCheck,
          indexer_id: indexerId || null
        });
        const jobIds = res.job_ids || [];
        const stopActions = jobIds.map((jid) => ({
          label: "Stop Job",
          icon: "square",
          callback: () => {
            this.apiPost(`/api/uploads/jobs/${jid}/stop`).then(() => {
              this.showToast("info", "Stopped", "Job stop signal sent");
            }).catch(() => {
            });
          }
        }));
        this.showToast(
          "success",
          "Upload Started",
          `${jobIds.length} job(s) started for ${items.length} item(s) from "${label}" \u2192 ${idxName}`,
          15e3,
          stopActions
        );
        this.startTimeout(() => this.loadJobs(), 500);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to start upload");
      }
    },
    // ============================================================
    //  Add Single Item to Queue
    // ============================================================
    async queueSingleItem(item, category) {
      if (this.isSelectionIgnored(item)) {
        this.showToast("warning", "Ignored", item.auto_select_reason || "This item is ignored for upload");
        return;
      }
      if (category === "external" && item && item.is_dir) {
        const selection = this.buildActionPayloadsFromEntries(this.getActionEntriesForItem(item, "external"));
        if (selection.uncategorizedExternal > 0) {
          this.showToast("warning", "Skipped", `${selection.uncategorizedExternal} item(s) need a category first`);
        }
        if (selection.items.length === 0) {
          this.showToast("warning", "No Valid Items", "No visible items from this folder are available to stage");
          return;
        }
        try {
          const res = await this.apiPost("/api/uploads/queue/items", { items: selection.items });
          if (res.added > 0) {
            this.showToast(
              "success",
              "Staged",
              `${res.added} visible item(s) from "${item.name}" added to staging`
            );
            this.loadQueuedPaths();
            this.loadQueueItems();
          } else {
            this.showToast("info", "Already Staged", `Visible items from "${item.name}" are already in staging`);
          }
        } catch (e2) {
          this.showToast("error", "Error", "Failed to add item to staging");
        }
        return;
      }
      let cat = category;
      if (cat === "external") {
        cat = this._resolveExtCategory(item.key);
      }
      if (!cat || cat === "external") {
        this.showToast("warning", "Category Required", "Select a category before queuing");
        return;
      }
      const payload = [{
        path: item.path,
        category: cat,
        itype: this.resolveUploadItype(item.itype, cat),
        name: item.name || (item.path || item.key).replace(/\\/g, "/").split("/").pop() || item.key,
        detected_category: item.detected_category || cat,
        detection_method: item.detection_method || "",
        selection_reason: item.auto_select_reason || ""
      }];
      try {
        const res = await this.apiPost("/api/uploads/queue/items", { items: payload });
        if (res.added > 0) {
          this.showToast("success", "Staged", `"${payload[0].name}" added to staging area`);
          this.loadQueuedPaths();
          this.loadQueueItems();
        } else {
          this.showToast("info", "Already Staged", `"${payload[0].name}" is already in staging`);
        }
      } catch (e2) {
        this.showToast("error", "Error", "Failed to add item to staging");
      }
    },
    // ============================================================
    //  Add to Queue (bulk)
    // ============================================================
    async addToQueue() {
      if (this.selectedItems.size === 0) return;
      const selection = this.buildSelectedActionPayloads();
      const items = selection.items.slice();
      const uncategorizedExternal = selection.uncategorizedExternal;
      console.info(
        `[QUEUE UI] staging selection visible=${selection.visibleSelectableCount} selected=${selection.selectedRawCount} to_stage=${items.length} excluded=${selection.excludedCount}`
      );
      if (uncategorizedExternal > 0) {
        this.showToast("warning", "Skipped", `${uncategorizedExternal} external item(s) skipped \u2014 select a category first`);
      }
      if (items.length === 0) {
        this.showToast("warning", "No Valid Items", "No visible selected items are available to stage");
        return;
      }
      items.sort((a2, b2) => {
        const getDirName = (path) => {
          const parts = path.split("/");
          const categoryIndex = parts.findIndex((p2) => p2.startsWith("0--"));
          return categoryIndex >= 0 ? parts.slice(categoryIndex + 1).join("/") : path;
        };
        return getDirName(a2.path).localeCompare(getDirName(b2.path));
      });
      try {
        const res = await this.apiPost("/api/uploads/queue/items", { items, bulk_selection: true });
        const added = res.added || 0;
        const total = res.total || 0;
        const skipped = items.length - added;
        if (added > 0) {
          this.showToast(
            "success",
            "Staged",
            `${added} item(s) added to staging` + (selection.excludedCount > 0 ? ` (${selection.excludedCount} excluded by filters)` : "") + (skipped > 0 ? ` (${skipped} already staged)` : "") + ` \u2014 ${total} total`
          );
        } else {
          this.showToast("info", "Already Staged", "All selected items are already in staging");
        }
        this.selectedItems = /* @__PURE__ */ new Set();
        this.selectedMeta = /* @__PURE__ */ new Map();
        this.loadQueuedPaths();
        this.loadQueueItems();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to add items to queue");
      }
    },
    // ============================================================
    //  Queue Item Loading & Management
    // ============================================================
    async loadQueueItems() {
      if (this._loadQueueItemsPromise) return this._loadQueueItemsPromise;
      this._loadQueueItemsPromise = (async () => {
        try {
          const data = await this.apiFetch("/api/uploads/queue/items");
          this.queueItems = data.items || [];
          this.loadQueuedPaths();
          this.$nextTick(() => this.initSortable());
        } catch (e2) {
          if (!e2.isOffline) {
            this.showToast("error", "Error", "Failed to load staging items");
          }
        } finally {
          this._loadQueueItemsPromise = null;
        }
      })();
      return this._loadQueueItemsPromise;
    },
    async loadJobs() {
      if (this._loadJobsPromise) return this._loadJobsPromise;
      this._loadJobsPromise = (async () => {
        try {
          const data = await this.apiFetch("/api/uploads/queue");
          this.running = data.running || [];
          this.queued = data.queued || [];
          this.finished = data.finished || [];
          this.counts = data.counts || { running: 0, queued: 0, finished: 0 };
          this.queueControl = data.control || { paused: false, active: null };
          this.jobQueueControlJobId = resolvePreferredJobId(this.jobQueueControlJobId, this.running, this.queued);
          maybeRevalidateQueuedJobs(this);
          syncActiveJobModal(this);
          syncQueuedJobModal(this);
        } catch (e2) {
          if (!e2.isOffline) {
            this.showToast("error", "Error", "Failed to load jobs");
          }
        } finally {
          this._loadJobsPromise = null;
        }
      })();
      return this._loadJobsPromise;
    },
    async revalidateQueuedJobs() {
      try {
        const res = await this.apiPost("/api/uploads/queue/revalidate", { include_paused: true });
        const updated = Number((res == null ? void 0 : res.updated) || 0);
        const cancelled = Number((res == null ? void 0 : res.cancelled) || 0);
        const inspected = Number((res == null ? void 0 : res.inspected) || 0);
        const message = inspected > 0 ? `${inspected} job(s) checked, ${updated} updated, ${cancelled} cancelled` : "No queued jobs needed revalidation";
        this.showToast("success", "Queue Revalidated", message);
        await this.loadJobs();
      } catch (e2) {
        if (!e2.isOffline) {
          this._pendingQueuedJobRevalidationDone = false;
          this.showToast("error", "Queue Revalidation Failed", (e2 == null ? void 0 : e2.message) || "Unable to re-scan queued jobs");
        }
      }
    },
    jobHasExplicitPaths(job) {
      if (!job) return false;
      if (job.has_explicit_paths === true) return true;
      return Array.isArray(job.target_paths) && job.target_paths.length > 0;
    },
    async openActiveJobModal(job) {
      if (!job) return;
      this.activeJobModalJob = job;
      this.activeJobModalItems = [];
      this.activeJobModalSearch = "";
      this.showActiveJobModal = true;
      await this.loadActiveJobModalItems(job.job_id);
    },
    async loadActiveJobModalItems(jobId, silent = false) {
      if (this._loadActiveJobItemsPromise) {
        if (this._activeJobItemsRequestJobId === jobId) return this._loadActiveJobItemsPromise;
        await this._loadActiveJobItemsPromise;
      }
      if (!silent) this.activeJobModalLoading = true;
      this._activeJobItemsRequestJobId = jobId;
      this._loadActiveJobItemsPromise = (async () => {
        try {
          const res = await this.apiFetch(`/api/uploads/queue/${jobId}/active-items`);
          if (this.activeJobModalJob && this.activeJobModalJob.job_id === jobId) {
            this.activeJobModalItems = res.items || [];
            this.$nextTick(() => this.initActiveJobModalSortable());
          }
        } catch (e2) {
          if (!e2.isOffline && !silent) {
            this.showToast("error", "Error", "Failed to load active job items");
          }
        } finally {
          this.activeJobModalLoading = false;
          this._loadActiveJobItemsPromise = null;
          this._activeJobItemsRequestJobId = null;
        }
      })();
      return this._loadActiveJobItemsPromise;
    },
    closeActiveJobModal() {
      this.destroyActiveJobModalSortable();
      this.showActiveJobModal = false;
      this.activeJobModalJob = null;
      this.activeJobModalItems = [];
      this.activeJobModalSearch = "";
      this.activeJobModalLoading = false;
      this.activeJobModalSaving = false;
    },
    async openCompletedJobModal(job) {
      this.completedJobModalJob = job;
      this.completedJobModalItems = [];
      this.completedJobModalSearch = "";
      this.completedJobModalLoading = true;
      try {
        const response = await this.apiFetch(`/api/uploads/queue/${job.job_id}/completed-items`);
        if (this.completedJobModalJob && this.completedJobModalJob.job_id === job.job_id) {
          this.completedJobModalItems = response.items || [];
        }
      } catch (error) {
        if (!error.isOffline) {
          this.showToast("error", "Error", "Failed to load completed job items");
        }
      } finally {
        this.completedJobModalLoading = false;
      }
    },
    closeCompletedJobModal() {
      this.completedJobModalJob = null;
      this.completedJobModalItems = [];
      this.completedJobModalSearch = "";
      this.completedJobModalLoading = false;
    },
    activeJobHasInspectableItems(job) {
      return this.jobHasExplicitPaths(job) || !!job?.current_item;
    },
    isJobQueueActiveEntry(job) {
      return !!job && job._queueEntryType === "active";
    },
    openJobQueueEntry(job) {
      if (!job) return;
      this.jobQueueControlJobId = job.job_id;
      if (this.isJobQueueActiveEntry(job)) {
        if (this.activeJobHasInspectableItems(job)) {
          this.openActiveJobModal(job);
        }
        return;
      }
      this.openQueuedJobModal(job);
    },
    editJobQueueEntry(job) {
      if (!job) return;
      if (this.isJobQueueActiveEntry(job)) {
        if (this.activeJobHasInspectableItems(job)) {
          this.openActiveJobModal(job);
        }
        return;
      }
      this.openQueuedJobModal(job);
    },
    jobQueueStatusLabel(job) {
      if (!job) return "Queued";
      if (this.isJobStopping(job)) return "Stopping";
      if (this.isJobPausing(job)) return "Pausing";
      if (this.isJobPaused(job)) return "Paused";
      if (job.status === "stopped") return "Stopped";
      if (this.isJobQueueActiveEntry(job)) return "Running";
      return "Queued";
    },
    jobQueueStatusBadgeClass(job) {
      if (!job) return "bg-notion-bg-hover text-notion-text-secondary";
      if (this.isJobStopping(job)) return "bg-notion-error/15 text-notion-error";
      if (this.isJobPaused(job)) return "bg-notion-warning/15 text-notion-warning";
      if (job.status === "stopped") return "bg-notion-warning/15 text-notion-warning";
      if (this.isJobQueueActiveEntry(job)) return "bg-notion-accent/10 text-notion-accent";
      return "bg-notion-bg-hover text-notion-text-secondary";
    },
    jobQueuePromoteTitle() {
      return this.running.length > 0 ? "Move to run next after the current job finishes" : "Move to front of queue";
    },
    jobQueueControlOptionLabel(job) {
      if (!job) return "Job";
      const prefix = this.isJobQueueActiveEntry(job) ? "Now" : `#${job._queuePosition}`;
      return `${prefix} - ${this.jobDisplayName(job)}`;
    },
    destroyActiveJobModalSortable() {
      this.destroySortableInstance("_activeJobModalSortable");
    },
    initActiveJobModalSortable() {
      this.destroyActiveJobModalSortable();
      if (!this.showActiveJobModal || this.activeJobModalSaving) return;
      if ((this.activeJobModalSearch || "").trim()) return;
      if (!this.activeJobModalCanReorder) return;
      if (this.activeJobModalItems.length < 2) return;
      const container = this.$refs.activeJobModalSortableContainer;
      if (!container) return;
      this.createSortableInstance("_activeJobModalSortable", container, {
        handle: ".active-job-drag-handle",
        draggable: ".active-job-row",
        animation: 180,
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        dragClass: "sortable-drag",
        onEnd: async () => {
          const rowEls = container.querySelectorAll(".active-job-row[data-path]");
          const byPath = new Map(this.activeJobModalItems.map((item) => [item.path, item]));
          const reordered = [];
          rowEls.forEach((el2) => {
            const path = el2.dataset.path;
            if (path && byPath.has(path)) reordered.push(byPath.get(path));
          });
          if (reordered.length !== this.activeJobModalItems.length) return;
          this.activeJobModalItems = reordered.map((item, i2) => ({ ...item, index: i2 + 1 }));
          await this.persistActiveJobModalOrder(false);
        }
      });
    },
    async persistActiveJobModalOrder(showSuccessToast = true) {
      if (!this.activeJobModalJob) return;
      if (!this.activeJobModalCanReorder) return;
      const paths = this.activeJobModalItems.map((item) => item.path);
      this.activeJobModalSaving = true;
      try {
        await this.apiPut(`/api/uploads/queue/${this.activeJobModalJob.job_id}/active-items/reorder`, { paths });
        if (showSuccessToast) {
          this.showToast("success", "Saved", "Active job order updated");
        }
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to save active job order");
        await this.loadJobs();
      } finally {
        this.activeJobModalSaving = false;
        this.$nextTick(() => this.initActiveJobModalSortable());
      }
    },
    async removeActiveJobModalItem(path) {
      if (!this.activeJobModalJob || !path || this.activeJobModalSaving) return;
      const prevItems = [...this.activeJobModalItems];
      const normalized = String(path || "").trim();
      if (!normalized) return;
      this.activeJobModalItems = this.activeJobModalItems.filter((item) => item.path !== normalized).map((item, i2) => ({ ...item, index: i2 + 1 }));
      this.activeJobModalSaving = true;
      this.$nextTick(() => this.initActiveJobModalSortable());
      try {
        await this.apiFetch(`/api/uploads/queue/${this.activeJobModalJob.job_id}/active-items`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: normalized })
        });
        this.showToast("success", "Saved", "Active job item removed");
        await this.loadJobs();
      } catch (e2) {
        this.activeJobModalItems = prevItems;
        this.$nextTick(() => this.initActiveJobModalSortable());
        this.showToast("error", "Error", "Failed to remove item from active job");
      } finally {
        this.activeJobModalSaving = false;
        this.$nextTick(() => this.initActiveJobModalSortable());
      }
    },
    async openQueuedJobModal(job) {
      this.showQueuedJobModal = true;
      this.queuedJobModalJob = job;
      this.queuedJobModalSearch = "";
      this.queuedJobModalName = job.display_name || "";
      this.hydrateQueuedJobSchedule(job.run_after || null);
      await this.loadQueuedJobModalItems(job.job_id);
      this.$nextTick(() => this.initQueuedJobModalSortable());
    },
    closeQueuedJobModal() {
      this.destroyQueuedJobModalSortable();
      this.showQueuedJobModal = false;
      this.queuedJobModalJob = null;
      this.queuedJobModalItems = [];
      this.queuedJobModalSearch = "";
      this.queuedJobModalName = "";
      this.queuedJobModalDate = "";
      this.queuedJobModalHasTime = false;
      this.queuedJobModalTime = "12:00";
      this.queuedJobModalLoading = false;
      this.queuedJobModalSaving = false;
      this.queuedJobModalRenaming = false;
      this.queuedJobModalScheduling = false;
    },
    hydrateQueuedJobSchedule(runAfter) {
      if (!runAfter) {
        this.queuedJobModalDate = "";
        this.queuedJobModalHasTime = false;
        this.queuedJobModalTime = "12:00";
        return;
      }
      const dt2 = new Date(runAfter);
      if (Number.isNaN(dt2.getTime())) {
        this.queuedJobModalDate = "";
        this.queuedJobModalHasTime = false;
        this.queuedJobModalTime = "12:00";
        return;
      }
      const yyyy = dt2.getFullYear();
      const mm = String(dt2.getMonth() + 1).padStart(2, "0");
      const dd2 = String(dt2.getDate()).padStart(2, "0");
      const hh = String(dt2.getHours()).padStart(2, "0");
      const min2 = String(dt2.getMinutes()).padStart(2, "0");
      this.queuedJobModalDate = `${yyyy}-${mm}-${dd2}`;
      this.queuedJobModalTime = `${hh}:${min2}`;
      this.queuedJobModalHasTime = !(hh === "12" && min2 === "00");
    },
    buildQueuedJobRunAfterIso() {
      const date = (this.queuedJobModalDate || "").trim();
      if (!date) return null;
      const candidateTime = (this.queuedJobModalTime || "").trim();
      const time = this.queuedJobModalHasTime && candidateTime ? candidateTime : "12:00";
      const parsed = /* @__PURE__ */ new Date(`${date}T${time}:00`);
      if (Number.isNaN(parsed.getTime())) {
        return null;
      }
      return parsed.toISOString();
    },
    scheduleEpoch(runAfter) {
      if (!runAfter) return null;
      const dt2 = new Date(runAfter);
      if (Number.isNaN(dt2.getTime())) return null;
      return dt2.getTime();
    },
    formatRunAfterLocal(runAfter) {
      if (!runAfter) return "";
      const dt2 = new Date(runAfter);
      if (Number.isNaN(dt2.getTime())) return "Scheduled";
      return dt2.toLocaleString([], {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit"
      });
    },
    async saveQueuedJobSchedule() {
      if (!this.queuedJobModalJob || !this.queuedJobModalScheduleDirty) return;
      if (this.queuedJobModalDate && !this.buildQueuedJobRunAfterIso()) {
        this.showToast("error", "Invalid date/time", "Please choose a valid date and optional time.");
        return;
      }
      this.queuedJobModalScheduling = true;
      const runAfter = this.buildQueuedJobRunAfterIso();
      try {
        const res = await this.apiFetch(`/api/uploads/queue/${this.queuedJobModalJob.job_id}/schedule`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_after: runAfter || null })
        });
        const updatedRunAfter = res && res.run_after ? res.run_after : null;
        this.queuedJobModalJob = {
          ...this.queuedJobModalJob,
          run_after: updatedRunAfter
        };
        this.hydrateQueuedJobSchedule(updatedRunAfter);
        await this.loadJobs();
        this.showToast("success", "Saved", updatedRunAfter ? "Deferred schedule updated" : "Deferred schedule cleared");
      } catch (e2) {
        const detail = e2?.message || "Failed to update deferred schedule";
        this.showToast("error", "Error", detail);
      } finally {
        this.queuedJobModalScheduling = false;
      }
    },
    async clearQueuedJobSchedule() {
      if (!this.queuedJobModalJob) return;
      this.queuedJobModalDate = "";
      this.queuedJobModalHasTime = false;
      this.queuedJobModalTime = "12:00";
      await this.saveQueuedJobSchedule();
    },
    async saveQueuedJobName() {
      if (!this.queuedJobModalJob || !this.queuedJobModalNameDirty) return;
      this.queuedJobModalRenaming = true;
      const name = (this.queuedJobModalName || "").trim();
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${this.queuedJobModalJob.job_id}/name`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name || null })
        });
        const updatedName = res && res.display_name ? res.display_name : "";
        this.queuedJobModalName = updatedName;
        this.queuedJobModalJob = {
          ...this.queuedJobModalJob,
          display_name: updatedName || null
        };
        await this.loadJobs();
        this.showToast("success", "Saved", updatedName ? "Job name updated" : "Custom job name cleared");
      } catch (e2) {
        this.showToast("error", "Error", "Failed to update job name");
      } finally {
        this.queuedJobModalRenaming = false;
      }
    },
    async clearQueuedJobName() {
      if (!this.queuedJobModalJob) return;
      this.queuedJobModalName = "";
      await this.saveQueuedJobName();
    },
    async loadQueuedJobModalItems(jobId) {
      if (this._queuedJobLoadTimer) {
        clearTimeout(this._queuedJobLoadTimer);
      }
      const cached = this._queuedJobItemsCache[jobId];
      if (cached && Date.now() - cached.ts < 3e5) {
        this.queuedJobModalItems = cached.items;
        return;
      }
      this._queuedJobLoadTimer = setTimeout(async () => {
        this.queuedJobModalLoading = true;
        try {
          const data = await this.apiFetch(`/api/uploads/queue/${jobId}/items`);
          const items = data.items || [];
          this.queuedJobModalItems = items;
          this._queuedJobItemsCache[jobId] = { items, ts: Date.now() };
        } catch (e2) {
          this.showToast("error", "Error", "Failed to load queued job items");
          this.closeQueuedJobModal();
        } finally {
          this.queuedJobModalLoading = false;
          this.$nextTick(() => this.initQueuedJobModalSortable());
        }
      }, 200);
    },
    destroyQueuedJobModalSortable() {
      this.destroySortableInstance("_queuedJobModalSortable");
    },
    initQueuedJobModalSortable() {
      this.destroyQueuedJobModalSortable();
      if (!this.showQueuedJobModal || this.queuedJobModalLoading) return;
      if ((this.queuedJobModalSearch || "").trim()) return;
      if (this.queuedJobModalItems.length < 2) return;
      const container = this.$refs.queuedJobModalSortableContainer;
      if (!container) return;
      this.createSortableInstance("_queuedJobModalSortable", container, {
        handle: ".queued-job-drag-handle",
        draggable: ".queued-job-row",
        animation: 180,
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        dragClass: "sortable-drag",
        onEnd: async () => {
          const rowEls = container.querySelectorAll(".queued-job-row[data-path]");
          const byPath = new Map(this.queuedJobModalItems.map((item) => [item.path, item]));
          const reordered = [];
          rowEls.forEach((el2) => {
            const path = el2.dataset.path;
            if (path && byPath.has(path)) reordered.push(byPath.get(path));
          });
          if (reordered.length !== this.queuedJobModalItems.length) return;
          this.queuedJobModalItems = reordered.map((item, i2) => ({ ...item, index: i2 + 1 }));
          await this.persistQueuedJobModalOrder(false);
        }
      });
    },
    async persistQueuedJobModalOrder(showSuccessToast = true) {
      if (!this.queuedJobModalJob) return;
      const paths = this.queuedJobModalItems.map((item) => item.path);
      this.queuedJobModalSaving = true;
      try {
        await this.apiPut(`/api/uploads/queue/${this.queuedJobModalJob.job_id}/items/reorder`, { paths });
        if (showSuccessToast) {
          this.showToast("success", "Saved", "Queued job order updated");
        }
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to save queued job order");
        await this.loadQueuedJobModalItems(this.queuedJobModalJob.job_id);
      } finally {
        this.queuedJobModalSaving = false;
      }
    },
    async removeQueuedJobModalItem(path) {
      if (!this.queuedJobModalJob) return;
      const jobId = this.queuedJobModalJob.job_id;
      const prev = [...this.queuedJobModalItems];
      this.queuedJobModalItems = this.queuedJobModalItems.filter((item) => item.path !== path).map((item, i2) => ({ ...item, index: i2 + 1 }));
      this.$nextTick(() => this.initQueuedJobModalSortable());
      try {
        await this.apiFetch(`/api/uploads/queue/${jobId}/items`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path })
        });
        await this.loadJobs();
        if (this.queuedJobModalItems.length === 0) {
          this.closeQueuedJobModal();
        }
      } catch (e2) {
        this.queuedJobModalItems = prev;
        this.$nextTick(() => this.initQueuedJobModalSortable());
        this.showToast("error", "Error", "Failed to remove item from queued job");
      }
    },
    // ============================================================
    //  SortableJS - Drag-to-Reorder
    // ============================================================
    initSortable() {
      this.destroySortableInstance("_sortable");
      const container = this.$refs.sortableContainer;
      if (!container || this.queueItems.length === 0) return;
      this.createSortableInstance("_sortable", container, {
        handle: ".drag-handle",
        draggable: ".queue-group",
        animation: 200,
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        dragClass: "sortable-drag",
        onEnd: () => {
          const groupEls = container.querySelectorAll(".queue-group");
          const newIds = [];
          groupEls.forEach((groupEl) => {
            groupEl.querySelectorAll(".queue-item[data-id]").forEach((el2) => {
              newIds.push(parseInt(el2.dataset.id));
            });
          });
          const map = {};
          this.queueItems.forEach((q2) => {
            map[q2.id] = q2;
          });
          this.queueItems = newIds.filter((id2) => map[id2]).map((id2) => map[id2]);
          this.persistOrder();
        }
      });
    },
    async persistOrder() {
      try {
        const item_ids = this.queueItems.map((qi2) => qi2.id);
        await this.apiPut("/api/uploads/queue/items/reorder", { item_ids });
      } catch (e2) {
        this.showToast("error", "Error", "Failed to save queue order");
        await this.loadQueueItems();
      }
    },
    // ============================================================
    //  Queue Item Actions
    // ============================================================
    async removeItem(itemId) {
      if (!this._deletingIds) this._deletingIds = /* @__PURE__ */ new Set();
      if (this._deletingIds.has(itemId)) return;
      this._deletingIds.add(itemId);
      const idx = this.queueItems.findIndex((qi2) => qi2.id === itemId);
      const removed = idx >= 0 ? this.queueItems.splice(idx, 1)[0] : null;
      try {
        await this.apiFetch(`/api/uploads/queue/items/${itemId}`, { method: "DELETE" });
        clearTimeout(this._queuedPathsTimer);
        this._queuedPathsTimer = setTimeout(() => this.loadQueuedPaths(), 1500);
      } catch (e2) {
        if (removed && idx >= 0) {
          this.queueItems.splice(idx, 0, removed);
        }
        this.showToast("error", "Error", "Failed to remove item");
      } finally {
        this._deletingIds.delete(itemId);
      }
    },
    async clearQueueItems() {
      const ok = await this.confirmDialog(`Remove all ${this.queueItems.length} staged item(s)?`, {
        title: "Clear Staging Area",
        detail: "This only clears the staging area. Existing jobs are NOT removed.",
        danger: true,
        confirmLabel: "Clear Staging"
      });
      if (!ok) return;
      try {
        const res = await this.apiFetch("/api/uploads/queue/items/clear", { method: "POST" });
        this.queueItems = [];
        this.showToast("success", "Staging Cleared", `${res.cleared} item(s) removed from staging`);
        this.loadQueuedPaths();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to clear staging area");
      }
    },
    async createJobsFromStaging() {
      if (this.queueItems.length === 0) return;
      this.startingQueue = true;
      try {
        const res = await this.apiPost("/api/uploads/queue/start", {});
        const jobCount = res.jobs_created || 0;
        const startedItems = res.started_items || 0;
        const skippedItems = res.skipped_items || 0;
        const holdMsg = this.queueControl && this.queueControl.paused ? " Queue processing is paused, so jobs will wait until resumed." : " Processing will begin automatically.";
        const skippedMsg = skippedItems > 0 ? ` ${skippedItems} staged item(s) were skipped and left in staging.` : "";
        this.showToast(
          "success",
          "Jobs Created",
          `${jobCount} job(s) created from ${startedItems} staged item(s).${skippedMsg}${holdMsg}`
        );
        this.startTimeout(() => this.loadJobs(), 500);
        this.loadQueueItems();
        this.loadQueuedPaths();
      } catch (e2) {
        const detail = e2?.message || "Failed to create jobs from staging";
        this.showToast("error", "Error", detail);
      } finally {
        this.startingQueue = false;
      }
    },
    async previewStagingQueue() {
      if (this.queueItems.length === 0 || this.bulkPreviewLoading) return;
      this.bulkPreview = null;
      this.bulkPreviewRequest = {};
      this.bulkPreviewMode = "staging";
      this.bulkPreviewIndexerName = "all enabled indexers";
      this.bulkPreviewLoading = true;
      this.showBulkPreviewModal = true;
      try {
        this.bulkPreview = await this.apiPost("/api/uploads/queue/preview", {});
      } catch (e2) {
        this.closeBulkPreviewModal();
        this.showToast("error", "Preview Failed", e2?.message || "Failed to inspect the staged queue");
      } finally {
        this.bulkPreviewLoading = false;
      }
    },
    async startQueue() {
      await this.createJobsFromStaging();
    },
    async forceStartItem(item) {
      const idx = this.queueItems.findIndex((qi2) => qi2.id === item.id);
      const removed = idx >= 0 ? this.queueItems.splice(idx, 1)[0] : null;
      try {
        const res = await this.apiFetch(`/api/uploads/queue/items/${item.id}/start`, { method: "POST" });
        this.showToast("success", "Started", `"${item.name}" is now uploading`);
        this.startTimeout(() => this.loadJobs(), 500);
        this.loadQueuedPaths();
      } catch (e2) {
        if (removed && idx >= 0) {
          this.queueItems.splice(idx, 0, removed);
        }
        this.showToast("error", "Error", "Failed to start item");
      }
    },
    // ============================================================
    //  Queue Display Helpers
    // ============================================================
    categoryBadgeClass(cat) {
      const map = {
        tv: "bg-cyan-500/15 text-cyan-400",
        movies: "bg-purple-500/15 text-purple-400",
        anime: "bg-pink-500/15 text-pink-400",
        disc: "bg-[#E0E0E0] text-[#2A2A2A] border-[#B9B9B9]",
        books: "bg-amber-500/15 text-amber-400",
        ebooks: "bg-amber-500/15 text-amber-400",
        audiobooks: "bg-orange-500/15 text-orange-400",
        music: "bg-emerald-500/15 text-emerald-400",
        apps: "bg-blue-500/15 text-blue-400",
        misc: "bg-orange-500/15 text-orange-400"
      };
      return map[cat] || "bg-notion-bg-hover text-notion-text-tertiary";
    },
    jobScheduleLabel(job) {
      if (!job || !job.run_after) return "";
      return this.formatRunAfterLocal(job.run_after);
    },
    isQueueSectionExpanded(sectionKey) {
      return this.queueSectionExpanded[sectionKey] !== false;
    },
    queueSectionClass(sectionKey) {
      return this.isQueueSectionExpanded(sectionKey) ? "section-content" : "section-content section-collapsed";
    },
    toggleQueueSection(sectionKey) {
      if (!sectionKey) return;
      this.queueSectionExpanded = {
        ...this.queueSectionExpanded,
        [sectionKey]: !this.isQueueSectionExpanded(sectionKey)
      };
      if (sectionKey === "pending") {
        try {
          localStorage.setItem("nzb_pending_expanded", String(this.queueSectionExpanded.pending !== false));
        } catch (_e2) {
        }
      }
    },
    async renameJobInline(job) {
      if (!job || !job.job_id) return;
      const currentName = (job.display_name || "").trim();
      const nextName = await this.promptDialog(`Set a custom name for job ${job.job_id}.`, {
        title: "Rename Job",
        detail: "Leave blank to clear the custom name.",
        icon: "pencil",
        value: currentName,
        placeholder: "e.g. Weekend TV batch",
        confirmLabel: "Save Name"
      });
      if (nextName === null) return;
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${job.job_id}/name`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: nextName.trim() || null })
        });
        const updatedName = res && res.display_name ? res.display_name : "";
        if (this.queuedJobModalJob && this.queuedJobModalJob.job_id === job.job_id) {
          this.queuedJobModalJob = {
            ...this.queuedJobModalJob,
            display_name: updatedName || null
          };
          this.queuedJobModalName = updatedName;
        }
        await this.loadJobs();
        this.showToast("success", "Saved", updatedName ? "Job name updated" : "Custom job name cleared");
      } catch (e2) {
        this.showToast("error", "Error", "Failed to update job name");
      }
    },
    async _resolveScheduleRunAfter(job, currentDate, currentTime) {
      const dateText = await this.promptDialog(`Set a deferred date for job ${job.job_id}.`, {
        title: "Schedule Job",
        detail: "Leave blank to run the job immediately.",
        icon: "calendar-clock",
        value: currentDate,
        placeholder: "YYYY-MM-DD",
        inputType: "date",
        confirmLabel: "Continue"
      });
      if (dateText === null) return void 0;
      const date = dateText.trim();
      if (!date) return null;

      const useTime = await this.confirmDialog("Set a specific time for this job?", {
        title: "Schedule Time",
        detail: `Choose "Use 12:00 PM" to run at noon on ${date}.`,
        confirmLabel: "Pick a Time",
        cancelLabel: "Use 12:00 PM"
      });
      let time = "12:00";
      if (useTime) {
        const timeText = await this.promptDialog(`Time to start job ${job.job_id} on ${date}.`, {
          title: "Schedule Time",
          detail: "24-hour format (HH:MM).",
          icon: "clock",
          value: currentTime,
          placeholder: "HH:MM",
          inputType: "time",
          confirmLabel: "Set Time"
        });
        if (timeText === null) return void 0;
        time = timeText.trim() || "12:00";
      }
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
        this.showToast("error", "Invalid date", "Use YYYY-MM-DD format.");
        return void 0;
      }
      if (!/^\d{2}:\d{2}$/.test(time)) {
        this.showToast("error", "Invalid time", "Use HH:MM format (24-hour).");
        return void 0;
      }
      const dt2 = /* @__PURE__ */ new Date(`${date}T${time}:00`);
      if (Number.isNaN(dt2.getTime())) {
        this.showToast("error", "Invalid date/time", "Please enter a valid date and optional time.");
        return void 0;
      }
      return dt2.toISOString();
    },
    async scheduleJobInline(job) {
      if (!job || !job.job_id) return;
      const current = job.run_after ? new Date(job.run_after) : null;
      const hasCurrent = !!(current && !Number.isNaN(current.getTime()));
      const currentDate = hasCurrent ? `${current.getFullYear()}-${String(current.getMonth() + 1).padStart(2, "0")}-${String(current.getDate()).padStart(2, "0")}` : "";
      const currentTime = hasCurrent ? `${String(current.getHours()).padStart(2, "0")}:${String(current.getMinutes()).padStart(2, "0")}` : "12:00";
      const runAfter = await this._resolveScheduleRunAfter(job, currentDate, currentTime);
      if (runAfter === void 0) return;
      try {
        const res = await this.apiFetch(`/api/uploads/queue/${job.job_id}/schedule`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_after: runAfter })
        });
        const updatedRunAfter = res && res.run_after ? res.run_after : null;
        if (this.queuedJobModalJob && this.queuedJobModalJob.job_id === job.job_id) {
          this.queuedJobModalJob = {
            ...this.queuedJobModalJob,
            run_after: updatedRunAfter
          };
          this.hydrateQueuedJobSchedule(updatedRunAfter);
        }
        await this.loadJobs();
        this.showToast(
          "success",
          "Saved",
          updatedRunAfter ? `Deferred to ${this.formatRunAfterLocal(updatedRunAfter)}` : "Job set to Immediate"
        );
      } catch (e2) {
        const detail = e2?.message || "Failed to update deferred schedule";
        this.showToast("error", "Error", detail);
      }
    },
    finishedStatusBg(job) {
      const cfg = this.getStatusConfig(job.status);
      return `${cfg.bg}`;
    },
    finishedStatusBadge(job) {
      const map = {
        completed: "bg-green-500/15 text-notion-success",
        failed: "bg-red-500/15 text-notion-error",
        stopped: "bg-yellow-500/15 text-notion-warning",
        cancelled: "bg-yellow-500/15 text-notion-warning"
      };
      return map[job.status] || "bg-notion-bg-hover text-notion-text-tertiary";
    },
    recentJobEvents(job, limit = 3) {
      if (!job || !Array.isArray(job.events)) return [];
      return job.events.slice(-Math.max(1, Number(limit) || 3)).reverse();
    },
    // ============================================================
    //  Job Actions
    // ============================================================
    isJobPaused(job) {
      return job && (job.status === "paused" || !!job.pause_requested);
    },
    isJobPausing(job) {
      return job && job.status === "running" && !!job.pause_requested;
    },
    isJobStopping(job) {
      return job && job.status === "stopping";
    },
    isJobActionPending(jobId) {
      return !!jobId && this.pendingJobActions.has(jobId);
    },
    setJobActionPending(jobId, pending) {
      const next = new Set(this.pendingJobActions);
      if (pending) next.add(jobId);
      else next.delete(jobId);
      this.pendingJobActions = next;
    },
    async pauseJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/pause`, { method: "POST" });
        this.showToast("info", res.status === "paused" ? "Paused" : "Pause Requested", res.message || "Pause requested");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to pause job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },
    async resumeJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/resume`, { method: "POST" });
        this.showToast("success", "Resumed", res.message || "Job resumed");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to resume job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },
    async retryJob(job) {
      const jobId = job && job.job_id;
      if (!jobId || this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/retry`, { method: "POST" });
        this.showToast("success", "Retry Queued", res.message || `Retry queued as ${res.job_id}`);
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Retry Failed", e2?.message || "Failed to queue retry");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },
    async stopJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/stop`, { method: "POST" });
        this.showToast("info", "Stopping", res.message || "Stop signal sent");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to stop job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },
    async stopAndClearJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      const ok = await this.confirmDialog("Stop this active job and remove it from the queue view?", {
        title: "Stop And Clear Job",
        detail: "The backend will still kill any active tool processes and clean temp files.",
        danger: true,
        confirmLabel: "Stop And Clear"
      });
      if (!ok) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/stop-clear`, { method: "POST" });
        this.running = this.running.filter((j2) => j2.job_id !== jobId);
        this.queued = this.queued.filter((j2) => j2.job_id !== jobId);
        this.counts.running = this.running.length;
        this.counts.queued = this.queued.length;
        this.showToast("warning", "Clearing", res.message || "Job stopping and clearing");
        this.startTimeout(() => this.loadJobs(), 600);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to stop and clear job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },
    async pauseQueueProcessing() {
      try {
        const res = await this.apiFetch("/api/uploads/queue/pause", { method: "POST" });
        this.showToast("info", "Queue Paused", res.message || "Queue processing paused");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to pause queue processing");
      }
    },
    async resumeQueueProcessing() {
      try {
        const res = await this.apiFetch("/api/uploads/queue/resume", { method: "POST" });
        this.showToast("success", "Queue Resumed", res.message || "Queue processing resumed");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to resume queue processing");
      }
    },
    async stopQueueProcessing() {
      const ok = await this.confirmDialog("Stop the active job and pause queue processing?", {
        title: "Stop Queue Processing",
        detail: "Queued jobs will remain in the job queue.",
        danger: true,
        confirmLabel: "Stop Queue"
      });
      if (!ok) return;
      try {
        const res = await this.apiFetch("/api/uploads/queue/stop", { method: "POST" });
        this.showToast("warning", "Queue Stopping", res.message || "Queue processing stopping");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to stop queue processing");
      }
    },
    async stopQueueAndClearProcessing() {
      const ok = await this.confirmDialog("Stop the active job and clear all waiting jobs?", {
        title: "Stop And Clear Queue",
        detail: "The active row is hidden while the job shuts down in the background.",
        danger: true,
        confirmLabel: "Stop And Clear"
      });
      if (!ok) return;
      try {
        const res = await this.apiFetch("/api/uploads/queue/stop-clear", { method: "POST" });
        this.running = [];
        this.queued = [];
        this.counts.running = 0;
        this.counts.queued = 0;
        this.queueControl = res.control || this.queueControl;
        this.showToast("warning", "Queue Clearing", res.message || "Queue stopping and clearing");
        this.startTimeout(() => this.loadJobs(), 700);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to stop and clear queue");
      }
    },
    async cancelJob(jobId) {
      try {
        await this.apiFetch(`/api/uploads/jobs/${jobId}/stop`, { method: "POST" });
        this.queued = this.queued.filter((j2) => j2.job_id !== jobId);
        this.counts.queued = this.queued.length;
        if (this.queuedJobModalJob && this.queuedJobModalJob.job_id === jobId) {
          this.closeQueuedJobModal();
        }
        this.showToast("success", "Removed", "Job removed from queue");
        this.startTimeout(() => this.loadJobs(), 300);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to cancel job");
      }
    },
    async promoteJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        await this.apiFetch(`/api/uploads/queue/${jobId}/promote`, { method: "POST" });
        this.showToast("success", "Promoted", "Job moved to front of queue");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to promote job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },
    async clearJobQueue() {
      const ok = await this.confirmDialog(`Cancel all ${this.counts.queued} waiting job(s)?`, {
        title: "Clear Waiting Jobs",
        detail: "This does NOT clear staged items.",
        danger: true,
        confirmLabel: "Cancel Jobs",
        cancelLabel: "Keep Jobs"
      });
      if (!ok) return;
      try {
        const res = await this.apiFetch("/api/uploads/queue/clear", { method: "POST" });
        this.closeQueuedJobModal();
        this.showToast("success", "Waiting Jobs Cleared", `${res.cleared} queued job(s) cancelled`);
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to clear job queue");
      }
    },
    async clearFinished() {
      try {
        await this.apiFetch("/api/uploads/jobs/completed", { method: "DELETE" });
        this.finished = [];
        this.counts.finished = 0;
        this.showFinishedModal = false;
        this.showToast("success", "Cleared", "Finished jobs dismissed");
      } catch (e2) {
        this.showToast("error", "Error", "Failed to clear finished jobs");
      }
    },
    async deleteJob(jobId) {
      this.finished = this.finished.filter((j2) => j2.job_id !== jobId);
      this.counts.finished = this.finished.length;
      try {
        await this.apiFetch(`/api/uploads/jobs/${jobId}`, { method: "DELETE" });
      } catch (e2) {
        this.showToast("error", "Error", "Failed to delete job");
        await this.loadJobs();
      }
    },
    // ============================================================
    //  Refresh All
    // ============================================================
    refreshAll(forceRefresh = false) {
      this.loadPending(forceRefresh);
      this.loadQueueItems();
      this.loadJobs();
      this.loadQueuedPaths();
    }
  },
  watch: {
    searchQuery() {
      this.debouncedLoadPending();
    },
    literalSearch() {
      this.loadPending();
    },
    selectedCategories() {
      this.categoryFilterOpen = false;
      if (!this.categorySelectionReady) {
        return;
      }
      this._normalizeSelectedCategories();
    },
    availableCategories(cats) {
      if (!this.categorySelectionReady) {
        return;
      }
      this._normalizeSelectedCategories();
    },
    queuedJobModalSearch() {
      this.$nextTick(() => this.initQueuedJobModalSortable());
    },
    activeJobModalSearch() {
      this.$nextTick(() => this.initActiveJobModalSortable());
    }
  },
  mounted() {
    try {
      this._sortable = null;
      this._activeJobModalSortable = null;
      this._queuedJobModalSortable = null;
      this.loadSummary();
      this.loadQueuedPaths();
      this.loadProcessingSettings().then(() => this.loadPending()).catch((e2) => console.error("loadProcessingSettings/loadPending failed", e2));
      this.loadQueueItems();
      this.loadJobs();
      let _idleTick = 0;
      this.startInterval(() => {
        const hasActivity = this.counts.running > 0 || this.counts.queued > 0;
        let pollNow;
        if (hasActivity) {
          _idleTick = 0;
          pollNow = true;
        } else {
          pollNow = _idleTick++ % 3 === 0;
        }
        if (pollNow) this.loadJobs();
        const stagingVisible = !!(this.queueSectionExpanded && this.queueSectionExpanded.staging);
        if (!hasActivity && stagingVisible && pollNow) {
          this.loadQueueItems();
        }
      }, 2e3);
      this.startInterval(this.loadPending, 6e4);
    } catch (e2) {
      console.error("NZBPostarr Hydration Safeguard Hook:", e2);
      this.loading = false;
    }
  },
  beforeUnmount() {
    if (this._animeWatcher) {
      clearInterval(this._animeWatcher);
      this._animeWatcher = null;
    }
    if (this._loadingTimer) {
      clearInterval(this._loadingTimer);
      this._loadingTimer = null;
    }
    if (this._queuedPathsTimer) {
      clearTimeout(this._queuedPathsTimer);
      this._queuedPathsTimer = null;
    }
    if (this._queuedJobLoadTimer) {
      clearTimeout(this._queuedJobLoadTimer);
      this._queuedJobLoadTimer = null;
    }
    if (this.debouncedLoadPending && typeof this.debouncedLoadPending.cancel === "function") {
      this.debouncedLoadPending.cancel();
    }
    this.destroySortableInstance("_sortable");
    this.destroyActiveJobModalSortable();
    this.destroyQueuedJobModalSortable();
    this.destroyPendingExternalGroupsSortable();
  }
});
