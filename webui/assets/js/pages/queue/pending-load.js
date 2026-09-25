// Queue page: Loading the pending tree: /api/pending/items, summary, overrides, queued paths and lazy children.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.
import { deepFreezePendingTree, normalizePendingNode, normalizePendingExternalGroups } from "./pending-tree.logic.js";


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

export default {
  computed: {
    cacheAge() {
      if (!this.cachedAt) return "";
      const age = Math.round(Date.now() / 1e3 - this.cachedAt);
      if (age < 2) return "just now";
      if (age < 60) return `${age}s ago`;
      return `${Math.floor(age / 60)}m ago`;
    },
  },
  methods: {
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
      } catch (_summaryError) {
        // The summary is optional; apiFetch has already reported the failure.
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
    //  Refresh All
    // ============================================================
    refreshAll(forceRefresh = false) {
      this.loadPending(forceRefresh);
      this.loadQueueItems();
      this.loadJobs();
      this.loadQueuedPaths();
    },

    _graftLoadedChildren(newBase) {
      const old = this._lastRawItems;
      if (!old || !newBase) return;
      const graft = (newNode, oldNode) => {
        if (!newNode || !oldNode) return;
        const oldKids = oldNode.children || oldNode.files || [];
        const newKids = newNode.children || newNode.files || [];
        if (oldKids.length > 0 && newKids.length === 0) {
          // The previous tree is deep-frozen; graft a thawed copy so the
          // normalizer can still annotate the grafted nodes.
          const kids = this._clonePendingItems({ kids: oldKids }).kids || [];
          newNode.children = kids;
          newNode.files = kids;
        }
        for (const nc of (newNode.children || [])) {
          const oc = oldKids.find(c => c.key === nc.key);
          if (oc) graft(nc, oc);
        }
      };
      for (const [gi, group] of (newBase.external || []).entries()) {
        const og = (old.external || [])[gi];
        if (!og) continue;
        for (const item of (group.items || [])) {
          const oi = (og.items || []).find(i => i.key === item.key);
          if (oi) graft(item, oi);
        }
      }
    },

    _clonePendingItems(items) {
      if (!items || typeof items !== "object") return { movies: [], misc: [], external: [] };
      try {
        if (typeof structuredClone === "function") {
          return structuredClone(items);
        }
      } catch (_cloneError) {
        // structuredClone refused the tree: fall back to the JSON copy below.
      }
      try {
        return JSON.parse(JSON.stringify(items));
      } catch (_e2) {
        return { movies: [], misc: [], external: [] };
      }
    },

    _findPendingNodeByKey(items, targetKey, targetPath = "") {
      if (!items || !targetKey) return null;
      const normalizedTargetPath = this.normalizePathKey(targetPath || "");
      const visit = (node) => {
        if (!node || typeof node !== "object") return null;
        if (String(node.key || "") === String(targetKey)) return node;
        if (normalizedTargetPath && this.normalizePathKey(node.path || "") === normalizedTargetPath) return node;
        const childLists = [];
        if (Array.isArray(node.children)) childLists.push(node.children);
        if (Array.isArray(node.files) && node.files !== node.children) childLists.push(node.files);
        if (Array.isArray(node.items)) childLists.push(node.items);
        for (const list of childLists) {
          for (const child of list) {
            const found = visit(child);
            if (found) return found;
          }
        }
        return null;
      };
      for (const section of Object.values(items)) {
        if (!Array.isArray(section)) continue;
        for (const entry of section) {
          const found = visit(entry);
          if (found) return found;
        }
      }
      return null;
    },

    _findCurrentPendingNode(targetKey, targetPath = "") {
      return this._findPendingNodeByKey(this.items, targetKey, targetPath);
    },

    _replacePendingTree(rawItems) {
      const nextRaw = rawItems && typeof rawItems === "object" ? rawItems : { movies: [], misc: [], external: [] };
      this._lastRawItems = nextRaw;
      this.skipFiles = this.skipFiles || { enabled: false, display_mode: "disabled" };
      const itemsForState = this.skipFiles.enabled && this.skipFiles.display_mode === "hidden" ? this._stripHiddenSkippedItems(nextRaw) : nextRaw;
      this._normalizePendingItemsForState(itemsForState);
      this.items = deepFreezePendingTree(itemsForState);
      this._applyDetectedCategories();
    },

    async ensureExtChildrenLoaded(itemOrKey, fallbackPath = "") {
      const currentItem = typeof itemOrKey === "string" ? this._findCurrentPendingNode(itemOrKey, fallbackPath) : itemOrKey;
      if (!currentItem || !currentItem.is_dir) return false;
      const itemKey = String(currentItem.key || (typeof itemOrKey === "string" ? itemOrKey : ""));
      if (!itemKey) return false;
      const existingChildren = this._getLoadedRowChildren(currentItem);
      if (existingChildren.length > 0) return true;
      if (Number(currentItem.child_count || 0) <= 0) return false;
      if (this._loadingExtChildren[itemKey]) {
        try {
          await this._loadingExtChildren[itemKey];
        } catch (_inFlightError) {
          // The in-flight load reports its own failure; re-read the tree below.
        }
        const refreshed = this._findCurrentPendingNode(itemKey, currentItem.path || fallbackPath);
        return this._getLoadedRowChildren(refreshed).length > 0;
      }
      const loader = (async () => {
        const params = new URLSearchParams();
        params.set("key", itemKey);
        if (currentItem.path || fallbackPath) params.set("path", currentItem.path || fallbackPath);
        const data = await this.apiFetch(`/api/pending/children?${params.toString()}`, { timeoutMs: 9e4 });
        const loadedChildren = Array.isArray(data == null ? void 0 : data.children) ? data.children : [];
        const rawBase = this._clonePendingItems(this._lastRawItems || this.items);
        const target = this._findPendingNodeByKey(rawBase, itemKey, currentItem.path || fallbackPath);
        if (!target) return false;
        target.children = loadedChildren;
        target.files = loadedChildren;
        target.child_count = Number(data == null ? void 0 : data.child_count) || loadedChildren.length;
        this._replacePendingTree(rawBase);
        if (loadedChildren.length > 0 && this.selectedItems.has(itemKey)) {
          const nextSet = new Set(this.selectedItems);
          loadedChildren.forEach((child) => { if (child && child.key) nextSet.add(child.key); });
          this.selectedItems = nextSet;
        }
        return loadedChildren.length > 0;
      })();
      this._loadingExtChildren[itemKey] = loader;
      try {
        return await loader;
      } catch (_e2) {
        if (_e2 && _e2.status === 401) {
          window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
        } else {
          this.showToast("error", "Error", "Failed to load child items");
        }
        return false;
      } finally {
        delete this._loadingExtChildren[itemKey];
      }
    },

    isExtChildrenLoading(itemOrKey, fallbackPath = "") {
      const currentItem = typeof itemOrKey === "string" ? this._findCurrentPendingNode(itemOrKey, fallbackPath) : itemOrKey;
      const itemKey = currentItem && currentItem.key ? String(currentItem.key) : typeof itemOrKey === "string" ? itemOrKey : "";
      return !!(itemKey && this._loadingExtChildren[itemKey]);
    },

    async _hydrateExpandedExtChildren() {
      if (this._expandedExtHydrationPromise) {
        return this._expandedExtHydrationPromise;
      }
      const expandedKeys = Object.entries(this.expandedExtItems || {}).filter(([, open]) => !!open).map(([key]) => key).filter(Boolean);
      if (expandedKeys.length === 0) return;
      this._expandedExtHydrationPromise = (async () => {
        for (const key of expandedKeys) {
          const node = this._findCurrentPendingNode(key);
          if (!node || !node.is_dir) continue;
          if (this._getLoadedRowChildren(node).length > 0) continue;
          if (Number(node.child_count || 0) <= 0) continue;
          await this.ensureExtChildrenLoaded(node);
        }
      })();
      try {
        await this._expandedExtHydrationPromise;
      } finally {
        this._expandedExtHydrationPromise = null;
      }
    },

    /**
     * Recursively fetches children for any collapsed (never-expanded)
     * directory under `node`, so bulk-selecting a folder that was never
     * manually expanded still picks up every file inside it. Without
     * this, checking a collapsed folder's box only selected the folder
     * key itself (its children array was empty client-side), which
     * produced zero real uploadable items and silently did nothing.
     */
    async _ensureExtSubtreeLoaded(node) {
      if (!node || !node.is_dir) return node;
      let current = node;
      if (this._getLoadedRowChildren(current).length === 0 && Number(current.child_count || 0) > 0) {
        await this.ensureExtChildrenLoaded(current);
        current = this._findCurrentPendingNode(current.key, current.path) || current;
      }
      const kids = this._getLoadedRowChildren(current);
      for (const child of kids) {
        await this._ensureExtSubtreeLoaded(child);
      }
      return this._findCurrentPendingNode(current.key, current.path) || current;
    },
  },
};

export { syncPendingAnimeWatcher, runPendingLoad, applyLoadedPendingItems };
