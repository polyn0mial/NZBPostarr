// Queue page: Force upload: the indexer menu, the bulk preview modal and the one force-upload request path.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

export default {
  computed: {
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
  },
  methods: {
    forceUploadExtChild(child, event) {
      const cat = this.getCategoryForItem(child);
      const overridden = { ...child, itype: this.resolveUploadItype(child.itype, cat) };
      this.openForceUploadMenu(overridden, event);
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

    closeForceUploadMenu() {
      this.flyoutOpen = false;
      this.flyoutTarget = null;
      this.forceSkipDupeCheck = false;
    },

    executeForceUpload(indexerId) {
      const target = this.flyoutTarget;
      const skipDupeCheck = this.forceSkipDupeCheck;
      this.closeForceUploadMenu();
      if (target === "bulk") {
        this._doForceUploadBulk(indexerId, skipDupeCheck);
      } else if (target && target.kind === "itemList") {
        this._doForceUploadItemList(
          target.items || [],
          indexerId,
          target.label || "selected items",
          skipDupeCheck
        );
      } else if (target) {
        this._doForceUploadSingle(target, indexerId, skipDupeCheck);
      }
    },

    // "Stop Job" toast actions for the jobs a force upload just started.
    _forceUploadStopActions(jobIds) {
      return jobIds.map((jid) => ({
        label: "Stop Job",
        icon: "square",
        callback: () => {
          this.apiPost(`/api/uploads/jobs/${jid}/stop`).then(() => {
            this.showToast("info", "Stopped", "Job stop signal sent");
          }).catch(() => {
          });
        }
      }));
    },

    async _doForceUploadSingle(item, indexerId, skipDupeCheck = false) {
      if (this.isSelectionIgnored(item)) {
        this.showToast("warning", "Ignored", item.auto_select_reason || "This item is ignored for upload");
        return;
      }
      const cat = this.getCategoryForItem(item);
      if (!cat || cat === "external") {
        this.showToast("warning", "Category Required", "Select a valid category before uploading");
        return;
      }
      const idxName = indexerId ? this.activeIndexers.find((i2) => i2.id === indexerId)?.name || indexerId : "all indexers";
      try {
        const res = await this.apiPost("/api/pending/force-upload", {
          items: [{
            path: item.path,
            category: cat,
            itype: this.resolveUploadItype(item.itype, cat),
            name: item.name || (item.path || item.key).replace(/\\/g, "/").split("/").pop() || item.key,
            detected_category: item.detected_category || cat,
            detection_method: item.detection_method || "",
            selection_reason: item.auto_select_reason || ""
          }],
          enable_duplicate_check: !skipDupeCheck,
          indexer_id: indexerId || null
        });
        const jobIds = res.job_ids || [];
        const stopActions = this._forceUploadStopActions(jobIds);
        this.showToast("success", "Upload Started", `Job started for "${item.name}" \u2192 ${idxName}`, 15e3, stopActions);
        this.startTimeout(() => this.loadJobs(), 500);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to start upload");
      }
    },

    async _doForceUploadBulk(indexerId, skipDupeCheck = false) {
      if (this.selectedItems.size === 0) return;
      const selection = this.buildSelectedActionPayloads();
      const items = selection.items;
      const uncategorizedExternal = selection.uncategorizedExternal;
      console.info(
        `[QUEUE UI] force-upload selection visible=${selection.visibleSelectableCount} selected=${selection.selectedRawCount} queued=${items.length} excluded=${selection.excludedCount}`
      );
      if (uncategorizedExternal > 0) {
        this.showToast("warning", "Skipped", `${uncategorizedExternal} external item(s) skipped \u2014 select a category first`);
      }
      if (items.length === 0) return;
      const idxName = indexerId ? this.activeIndexers.find((i2) => i2.id === indexerId)?.name || indexerId : "all indexers";
      const request = {
        items,
        enable_duplicate_check: !skipDupeCheck,
        indexer_id: indexerId || null,
        bulk_selection: true
      };
      try {
        const res = await this.apiPost("/api/pending/force-upload", request);
        const jobIds = res.job_ids || [];
        const stopActions = jobIds.map((jid) => ({
          label: "Stop Job",
          icon: "square",
          callback: () => this.apiPost(`/api/uploads/jobs/${jid}/stop`).catch(() => {})
        }));
        this.showToast(
          "success",
          "Upload Started",
          `${jobIds.length} job(s) started for ${res.items_count || items.length} item(s) → ${idxName}`,
          15e3,
          stopActions
        );
        this.selectedItems = new Set();
        this.selectedMeta = new Map();
        this.startTimeout(() => this.loadJobs(), 500);
      } catch (e2) {
        this.showToast("error", "Error", e2?.message || "Failed to start upload");
      }
    },

    closeBulkPreviewModal() {
      if (this.bulkPreviewStarting) return;
      this.showBulkPreviewModal = false;
      this.bulkPreview = null;
      this.bulkPreviewRequest = null;
      this.bulkPreviewMode = "bulk";
      this.bulkPreviewIndexerName = "";
      this.bulkPreviewLoading = false;
    },

    async confirmBulkPreviewUpload() {
      if (!this.bulkPreviewRequest || !this.bulkPreviewCanStart || this.bulkPreviewStarting) return;
      this.bulkPreviewStarting = true;
      try {
        const endpoint = this.bulkPreviewMode === "staging" ? "/api/uploads/queue/start" : "/api/pending/force-upload";
        const res = await this.apiPost(endpoint, this.bulkPreviewRequest);
        const jobIds = res.job_ids || [];
        const stopActions = this._forceUploadStopActions(jobIds);
        if (this.bulkPreviewMode === "staging") {
          const startedItems = res.started_items || 0;
          const skippedItems = res.skipped_items || 0;
          const holdMsg = this.queueControl && this.queueControl.paused ? " Queue processing is paused, so the job will wait until resumed." : " Processing will begin automatically.";
          const skippedMsg = skippedItems > 0 ? ` ${skippedItems} staged item(s) were skipped and left in staging.` : "";
          this.showToast("success", "Job Created", `${startedItems} staged item(s) queued.${skippedMsg}${holdMsg}`, 15e3, stopActions);
          this.loadQueueItems();
          this.loadQueuedPaths();
        } else {
          this.showToast(
            "success",
            "Upload Started",
            `${jobIds.length} job(s) started for ${res.items_count} planned item(s) \u2192 ${this.bulkPreviewIndexerName}`,
            15e3,
            stopActions
          );
          this.selectedItems = /* @__PURE__ */ new Set();
          this.selectedMeta = /* @__PURE__ */ new Map();
        }
        this.showBulkPreviewModal = false;
        this.bulkPreview = null;
        this.bulkPreviewRequest = null;
        this.bulkPreviewMode = "bulk";
        this.bulkPreviewIndexerName = "";
        this.startTimeout(() => this.loadJobs(), 500);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to start upload");
      } finally {
        this.bulkPreviewStarting = false;
      }
    },

    async _doForceUploadItemList(rawItems, indexerId, label, skipDupeCheck = false) {
      if (!Array.isArray(rawItems) || rawItems.length === 0) return;
      const items = rawItems.map((item) => ({
        path: item.path,
        category: this.getCategoryForItem(item) || item.category,
        manual_category: item.key ? this.manualExternalCategories[item.key] || "" : "",
        itype: this.resolveUploadItype(item.itype, this.getCategoryForItem(item) || item.category),
        name: item.name || (item.path || "").replace(/\\/g, "/").split("/").pop() || "",
        detected_category: item.detected_category || this.getCategoryForItem(item) || item.category,
        detection_method: item.detection_method || "",
        selection_reason: item.auto_select_reason || ""
      })).filter((item) => !!item.path && !!item.category && item.category !== "external");
      if (items.length === 0) {
        this.showToast("warning", "Category Required", "No actionable items with a valid category were found");
        return;
      }
      const idxName = indexerId ? this.activeIndexers.find((i2) => i2.id === indexerId)?.name || indexerId : "all indexers";
      try {
        const res = await this.apiPost("/api/pending/force-upload", {
          items,
          enable_duplicate_check: !skipDupeCheck,
          indexer_id: indexerId || null
        });
        const jobIds = res.job_ids || [];
        const stopActions = this._forceUploadStopActions(jobIds);
        this.showToast(
          "success",
          "Upload Started",
          `${jobIds.length} job(s) started for ${items.length} item(s) from "${label}" \u2192 ${idxName}`,
          15e3,
          stopActions
        );
        this.startTimeout(() => this.loadJobs(), 500);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to start upload");
      }
    },
  },
};
