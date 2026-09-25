// Queue page: The staging area: stage rows, reorder and remove them, create jobs from them.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

export default {
  computed: {
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
  },
  methods: {
    // ============================================================
    //  Add Single Item to Queue
    // ============================================================
    async queueSingleItem(item, category) {
      if (this.isSelectionIgnored(item)) {
        this.showToast("warning", "Ignored", item.auto_select_reason || "This item is ignored for upload");
        return;
      }
      if (category === "external" && item && item.is_dir) {
        const selection = this.buildActionPayloadsFromEntries(this.getActionEntriesForItem(item, "external"));
        if (selection.uncategorizedExternal > 0) {
          this.showToast("warning", "Skipped", `${selection.uncategorizedExternal} item(s) need a category first`);
        }
        if (selection.items.length === 0) {
          this.showToast("warning", "No Valid Items", "No visible items from this folder are available to stage");
          return;
        }
        try {
          const res = await this.apiPost("/api/uploads/queue/items", { items: selection.items });
          if (res.added > 0) {
            this.showToast(
              "success",
              "Staged",
              `${res.added} visible item(s) from "${item.name}" added to staging`
            );
            this.loadQueuedPaths();
            this.loadQueueItems();
          } else {
            this.showToast("info", "Already Staged", `Visible items from "${item.name}" are already in staging`);
          }
        } catch (e2) {
          this.showToast("error", "Error", "Failed to add item to staging");
        }
        return;
      }
      let cat = category;
      if (cat === "external") {
        cat = this._resolveExtCategory(item.key);
      }
      if (!cat || cat === "external") {
        this.showToast("warning", "Category Required", "Select a category before queuing");
        return;
      }
      const payload = [{
        path: item.path,
        category: cat,
        itype: this.resolveUploadItype(item.itype, cat),
        name: item.name || (item.path || item.key).replace(/\\/g, "/").split("/").pop() || item.key,
        detected_category: item.detected_category || cat,
        detection_method: item.detection_method || "",
        selection_reason: item.auto_select_reason || ""
      }];
      try {
        const res = await this.apiPost("/api/uploads/queue/items", { items: payload });
        if (res.added > 0) {
          this.showToast("success", "Staged", `"${payload[0].name}" added to staging area`);
          this.loadQueuedPaths();
          this.loadQueueItems();
        } else {
          this.showToast("info", "Already Staged", `"${payload[0].name}" is already in staging`);
        }
      } catch (e2) {
        this.showToast("error", "Error", "Failed to add item to staging");
      }
    },

    // ============================================================
    //  Add to Queue (bulk)
    // ============================================================
    async addToQueue() {
      if (this.selectedItems.size === 0) return;
      const selection = this.buildSelectedActionPayloads();
      const items = selection.items.slice();
      const uncategorizedExternal = selection.uncategorizedExternal;
      console.info(
        `[QUEUE UI] staging selection visible=${selection.visibleSelectableCount} selected=${selection.selectedRawCount} to_stage=${items.length} excluded=${selection.excludedCount}`
      );
      if (uncategorizedExternal > 0) {
        this.showToast("warning", "Skipped", `${uncategorizedExternal} external item(s) skipped \u2014 select a category first`);
      }
      if (items.length === 0) {
        this.showToast("warning", "No Valid Items", "No visible selected items are available to stage");
        return;
      }
      items.sort((a2, b2) => {
        const getDirName = (path) => {
          const parts = path.split("/");
          const categoryIndex = parts.findIndex((p2) => p2.startsWith("0--"));
          return categoryIndex >= 0 ? parts.slice(categoryIndex + 1).join("/") : path;
        };
        return getDirName(a2.path).localeCompare(getDirName(b2.path));
      });
      try {
        const res = await this.apiPost("/api/uploads/queue/items", { items, bulk_selection: true });
        const added = res.added || 0;
        const total = res.total || 0;
        const skipped = items.length - added;
        if (added > 0) {
          this.showToast(
            "success",
            "Staged",
            `${added} item(s) added to staging` + (selection.excludedCount > 0 ? ` (${selection.excludedCount} excluded by filters)` : "") + (skipped > 0 ? ` (${skipped} already staged)` : "") + ` \u2014 ${total} total`
          );
        } else {
          this.showToast("info", "Already Staged", "All selected items are already in staging");
        }
        this.selectedItems = /* @__PURE__ */ new Set();
        this.selectedMeta = /* @__PURE__ */ new Map();
        this.loadQueuedPaths();
        this.loadQueueItems();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to add items to queue");
      }
    },

    // ============================================================
    //  Queue Item Loading & Management
    // ============================================================
    async loadQueueItems() {
      if (this._loadQueueItemsPromise) return this._loadQueueItemsPromise;
      this._loadQueueItemsPromise = (async () => {
        try {
          const data = await this.apiFetch("/api/uploads/queue/items");
          this.queueItems = data.items || [];
          this.loadQueuedPaths();
          this.$nextTick(() => this.initSortable());
        } catch (e2) {
          if (!e2.isOffline) {
            this.showToast("error", "Error", "Failed to load staging items");
          }
        } finally {
          this._loadQueueItemsPromise = null;
        }
      })();
      return this._loadQueueItemsPromise;
    },

    // ============================================================
    //  SortableJS - Drag-to-Reorder
    // ============================================================
    initSortable() {
      this.destroySortableInstance("_sortable");
      const container = this.$refs.sortableContainer;
      if (!container || this.queueItems.length === 0) return;
      this.createSortableInstance("_sortable", container, {
        handle: ".drag-handle",
        draggable: ".queue-group",
        animation: 200,
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        dragClass: "sortable-drag",
        onEnd: () => {
          const groupEls = container.querySelectorAll(".queue-group");
          const newIds = [];
          groupEls.forEach((groupEl) => {
            groupEl.querySelectorAll(".queue-item[data-id]").forEach((el2) => {
              newIds.push(parseInt(el2.dataset.id));
            });
          });
          const map = {};
          this.queueItems.forEach((q2) => {
            map[q2.id] = q2;
          });
          this.queueItems = newIds.filter((id2) => map[id2]).map((id2) => map[id2]);
          this.persistOrder();
        }
      });
    },

    async persistOrder() {
      try {
        const item_ids = this.queueItems.map((qi2) => qi2.id);
        await this.apiPut("/api/uploads/queue/items/reorder", { item_ids });
      } catch (e2) {
        this.showToast("error", "Error", "Failed to save queue order");
        await this.loadQueueItems();
      }
    },

    // ============================================================
    //  Queue Item Actions
    // ============================================================
    async removeItem(itemId) {
      if (!this._deletingIds) this._deletingIds = /* @__PURE__ */ new Set();
      if (this._deletingIds.has(itemId)) return;
      this._deletingIds.add(itemId);
      const idx = this.queueItems.findIndex((qi2) => qi2.id === itemId);
      const removed = idx >= 0 ? this.queueItems.splice(idx, 1)[0] : null;
      try {
        await this.apiFetch(`/api/uploads/queue/items/${itemId}`, { method: "DELETE" });
        clearTimeout(this._queuedPathsTimer);
        this._queuedPathsTimer = setTimeout(() => this.loadQueuedPaths(), 1500);
      } catch (e2) {
        if (removed && idx >= 0) {
          this.queueItems.splice(idx, 0, removed);
        }
        this.showToast("error", "Error", "Failed to remove item");
      } finally {
        this._deletingIds.delete(itemId);
      }
    },

    async clearQueueItems() {
      const ok = await this.confirmDialog(`Remove all ${this.queueItems.length} staged item(s)?`, {
        title: "Clear Staging Area",
        detail: "This only clears the staging area. Existing jobs are NOT removed.",
        danger: true,
        confirmLabel: "Clear Staging"
      });
      if (!ok) return;
      try {
        const res = await this.apiFetch("/api/uploads/queue/items/clear", { method: "POST" });
        this.queueItems = [];
        this.showToast("success", "Staging Cleared", `${res.cleared} item(s) removed from staging`);
        this.loadQueuedPaths();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to clear staging area");
      }
    },

    async createJobsFromStaging() {
      if (this.queueItems.length === 0) return;
      this.startingQueue = true;
      try {
        const res = await this.apiPost("/api/uploads/queue/start", {});
        const jobCount = res.jobs_created || 0;
        const startedItems = res.started_items || 0;
        const skippedItems = res.skipped_items || 0;
        const holdMsg = this.queueControl && this.queueControl.paused ? " Queue processing is paused, so jobs will wait until resumed." : " Processing will begin automatically.";
        const skippedMsg = skippedItems > 0 ? ` ${skippedItems} staged item(s) were skipped and left in staging.` : "";
        this.showToast(
          "success",
          "Jobs Created",
          `${jobCount} job(s) created from ${startedItems} staged item(s).${skippedMsg}${holdMsg}`
        );
        this.startTimeout(() => this.loadJobs(), 500);
        this.loadQueueItems();
        this.loadQueuedPaths();
      } catch (e2) {
        const detail = e2?.message || "Failed to create jobs from staging";
        this.showToast("error", "Error", detail);
      } finally {
        this.startingQueue = false;
      }
    },

    async previewStagingQueue() {
      if (this.queueItems.length === 0 || this.bulkPreviewLoading) return;
      this.bulkPreview = null;
      this.bulkPreviewRequest = {};
      this.bulkPreviewMode = "staging";
      this.bulkPreviewIndexerName = "all enabled indexers";
      this.bulkPreviewLoading = true;
      this.showBulkPreviewModal = true;
      try {
        this.bulkPreview = await this.apiPost("/api/uploads/queue/preview", {});
      } catch (e2) {
        this.closeBulkPreviewModal();
        this.showToast("error", "Preview Failed", e2?.message || "Failed to inspect the staged queue");
      } finally {
        this.bulkPreviewLoading = false;
      }
    },

    async startQueue() {
      await this.createJobsFromStaging();
    },

    async forceStartItem(item) {
      const idx = this.queueItems.findIndex((qi2) => qi2.id === item.id);
      const removed = idx >= 0 ? this.queueItems.splice(idx, 1)[0] : null;
      try {
        await this.apiFetch(`/api/uploads/queue/items/${item.id}/start`, { method: "POST" });
        this.showToast("success", "Started", `"${item.name}" is now uploading`);
        this.startTimeout(() => this.loadJobs(), 500);
        this.loadQueuedPaths();
      } catch (e2) {
        if (removed && idx >= 0) {
          this.queueItems.splice(idx, 0, removed);
        }
        this.showToast("error", "Error", "Failed to start item");
      }
    },

    isQueueSectionExpanded(sectionKey) {
      return this.queueSectionExpanded[sectionKey] !== false;
    },

    queueSectionClass(sectionKey) {
      return this.isQueueSectionExpanded(sectionKey) ? "section-content" : "section-content section-collapsed";
    },

    toggleQueueSection(sectionKey) {
      if (!sectionKey) return;
      this.queueSectionExpanded = {
        ...this.queueSectionExpanded,
        [sectionKey]: !this.isQueueSectionExpanded(sectionKey)
      };
      if (sectionKey === "pending") {
        try {
          localStorage.setItem("nzb_pending_expanded", String(this.queueSectionExpanded.pending !== false));
        } catch (_storageError) {
          // Storage full or disabled: keep the in-memory state.
        }
      }
    },
  },
};
