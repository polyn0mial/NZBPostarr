// Auto-split from queue.js - verbatim methods bodies.
import { queueCategoryMeta as categoryMeta } from "page-base";
export default {
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

isItemFilepart(item) {
      return !!(item && item.has_filepart);
    },

isItemExcluded(item) {
      return item && item.excluded === true;
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

forceUploadExtChild(child, event) {
      const cat = this.getCategoryForItem(child);
      const overridden = { ...child, itype: this.resolveUploadItype(child.itype, cat) };
      this.openForceUploadMenu(overridden, event);
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

getCategoryStyleMeta(catId) {
      const palette = {
        cyan: { border: "#0e7490", background: "rgba(8, 145, 178, 0.16)", text: "#22d3ee" },
        purple: { border: "#7e22ce", background: "rgba(147, 51, 234, 0.16)", text: "#c084fc" },
        orange: { border: "#9a3412", background: "rgba(249, 115, 22, 0.16)", text: "#fb923c" },
        amber: { border: "#a16207", background: "rgba(245, 158, 11, 0.16)", text: "#fbbf24" },
        pink: { border: "#9d174d", background: "rgba(236, 72, 153, 0.16)", text: "#f472b6" },
        slate: { border: "#64748b", background: "rgba(148, 163, 184, 0.16)", text: "#e2e8f0" },
        zinc: { border: "#71717a", background: "rgba(161, 161, 170, 0.16)", text: "#e4e4e7" },
        green: { border: "#15803d", background: "rgba(34, 197, 94, 0.16)", text: "#4ade80" },
        emerald: { border: "#047857", background: "rgba(16, 185, 129, 0.16)", text: "#34d399" },
        blue: { border: "#2563eb", background: "rgba(59, 130, 246, 0.16)", text: "#60a5fa" },
        red: { border: "#991b1b", background: "rgba(239, 68, 68, 0.16)", text: "#f87171" },
        gray: { border: "#4b5563", background: "rgba(107, 114, 128, 0.16)", text: "#d1d5db" }
      };
      const raw = this.getCategoryColor(catId);
      const normalized = String(raw || "").trim().toLowerCase();
      if (normalized.startsWith("#")) {
        const hex = normalized;
        return {
          border: hex,
          background: `${hex}26`,
          text: hex
        };
      }
      return palette[normalized] || palette.gray;
    },

getCategorySwatchStyle(catId) {
      const style = this.getCategoryStyleMeta(catId);
      return {
        backgroundColor: style.background,
        border: `1px solid ${style.border}`,
        color: style.text,
        boxShadow: `inset 0 0 0 1px ${style.border}33`
      };
    },

getCategoryIconStyle(catId) {
      const style = this.getCategoryStyleMeta(catId);
      return {
        color: style.text
      };
    },

getCategoryBadgeStyle(catId) {
      const style = this.getCategoryStyleMeta(catId);
      return {
        backgroundColor: style.background,
        borderColor: style.border,
        color: style.text
      };
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

getExternalItemCategory(item) {
      const cat = this.getCategoryForItem(item);
      return cat && cat !== "external" ? cat : "";
    },

getUploadCategoryForItem(item) {
      if (this.isSelectionIgnored(item)) return "";
      if (this.isPackOnlyExternalChild(item)) return "";
      const cat = this.getCategoryForItem(item);
      return cat && cat !== "external" ? cat : "";
    },

getFolderStateTooltip(item) {
      if (!item || !item.is_dir) return "";
      if (this.isPackOnlyExternalChild(item)) {
        return "This nested folder inherits the parent pack category and is only uploadable through the parent pack.";
      }
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
      const inheritedExternalCategory = this.isExternalDescendantNode(item) ? normalizeCategory(this.findExternalAncestorCategory(item)) : "";
      if (inheritedExternalCategory && inheritedExternalCategory !== "external") return inheritedExternalCategory;
      const directManualCategory = item.key ? normalizeCategory(this.manualExternalCategories[item.key]) : "";
      if (directManualCategory && directManualCategory !== "external") return directManualCategory;
      if ((item.itype || "").toLowerCase() === "anime") return "anime";
      const detectedCategory = normalizeCategory(item.detected_category);
      if (detectedCategory && detectedCategory !== "external") return detectedCategory;
      const explicitCategory = normalizeCategory(item.category);
      if (explicitCategory && explicitCategory !== "external") return explicitCategory;
      const safeAssigned = normalizeCategory(item.assigned_category_safe);
      if (safeAssigned && safeAssigned !== "external") return safeAssigned;
      const resolvedExternal = item.key ? normalizeCategory(this._resolveExtCategory(item.key)) : "";
      if (resolvedExternal && resolvedExternal !== "external") return resolvedExternal;
      const itype = (item.itype || "").toLowerCase();
      if (itype === "external") {
        return detectedCategory || "";
      }
      if (itype.includes("season pack") || itype.includes("tv show")) return "tv";
      if (itype.includes("tv") || itype.includes("episode")) return "tv";
      if (itype.includes("movie")) return "movies";
      const itypeCategory = this.itypeToCategory(item.itype);
      if (itypeCategory) return itypeCategory;
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
};
