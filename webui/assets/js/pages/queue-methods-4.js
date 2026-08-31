// Auto-split from queue.js - verbatim methods bodies.
export default {
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
        const stopActions = jobIds.map((jid) => ({
          label: "Stop Job",
          icon: "square",
          callback: () => {
            this.apiPost(`/api/uploads/jobs/${jid}/stop`).then(() => {
              this.showToast("info", "Stopped", "Job stop signal sent");
            }).catch(() => {
            });
          }
        }));
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
        const stopActions = jobIds.map((jid) => ({
          label: "Stop Job",
          icon: "square",
          callback: () => {
            this.apiPost(`/api/uploads/jobs/${jid}/stop`).then(() => {
              this.showToast("info", "Stopped", "Job stop signal sent");
            }).catch(() => {
            });
          }
        }));
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
        const stopActions = jobIds.map((jid) => ({
          label: "Stop Job",
          icon: "square",
          callback: () => {
            this.apiPost(`/api/uploads/jobs/${jid}/stop`).then(() => {
              this.showToast("info", "Stopped", "Job stop signal sent");
            }).catch(() => {
            });
          }
        }));
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

async loadJobs() {
      if (this._loadJobsPromise) return this._loadJobsPromise;
      this._loadJobsPromise = (async () => {
        try {
          const data = await this.apiFetch("/api/uploads/queue");
          this.running = data.running || [];
          this.queued = data.queued || [];
          this.finished = data.finished || [];
          this.counts = data.counts || { running: 0, queued: 0, finished: 0 };
          this.queueControl = data.control || { paused: false, active: null };
          this.jobQueueControlJobId = resolvePreferredJobId(this.jobQueueControlJobId, this.running, this.queued);
          maybeRevalidateQueuedJobs(this);
          syncActiveJobModal(this);
          syncQueuedJobModal(this);
        } catch (e2) {
          if (!e2.isOffline) {
            this.showToast("error", "Error", "Failed to load jobs");
          }
        } finally {
          this._loadJobsPromise = null;
        }
      })();
      return this._loadJobsPromise;
    },

async revalidateQueuedJobs() {
      try {
        const res = await this.apiPost("/api/uploads/queue/revalidate", { include_paused: true });
        const updated = Number((res == null ? void 0 : res.updated) || 0);
        const cancelled = Number((res == null ? void 0 : res.cancelled) || 0);
        const inspected = Number((res == null ? void 0 : res.inspected) || 0);
        const message = inspected > 0 ? `${inspected} job(s) checked, ${updated} updated, ${cancelled} cancelled` : "No queued jobs needed revalidation";
        this.showToast("success", "Queue Revalidated", message);
        await this.loadJobs();
      } catch (e2) {
        if (!e2.isOffline) {
          this._pendingQueuedJobRevalidationDone = false;
          this.showToast("error", "Queue Revalidation Failed", (e2 == null ? void 0 : e2.message) || "Unable to re-scan queued jobs");
        }
      }
    },

jobHasExplicitPaths(job) {
      if (!job) return false;
      if (job.has_explicit_paths === true) return true;
      return Array.isArray(job.target_paths) && job.target_paths.length > 0;
    },

async openActiveJobModal(job) {
      if (!job) return;
      this.activeJobModalJob = job;
      this.activeJobModalItems = [];
      this.activeJobModalSearch = "";
      this.showActiveJobModal = true;
      await this.loadActiveJobModalItems(job.job_id);
    },

async loadActiveJobModalItems(jobId, silent = false) {
      if (this._loadActiveJobItemsPromise) {
        if (this._activeJobItemsRequestJobId === jobId) return this._loadActiveJobItemsPromise;
        await this._loadActiveJobItemsPromise;
      }
      if (!silent) this.activeJobModalLoading = true;
      this._activeJobItemsRequestJobId = jobId;
      this._loadActiveJobItemsPromise = (async () => {
        try {
          const res = await this.apiFetch(`/api/uploads/queue/${jobId}/active-items`);
          if (this.activeJobModalJob && this.activeJobModalJob.job_id === jobId) {
            this.activeJobModalItems = res.items || [];
            this.$nextTick(() => this.initActiveJobModalSortable());
          }
        } catch (e2) {
          if (!e2.isOffline && !silent) {
            this.showToast("error", "Error", "Failed to load active job items");
          }
        } finally {
          this.activeJobModalLoading = false;
          this._loadActiveJobItemsPromise = null;
          this._activeJobItemsRequestJobId = null;
        }
      })();
      return this._loadActiveJobItemsPromise;
    },

closeActiveJobModal() {
      this.destroyActiveJobModalSortable();
      this.showActiveJobModal = false;
      this.activeJobModalJob = null;
      this.activeJobModalItems = [];
      this.activeJobModalSearch = "";
      this.activeJobModalLoading = false;
      this.activeJobModalSaving = false;
    },

async openCompletedJobModal(job) {
      this.completedJobModalJob = job;
      this.completedJobModalItems = [];
      this.completedJobModalSearch = "";
      this.completedJobModalLoading = true;
      try {
        const response = await this.apiFetch(`/api/uploads/queue/${job.job_id}/completed-items`);
        if (this.completedJobModalJob && this.completedJobModalJob.job_id === job.job_id) {
          this.completedJobModalItems = response.items || [];
        }
      } catch (error) {
        if (!error.isOffline) {
          this.showToast("error", "Error", "Failed to load completed job items");
        }
      } finally {
        this.completedJobModalLoading = false;
      }
    },

closeCompletedJobModal() {
      this.completedJobModalJob = null;
      this.completedJobModalItems = [];
      this.completedJobModalSearch = "";
      this.completedJobModalLoading = false;
    },

activeJobHasInspectableItems(job) {
      return this.jobHasExplicitPaths(job) || !!job?.current_item;
    },

isJobQueueActiveEntry(job) {
      return !!job && job._queueEntryType === "active";
    },

openJobQueueEntry(job) {
      if (!job) return;
      this.jobQueueControlJobId = job.job_id;
      if (this.isJobQueueActiveEntry(job)) {
        if (this.activeJobHasInspectableItems(job)) {
          this.openActiveJobModal(job);
        }
        return;
      }
      this.openQueuedJobModal(job);
    },

editJobQueueEntry(job) {
      if (!job) return;
      if (this.isJobQueueActiveEntry(job)) {
        if (this.activeJobHasInspectableItems(job)) {
          this.openActiveJobModal(job);
        }
        return;
      }
      this.openQueuedJobModal(job);
    },

jobQueueStatusLabel(job) {
      if (!job) return "Queued";
      if (this.isJobStopping(job)) return "Stopping";
      if (this.isJobPausing(job)) return "Pausing";
      if (this.isJobPaused(job)) return "Paused";
      if (job.status === "stopped") return "Stopped";
      if (this.isJobQueueActiveEntry(job)) return "Running";
      return "Queued";
    },

jobQueueStatusBadgeClass(job) {
      if (!job) return "bg-notion-bg-hover text-notion-text-secondary";
      if (this.isJobStopping(job)) return "bg-notion-error/15 text-notion-error";
      if (this.isJobPaused(job)) return "bg-notion-warning/15 text-notion-warning";
      if (job.status === "stopped") return "bg-notion-warning/15 text-notion-warning";
      if (this.isJobQueueActiveEntry(job)) return "bg-notion-accent/10 text-notion-accent";
      return "bg-notion-bg-hover text-notion-text-secondary";
    },

jobQueuePromoteTitle() {
      return this.running.length > 0 ? "Move to run next after the current job finishes" : "Move to front of queue";
    },

jobQueueControlOptionLabel(job) {
      if (!job) return "Job";
      const prefix = this.isJobQueueActiveEntry(job) ? "Now" : `#${job._queuePosition}`;
      return `${prefix} - ${this.jobDisplayName(job)}`;
    },

destroyActiveJobModalSortable() {
      this.destroySortableInstance("_activeJobModalSortable");
    },

initActiveJobModalSortable() {
      this.destroyActiveJobModalSortable();
      if (!this.showActiveJobModal || this.activeJobModalSaving) return;
      if ((this.activeJobModalSearch || "").trim()) return;
      if (!this.activeJobModalCanReorder) return;
      if (this.activeJobModalItems.length < 2) return;
      const container = this.$refs.activeJobModalSortableContainer;
      if (!container) return;
      this.createSortableInstance("_activeJobModalSortable", container, {
        handle: ".active-job-drag-handle",
        draggable: ".active-job-row",
        animation: 180,
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        dragClass: "sortable-drag",
        onEnd: async () => {
          const rowEls = container.querySelectorAll(".active-job-row[data-path]");
          const byPath = new Map(this.activeJobModalItems.map((item) => [item.path, item]));
          const reordered = [];
          rowEls.forEach((el2) => {
            const path = el2.dataset.path;
            if (path && byPath.has(path)) reordered.push(byPath.get(path));
          });
          if (reordered.length !== this.activeJobModalItems.length) return;
          this.activeJobModalItems = reordered.map((item, i2) => ({ ...item, index: i2 + 1 }));
          await this.persistActiveJobModalOrder(false);
        }
      });
    },

async persistActiveJobModalOrder(showSuccessToast = true) {
      if (!this.activeJobModalJob) return;
      if (!this.activeJobModalCanReorder) return;
      const paths = this.activeJobModalItems.map((item) => item.path);
      this.activeJobModalSaving = true;
      try {
        await this.apiPut(`/api/uploads/queue/${this.activeJobModalJob.job_id}/active-items/reorder`, { paths });
        if (showSuccessToast) {
          this.showToast("success", "Saved", "Active job order updated");
        }
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to save active job order");
        await this.loadJobs();
      } finally {
        this.activeJobModalSaving = false;
        this.$nextTick(() => this.initActiveJobModalSortable());
      }
    },

async removeActiveJobModalItem(path) {
      if (!this.activeJobModalJob || !path || this.activeJobModalSaving) return;
      const prevItems = [...this.activeJobModalItems];
      const normalized = String(path || "").trim();
      if (!normalized) return;
      this.activeJobModalItems = this.activeJobModalItems.filter((item) => item.path !== normalized).map((item, i2) => ({ ...item, index: i2 + 1 }));
      this.activeJobModalSaving = true;
      this.$nextTick(() => this.initActiveJobModalSortable());
      try {
        await this.apiFetch(`/api/uploads/queue/${this.activeJobModalJob.job_id}/active-items`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: normalized })
        });
        this.showToast("success", "Saved", "Active job item removed");
        await this.loadJobs();
      } catch (e2) {
        this.activeJobModalItems = prevItems;
        this.$nextTick(() => this.initActiveJobModalSortable());
        this.showToast("error", "Error", "Failed to remove item from active job");
      } finally {
        this.activeJobModalSaving = false;
        this.$nextTick(() => this.initActiveJobModalSortable());
      }
    },

async openQueuedJobModal(job) {
      this.showQueuedJobModal = true;
      this.queuedJobModalJob = job;
      this.queuedJobModalSearch = "";
      this.queuedJobModalName = job.display_name || "";
      this.hydrateQueuedJobSchedule(job.run_after || null);
      await this.loadQueuedJobModalItems(job.job_id);
      this.$nextTick(() => this.initQueuedJobModalSortable());
    },

closeQueuedJobModal() {
      this.destroyQueuedJobModalSortable();
      this.showQueuedJobModal = false;
      this.queuedJobModalJob = null;
      this.queuedJobModalItems = [];
      this.queuedJobModalSearch = "";
      this.queuedJobModalName = "";
      this.queuedJobModalDate = "";
      this.queuedJobModalHasTime = false;
      this.queuedJobModalTime = "12:00";
      this.queuedJobModalLoading = false;
      this.queuedJobModalSaving = false;
      this.queuedJobModalRenaming = false;
      this.queuedJobModalScheduling = false;
    },

hydrateQueuedJobSchedule(runAfter) {
      if (!runAfter) {
        this.queuedJobModalDate = "";
        this.queuedJobModalHasTime = false;
        this.queuedJobModalTime = "12:00";
        return;
      }
      const dt2 = new Date(runAfter);
      if (Number.isNaN(dt2.getTime())) {
        this.queuedJobModalDate = "";
        this.queuedJobModalHasTime = false;
        this.queuedJobModalTime = "12:00";
        return;
      }
      const yyyy = dt2.getFullYear();
      const mm = String(dt2.getMonth() + 1).padStart(2, "0");
      const dd2 = String(dt2.getDate()).padStart(2, "0");
      const hh = String(dt2.getHours()).padStart(2, "0");
      const min2 = String(dt2.getMinutes()).padStart(2, "0");
      this.queuedJobModalDate = `${yyyy}-${mm}-${dd2}`;
      this.queuedJobModalTime = `${hh}:${min2}`;
      this.queuedJobModalHasTime = !(hh === "12" && min2 === "00");
    },

buildQueuedJobRunAfterIso() {
      const date = (this.queuedJobModalDate || "").trim();
      if (!date) return null;
      const candidateTime = (this.queuedJobModalTime || "").trim();
      const time = this.queuedJobModalHasTime && candidateTime ? candidateTime : "12:00";
      const parsed = /* @__PURE__ */ new Date(`${date}T${time}:00`);
      if (Number.isNaN(parsed.getTime())) {
        return null;
      }
      return parsed.toISOString();
    },
};
