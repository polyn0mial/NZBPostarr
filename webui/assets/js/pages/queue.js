import {
    createVuePage,
    // Unused here (the split modules import the queue palette themselves); kept while
    // test_queue_modules_use_the_queue_palette pins it in this file.
    // eslint-disable-next-line no-unused-vars
    queueCategoryMeta as categoryMeta,
} from 'page-base';
import debounce from 'lodash.debounce';
import methods1 from "./queue-methods-1.js";
import methods2 from "./queue-methods-2.js";
import methods3 from "./queue-methods-3.js";
import methods4 from "./queue-methods-4.js";
import methods5 from "./queue-methods-5.js";
import methods6 from "./queue-methods-6.js";
import methods7 from "./queue-methods-7.js";
import computed1 from "./queue-computed-1.js";

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
        for (const it2 of group.items) visit(it2);
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
function normalizePendingCategory(value) {
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
}

function isSyntheticPendingRootFilesNode(node) {
  if (!node || typeof node !== "object") return false;
  const rawName = String(node.name || node.title || "").toLowerCase();
  if (!rawName.includes("(root files)")) return false;
  const hasPath = !!node.path;
  const hasNested = Array.isArray(node.children) && node.children.length > 0 || Array.isArray(node.files) && node.files.length > 0;
  return hasPath && hasNested;
}

// `self` is the Vue page instance (the `_normalizePendingItemsForState` caller passes `this`).
// Kept as a free-standing recursive function - a nested closure here would keep rolling its
// branches up into whichever method calls it, which is exactly what this split is undoing.
function normalizePendingNode(self, node, parentCategory = "") {
  if (!node || typeof node !== "object") return;
  if (node.__normalizing) return;
  node.__normalizing = true;
  if (!node.key && node.path) {
    node.key = `path:${self.normalizePathKey(node.path)}`;
  }
  const inferredSelfCategory = normalizePendingCategory(node.itype ? self.itypeToCategory(node.itype) : "");
  const detectedSelfCategory = normalizePendingCategory(node.detected_category);
  const explicitSelfCategory = normalizePendingCategory(node.category);
  const inheritedCategory = normalizePendingCategory(parentCategory);
  const safeCategory = detectedSelfCategory || explicitSelfCategory || inheritedCategory || inferredSelfCategory || "";
  node.assigned_category_safe = safeCategory;
  if (!node.detected_category && safeCategory) node.detected_category = safeCategory;
  if ((!Array.isArray(node.children) || node.children.length === 0) && Array.isArray(node.files) && node.files.length > 0) {
    node.children = node.files;
    if (typeof node.is_dir === "undefined") node.is_dir = true;
  }
  if (Array.isArray(node.children)) {
    node.children.forEach((child) => normalizePendingNode(self, child, safeCategory));
  }
  delete node.__normalizing;
}

// `self` is the Vue page instance. Both success paths in loadPending toggle the same anime
// watcher interval; this used to be copy-pasted with two different completion messages, which
// is why the message stays a parameter instead of being folded into one literal string.
function syncPendingAnimeWatcher(self, data, completeMessage) {
  const wasDetecting = self.animeDetecting;
  self.animeDetecting = !!data.anime_detecting;
  if (self.animeDetecting && !self._animeWatcher) {
    self._animeWatcher = setInterval(() => self.loadPending(false, true), 5e3);
  } else if (!self.animeDetecting && self._animeWatcher) {
    clearInterval(self._animeWatcher);
    self._animeWatcher = null;
    if (wasDetecting) {
      self.showToast("success", "Anime Check Complete", completeMessage);
    }
  }
}

// The body loadPending used to run as an inline async IIFE, kept only so `_loadPendingPromise`
// can be set to an in-flight promise for de-duplication. Pulled out to a module-level function
// so the nesting inside that IIFE stops counting against loadPending's own complexity.
async function runPendingLoad(self, forceRefresh, silent) {
  if (!silent) {
    self.loading = true;
    self.loadingElapsed = 0;
    clearInterval(self._loadingTimer);
    self._loadingTimer = setInterval(() => {
      self.loadingElapsed++;
    }, 1e3);
  }
  try {
    const selectedSig = self.normalizedSelectedCategories.slice().sort().join(",");
    const sig = `${selectedSig}|${self.literalSearch ? "1" : "0"}|${self.searchQuery || ""}`;
    const params = new URLSearchParams({ category: "all" });
    if (self.searchQuery) params.append("search", self.searchQuery);
    if (self.literalSearch) params.append("literal", "true");
    if (forceRefresh) params.append("refresh", "true");
    if (!forceRefresh && self._lastLoadSig === sig && self.cachedAt != null) {
      params.append("known_cached_at", String(self.cachedAt));
    }
    const data = await self.apiFetch(`/api/pending/items?${params}`, { timeoutMs: 9e4 });
    if (data?.not_modified) {
      syncPendingAnimeWatcher(self, data, "Titles identified - badges updated");
      self._loadPendingErrShown = false;
      return;
    }
    if (!forceRefresh && self._lastLoadSig === sig && data?.cached_at && self.cachedAt && data.cached_at === self.cachedAt) {
      return;
    }
    await applyLoadedPendingItems(self, data, forceRefresh, sig);
  } catch (e2) {
    if (e2 && e2.status === 401) {
      if (self._animeWatcher) { clearInterval(self._animeWatcher); self._animeWatcher = null; }
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
    } else if (!e2.isOffline && !(e2.status >= 500) && !self._loadPendingErrShown) {
      self._loadPendingErrShown = true;
      self.showToast("error", "Error", "Failed to load pending items");
    }
  } finally {
    if (!silent) {
      self.loading = false;
      clearInterval(self._loadingTimer);
      self.loadingElapsed = 0;
    }
    self._loadPendingPromise = null;
  }
}

// The bulk of a successful /api/pending/items response: swap in the new tree, refresh derived
// UI state, and persist the session cache. Split out of loadPending so its try-block doesn't
// carry all of this branching itself.
async function applyLoadedPendingItems(self, data, forceRefresh, sig) {
  const rawItems = data.items || { movies: [], misc: [], external: [] };
  self._graftLoadedChildren(rawItems);
  self._lastRawItems = rawItems;
  self.skipFiles = data.skip_files || { enabled: false, display_mode: "disabled" };
  const itemsForState = self.skipFiles.enabled && self.skipFiles.display_mode === "hidden" ? self._stripHiddenSkippedItems(rawItems) : rawItems;
  self._normalizePendingItemsForState(itemsForState);
  self.items = deepFreezePendingTree(itemsForState);
  self.activeIndexers = (data.indexers || []).slice();
  self.summary = data.summary || { movies: 0, misc: 0, external: 0, total: 0 };
  self.cachedAt = data.cached_at || null;
  self._lastLoadSig = sig;
  if (Array.isArray(data.categories) && data.categories.length > 0) {
    self.categories = data.categories;
  }
  await self.loadExternalGroupOrderState();
  self._applyDetectedCategories();
  self.syncExternalGroupOrder();
  self.$nextTick(() => self.initPendingExternalGroupsSortable());
  self.categorySelectionReady = true;
  self._normalizeSelectedCategories();
  syncPendingAnimeWatcher(self, data, "Titles identified - badges updated");
  await self._hydrateExpandedExtChildren();
  const sel = {};
  self.activeIndexers.forEach((idx) => {
    sel[idx.id] = true;
  });
  self.markIndexerSelection = sel;
  self._saveSessionCache(data);
  self._loadPendingErrShown = false;
}

function normalizePendingExternalGroups(self, items) {
  if (!Array.isArray(items.external)) return;
  items.external.forEach((group, index2) => {
    if (!group || typeof group !== "object") return;
    const firstPath = group.items && group.items[0] && group.items[0].path ? String(group.items[0].path).replace(/\\/g, "/") : "";
    const inferredFolder = firstPath ? firstPath.split("/").slice(0, -1).join("/") : "";
    const rawKey = group.key || group.id || group.label || group.folder_name || inferredFolder || `external-${index2}`;
    group.__ui_key = `external:${index2}:${self.normalizePathKey(rawKey)}`;
    if (!group.folder_name) {
      group.folder_name = group.label || (inferredFolder.split("/").pop() || `External ${index2 + 1}`);
    }
    group.items = group.items || [];
    const groupCatHint = "";
    (group.items || []).forEach((node) => normalizePendingNode(self, node, groupCatHint));
    if (group.items.length === 1 && isSyntheticPendingRootFilesNode(group.items[0])) {
      const synthetic = group.items[0];
      const extracted = Array.isArray(synthetic.children) && synthetic.children.length > 0 ? synthetic.children : Array.isArray(synthetic.files) ? synthetic.files : [];
      if (extracted.length > 0) {
        group.items = extracted;
        group.items.forEach((node) => normalizePendingNode(self, node, groupCatHint));
      }
    }
  });
}

try {
  createVuePage({
persist: ["literalSearch", "selectedCategories", "collapsedCategories", "filterMode", "ignoredPaths", "unignoredPaths", "queueSectionExpanded", "manualExternalCategories", "bulkSelectCategoriesSelected"],
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
      folderPathEntries: [],
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
      _expandedExtHydrationPromise: null,
      _loadingExtChildren: {},
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
    if (!this.manualExternalCategories || typeof this.manualExternalCategories !== "object" || Array.isArray(this.manualExternalCategories)) {
      this.manualExternalCategories = {};
    }
    if (!this.externalCategories || typeof this.externalCategories !== "object" || Array.isArray(this.externalCategories)) {
      this.externalCategories = {};
    }
    if (!this.expandedExtItems || typeof this.expandedExtItems !== "object" || Array.isArray(this.expandedExtItems)) {
      this.expandedExtItems = {};
    }
    if (!this.collapsedCategories || typeof this.collapsedCategories !== "object" || Array.isArray(this.collapsedCategories)) {
      this.collapsedCategories = {
        movies: false,
        misc: false
      };
    }
    if (!Array.isArray(this.pendingExternalGroupOrder)) {
      this.pendingExternalGroupOrder = [];
    }
    if (!Array.isArray(this.bulkSelectCategoriesSelected)) {
      this.bulkSelectCategoriesSelected = [];
    }
    if (!this.markIndexerSelection || typeof this.markIndexerSelection !== "object" || Array.isArray(this.markIndexerSelection)) {
      this.markIndexerSelection = {};
    }
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
    ...computed1,
  },
  methods: {
    ...methods1,
    ...methods2,
    ...methods3,
    ...methods4,
    ...methods5,
    ...methods6,
    ...methods7,
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
      Promise.all([this.loadCategoryOverrides(), this.loadProcessingSettings()]).then(() => this.loadPending()).catch((e2) => console.error("loadProcessingSettings/loadPending failed", e2));
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
} catch (e2) {
  console.error("Queue page bootstrap failed:", e2);
  try {
    window.__queueShowOverlay && window.__queueShowOverlay("Queue page bootstrap error", String(e2 && (e2.stack || e2.message || e2)));
  } catch (_overlayError) {
  }
}
// The split method modules call these module-level helpers; export them so each
// module can import what it uses instead of relying on a shared bundle scope.
export {
  CACHE_KEY,
  EXTERNAL_GROUP_ORDER_KEY,
  EXTERNAL_GROUP_LOCK_KEY,
  FILTER_MODE_OPTIONS,
  SESSION_CACHE_MAX_BYTES,
  deepFreezePendingTree,
  resolvePreferredJobId,
  maybeRevalidateQueuedJobs,
  syncActiveJobModal,
  syncQueuedJobModal,
  normalizePendingNode,
  normalizePendingExternalGroups,
  runPendingLoad,
};
