// Auto-split from queue.js - verbatim methods bodies.
import Sortable from "sortablejs";
import { EXTERNAL_GROUP_ORDER_KEY, normalizePendingNode, normalizePendingExternalGroups, runPendingLoad } from "./queue.js";
export default {
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
      const entry = this.findFolderEntryForGroup(group);
      return !!(entry && entry.manual_select_only);
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
          const folderEntries = settings && settings.folders ? settings.folders.folder_paths : null;
          this.folderPathEntries = Array.isArray(folderEntries) ? folderEntries.slice() : [];
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

async loadCategoryOverrides() {
      try {
        const data = await this.apiFetch("/api/pending/category-overrides");
        const serverOverrides = (data && data.overrides) || {};
        // Server is the durable source; anything already chosen locally in
        // this browser (e.g. a change made moments ago, not yet round-tripped)
        // wins so we never clobber an in-flight edit.
        this.manualExternalCategories = { ...serverOverrides, ...this.manualExternalCategories };
      } catch (e2) {
        console.warn("Failed to load category overrides", e2);
      }
    },

async loadPending(forceRefresh = false, silent = false) {
      if (this._loadPendingPromise) return this._loadPendingPromise;
      if (!silent && !forceRefresh && this.loading && Object.values(this.items).every((v2) => !Array.isArray(v2) || v2.length === 0)) {
        this._restoreSessionCache();
      }
      this._loadPendingPromise = runPendingLoad(this, forceRefresh, silent);
      return this._loadPendingPromise;
    },

_normalizePendingItemsForState(items) {
      if (!items || typeof items !== "object") return items;
      normalizePendingExternalGroups(this, items);
      for (const [key, val] of Object.entries(items)) {
        if (key === "external" || !Array.isArray(val)) continue;
        val.forEach((node) => normalizePendingNode(this, node));
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
};
