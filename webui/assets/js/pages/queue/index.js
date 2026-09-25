// Queue page entry. Each pages/queue/*.js feature module is a Vue options fragment
// ({ computed, methods }); they are merged into the page's own computed/methods here rather
// than passed as Vue mixins, so the page's methods keep overriding page-base's shared ones
// exactly as before (a mixin would lose to them).
import { createVuePage } from "page-base";
import debounce from "lodash.debounce";
import sortable from "./sortable.js";
import storage, { EXTERNAL_GROUP_LOCK_KEY } from "./storage.js";
import pendingLoad from "./pending-load.js";
import pendingFilters from "./pending-filters.js";
import pendingIgnore from "./pending-ignore.js";
import pendingSelection from "./pending-selection.js";
import bulkSelect from "./bulk-select.js";
import pendingCategories from "./pending-categories.js";
import pendingGroups from "./pending-groups.js";
import forceUpload from "./force-upload.js";
import staging from "./staging.js";
import jobsList from "./jobs-list.js";
import jobControls from "./job-controls.js";
import activeJobModal from "./active-job-modal.js";
import queuedJobModal from "./queued-job-modal.js";
import { startPageComponents } from "../../core/page.js";

const features = [
  sortable,
  storage,
  pendingLoad,
  pendingFilters,
  pendingIgnore,
  pendingSelection,
  bulkSelect,
  pendingCategories,
  pendingGroups,
  forceUpload,
  staging,
  jobsList,
  jobControls,
  activeJobModal,
  queuedJobModal,
];

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
  computed: Object.assign({}, ...features.map((feature) => feature.computed || {})),
  methods: Object.assign({}, ...features.map((feature) => feature.methods || {})),
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
startPageComponents();
