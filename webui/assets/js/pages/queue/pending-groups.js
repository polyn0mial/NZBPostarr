// Queue page: External folder groups: keys, order, collapse state and group-level actions.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

export default {
  computed: {
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
  },
  methods: {
    normalizePathKey(path) {
      return (path || "").replace(/\\/g, "/").replace(/\/$/, "").toLowerCase();
    },

    itemPathKey(item) {
      const raw = item && (item.path || item.key || item.name) ? (item.path || item.key || item.name) : "";
      return this.normalizePathKey(raw);
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
  },
};
