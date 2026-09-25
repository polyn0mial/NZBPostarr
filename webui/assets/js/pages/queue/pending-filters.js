// Queue page: Pending list filters and the visible-row index the template renders from.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.
import { FILTER_MODE_OPTIONS, searchMatches } from "./pending-visibility.logic.js";

export default {
  computed: {
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
    // `items` tree is frozen (see the pending-tree freeze helper in queue.js), the only deps
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
    },
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

    isFilterActive(mode) {
      return this.activeFilterModes.has(mode);
    },

    isZeroSize(size2) {
      return Number(size2) === 0;
    },

    sizeToneClass(size2, defaultClass = "text-notion-text-tertiary") {
      return this.isZeroSize(size2) ? "text-notion-error font-semibold" : defaultClass;
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
      return searchMatches(name, this.searchQuery, this.literalSearch);
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

    _getLoadedRowChildren(item) {
      if (!item || typeof item !== "object") return [];
      if (Array.isArray(item.children) && item.children.length > 0) return item.children;
      if (Array.isArray(item.files) && item.files.length > 0) return item.files;
      return [];
    },

    hasLoadedRowChildren(item) {
      return this._getLoadedRowChildren(item).length > 0;
    },

    visibleExtChildrenOf(item) {
      const idx = this._visibilityIndex;
      const visible = idx.extChildrenByKey.get(item.key);
      if (Array.isArray(visible) && visible.length > 0) return visible;
      return this._getLoadedRowChildren(item);
    },

    hasRowChildren(item) {
      if (!item || typeof item !== "object") return false;
      if (Array.isArray(item.children) && item.children.length > 0) return true;
      if (Array.isArray(item.files) && item.files.length > 0) return true;
      if (Number(item.child_count || 0) > 0) return true;
      return false;
    },

    getRowChildren(item) {
      return this._getLoadedRowChildren(item);
    },

    getRowChildCount(item) {
      if (!item || typeof item !== "object") return 0;
      const counted = /* @__PURE__ */ new Set();
      let total = 0;
      const visit = (node) => {
        if (!node || typeof node !== "object") return;
        const key = node.key || node.path || node.name || null;
        if (key && counted.has(key)) return;
        if (key) counted.add(key);
        total += 1;
        const children = Array.isArray(node.children) ? node.children : Array.isArray(node.files) ? node.files : [];
        children.forEach(visit);
      };
      const roots = Array.isArray(item.children) && item.children.length > 0 ? item.children : Array.isArray(item.files) ? item.files : [];
      roots.forEach(visit);
      return total || Number(item.child_count || 0) || 0;
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

    isExternalDescendantNode(item) {
      const key = item?.key || "";
      if (!key.startsWith("ext:")) return false;
      const rel = key.split(":").slice(2).join(":");
      return !!rel && rel.indexOf("/") !== -1;
    },

    isPackOnlyExternalChild(item) {
      if (!item || !this.isExternalDescendantNode(item)) return false;
      const ancestorCategory = this.findExternalAncestorCategory(item);
      if (!ancestorCategory || ["tv", "anime"].includes(ancestorCategory)) return false;
      return true;
    },

    findFolderEntryForGroup(group) {
      if (!group || typeof group !== "object") return null;
      const groupPath = this.normalizePathKey(group.folder_path || group.key || "");
      if (!groupPath) return null;
      const entries = Array.isArray(this.folderPathEntries) ? this.folderPathEntries : [];
      for (const entry of entries) {
        const entryPath = this.normalizePathKey(entry && entry.path ? entry.path : "");
        if (entryPath && entryPath === groupPath) {
          return entry;
        }
      }
      return null;
    },

    isGroupManualSelectionOnly(group) {
      // A configured directory with bulk selection turned off is manual selection only.
      const entry = this.findFolderEntryForGroup(group);
      return !!(entry && entry.allow_bulk_selection === false);
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

    isItemFilepart(item) {
      return !!(item && item.has_filepart);
    },

    isItemExcluded(item) {
      return item && item.excluded === true;
    },

    openFilterMode(event) {
      const rect = (event.target.closest("button") || event.target).getBoundingClientRect();
      this.filterModePos = { x: rect.left, y: rect.bottom + 4 };
      this.filterModeOpen = !this.filterModeOpen;
      this.bulkSelectOpen = false;
      this.categoryFilterOpen = false;
    },
  },
};
