// Queue page: Row selection and the action payloads built from it (stage, force upload, mark uploaded).
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.
import { selectionPathIsDirectory, isMultiSeasonRange, dedupeSelectionEntries } from "./selection.logic.js";

export default {
  computed: {
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
  },
  methods: {
    getSelectionTitle(item) {
      if (!item) return "";
      if (this.isPackOnlyExternalChild(item)) {
        return "This item inherits the parent pack category and can only be uploaded through the parent pack.";
      }
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
      for (const group of this.items.external || []) {
        for (const item of group.items || []) {
          this.visitVisibleExternalNodes(item, (node) => {
            visible.set(node.key, this._buildSelectionMeta(node));
          });
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
        is_dir: typeof item.is_dir === "boolean" ? item.is_dir : null,
        child_count: Number(item.child_count || 0),
        manual_category: item.key ? this.manualExternalCategories[item.key] || "" : "",
        detected_category: item.detected_category || category || "",
        detection_method: item.detection_method || "",
        detection_flags: item.detection_flags || [],
        detection_override: item.detection_override || "",
        selection_reason: item.auto_select_reason || ""
      };
    },

    _selectionKeyIsDirectory(key, fallbackPath = "") {
      return selectionPathIsDirectory(key, this.selectedMeta.get(key), fallbackPath);
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
      return isMultiSeasonRange(item);
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
        nodes.push(node);
      });
      return nodes;
    },

    _dedupeSelectionEntries(entries) {
      return dedupeSelectionEntries(entries);
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
      const itemSelected = this.selectedItems.has(item.key);
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
      (this.items.external || []).forEach((group) => {
        (group.items || []).forEach((item) => {
          entries.push(...this.collectSelectedExternalActionEntries(item));
        });
      });
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

    // ============================================================
    //  Expand / Collapse
    // ============================================================
    async toggleExtItem(key) {
      if (!key) return;
      const next = !this.expandedExtItems[key];
      if (typeof this.$set === "function") {
        this.$set(this.expandedExtItems, key, next);
      } else {
        this.expandedExtItems[key] = next;
      }
      if (next) {
        const item = this._findCurrentPendingNode(key);
        if (item && item.is_dir && this._getLoadedRowChildren(item).length === 0 && Number(item.child_count || 0) > 0) {
          try {
            await this.ensureExtChildrenLoaded(item);
          } catch (e2) {
            console.error("Failed to hydrate child items for expanded row", e2);
          }
        }
      }
    },

    // ============================================================
    //  External Folder ↔ Children Selection
    // ============================================================
    async toggleExtFolderSelection(item, checked) {
      if (checked) {
        await this._ensureExtSubtreeLoaded(item);
        item = this._findCurrentPendingNode(item.key, item.path) || item;
      }
      const nextSet = new Set(this.selectedItems);
      const visitItem = (node) => {
        if (!node || !node.key || !this.extItemPassesFilters(node)) return;
        if (checked) {
          nextSet.add(node.key);
        } else {
          nextSet.delete(node.key);
        }
        (node.children || []).forEach(visitItem);
      };
      (item.children || []).forEach(visitItem);
      if (checked) {
        nextSet.add(item.key);
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

    async toggleExtChildSelection(child, _parentItem, checked) {
      if (checked) {
        await this._ensureExtSubtreeLoaded(child);
        child = this._findCurrentPendingNode(child.key, child.path) || child;
      }
      const nextSet = new Set(this.selectedItems);
      const visitItem = (node) => {
        if (!node || !node.key || !this.extItemPassesFilters(node)) return;
        if (checked) {
          nextSet.add(node.key);
        } else {
          nextSet.delete(node.key);
        }
        (node.children || []).forEach(visitItem);
      };
      visitItem(child);
      this.selectedItems = nextSet;
    },

    // ============================================================
    //  Selection
    // ============================================================
    toggleItemSelection(item, checked) {
      if (checked) {
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

    async toggleExtGroupSelection(groupOrIdx, checked) {
      const initialGroup = typeof groupOrIdx === "number" ? (this.items.external || [])[groupOrIdx] : groupOrIdx;
      if (!initialGroup || !initialGroup.items) return;
      const groupKey = this.externalGroupKey(initialGroup);
      if (checked) {
        for (const item of initialGroup.items) {
          await this._ensureExtSubtreeLoaded(item);
        }
      }
      const group = (this.items.external || []).find((g2) => this.externalGroupKey(g2) === groupKey) || initialGroup;
      if (!group || !group.items) return;
      const nextSet = new Set(this.selectedItems);
      group.items.forEach((item) => {
        const visit = (node) => {
          if (!node || !node.key || !this.extItemPassesFilters(node)) return;
          if (checked) {
            nextSet.add(node.key);
          } else {
            nextSet.delete(node.key);
          }
          (node.children || []).forEach(visit);
        };
        visit(item);
      });
      this.selectedItems = nextSet;
    },

    areAllExtGroupSelected(groupOrIdx) {
      const group = typeof groupOrIdx === "number" ? (this.items.external || [])[groupOrIdx] : groupOrIdx;
      if (!group || !group.items || group.items.length === 0) return false;
      const selectable = [];
      group.items.forEach((item) => {
        selectable.push(...this.collectVisibleSelectableExternalNodes(item));
      });
      if (selectable.length === 0) return false;
      return selectable.every((item) => this.selectedItems.has(item.key));
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
  },
};
