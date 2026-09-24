// Auto-split from queue.js - verbatim computed bodies.
import { queueCategoryMeta as categoryMeta, categoryLabel } from "page-base";
import { FILTER_MODE_OPTIONS, deepFreezePendingTree } from "./queue.js";

export default {
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

selectedBreakdown() {
      let packs = 0;
      let packEpisodes = 0;
      const standaloneFiles = /* @__PURE__ */ new Set();
      const selectedPackPaths = /* @__PURE__ */ new Set();
      const visible = this.getVisibleSelectableItemsMap();
      for (const key of this.selectedItems) {
        const meta = visible.get(key) || this.selectedMeta.get(key);
        const path = meta && meta.path || "";
        const isDir = (meta && typeof meta.is_dir === "boolean") ? meta.is_dir : this._selectionKeyIsDirectory(key, path);
        if (isDir) {
          packs += 1;
          const np = path ? this.normalizePathKey(path) : null;
          if (np) selectedPackPaths.add(np);
          packEpisodes += Number(meta && meta.child_count || 0);
        }
      }
      const _isChildOfPack = (fp) => {
        if (!fp || selectedPackPaths.size === 0) return false;
        const norm = this.normalizePathKey(fp);
        for (const pp of selectedPackPaths) {
          if (norm.startsWith(pp + "/") || norm.startsWith(pp + "\\")) return true;
        }
        return false;
      };
      for (const key of this.selectedItems) {
        const meta = visible.get(key) || this.selectedMeta.get(key);
        const path = meta && meta.path || "";
        const isDir = (meta && typeof meta.is_dir === "boolean") ? meta.is_dir : this._selectionKeyIsDirectory(key, path);
        if (!isDir && !_isChildOfPack(path || key)) {
          standaloneFiles.add(this.normalizePathKey(path || key));
        }
      }
      const files = packEpisodes + standaloneFiles.size;
      return { packs, files, total: packs + files };
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
      let nestedEpisodes = 0;
      const addPreparedItem = (item) => {
        if (!item) return;
        const path = item.path || item.target_path || item.source_path || item.name || item.key;
        if (!path) return;
        const key = this.normalizePathKey(path);
        const isDir = typeof item.is_dir === "boolean" ? item.is_dir : this._selectionKeyIsDirectory(item.key || key, path);
        if (isDir) {
          if (this.isSeasonalPackItem(item)) {
            if (!seasonalPacks.has(key)) nestedEpisodes += Number(item.child_count || 0);
            seasonalPacks.add(key);
          }
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
      for (const entry of this.getSelectedActionEntries()) {
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
      return { singleFiles: singleFiles.size + nestedEpisodes, seasonalPacks: seasonalPacks.size };
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
      const internal = new Set(["external", "both"]);
      return Object.values(categoryMeta).filter((c2) => !internal.has(c2.id));
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
};
