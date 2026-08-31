// Auto-split from queue.js - verbatim methods bodies.
export default {
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
};
