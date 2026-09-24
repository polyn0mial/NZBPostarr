// Auto-split from queue.js - verbatim methods bodies.
export default {
scheduleEpoch(runAfter) {
      if (!runAfter) return null;
      const dt2 = new Date(runAfter);
      if (Number.isNaN(dt2.getTime())) return null;
      return dt2.getTime();
    },

formatRunAfterLocal(runAfter) {
      if (!runAfter) return "";
      const dt2 = new Date(runAfter);
      if (Number.isNaN(dt2.getTime())) return "Scheduled";
      return dt2.toLocaleString([], {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit"
      });
    },

async saveQueuedJobSchedule() {
      if (!this.queuedJobModalJob || !this.queuedJobModalScheduleDirty) return;
      if (this.queuedJobModalDate && !this.buildQueuedJobRunAfterIso()) {
        this.showToast("error", "Invalid date/time", "Please choose a valid date and optional time.");
        return;
      }
      this.queuedJobModalScheduling = true;
      const runAfter = this.buildQueuedJobRunAfterIso();
      try {
        const res = await this.apiFetch(`/api/uploads/queue/${this.queuedJobModalJob.job_id}/schedule`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_after: runAfter || null })
        });
        const updatedRunAfter = res && res.run_after ? res.run_after : null;
        this.queuedJobModalJob = {
          ...this.queuedJobModalJob,
          run_after: updatedRunAfter
        };
        this.hydrateQueuedJobSchedule(updatedRunAfter);
        await this.loadJobs();
        this.showToast("success", "Saved", updatedRunAfter ? "Deferred schedule updated" : "Deferred schedule cleared");
      } catch (e2) {
        const detail = e2?.message || "Failed to update deferred schedule";
        this.showToast("error", "Error", detail);
      } finally {
        this.queuedJobModalScheduling = false;
      }
    },

async clearQueuedJobSchedule() {
      if (!this.queuedJobModalJob) return;
      this.queuedJobModalDate = "";
      this.queuedJobModalHasTime = false;
      this.queuedJobModalTime = "12:00";
      await this.saveQueuedJobSchedule();
    },

async saveQueuedJobName() {
      if (!this.queuedJobModalJob || !this.queuedJobModalNameDirty) return;
      this.queuedJobModalRenaming = true;
      const name = (this.queuedJobModalName || "").trim();
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${this.queuedJobModalJob.job_id}/name`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name || null })
        });
        const updatedName = res && res.display_name ? res.display_name : "";
        this.queuedJobModalName = updatedName;
        this.queuedJobModalJob = {
          ...this.queuedJobModalJob,
          display_name: updatedName || null
        };
        await this.loadJobs();
        this.showToast("success", "Saved", updatedName ? "Job name updated" : "Custom job name cleared");
      } catch (e2) {
        this.showToast("error", "Error", "Failed to update job name");
      } finally {
        this.queuedJobModalRenaming = false;
      }
    },

async clearQueuedJobName() {
      if (!this.queuedJobModalJob) return;
      this.queuedJobModalName = "";
      await this.saveQueuedJobName();
    },

async loadQueuedJobModalItems(jobId) {
      if (this._queuedJobLoadTimer) {
        clearTimeout(this._queuedJobLoadTimer);
      }
      const cached = this._queuedJobItemsCache[jobId];
      if (cached && Date.now() - cached.ts < 3e5) {
        this.queuedJobModalItems = cached.items;
        return;
      }
      this._queuedJobLoadTimer = setTimeout(async () => {
        this.queuedJobModalLoading = true;
        try {
          const data = await this.apiFetch(`/api/uploads/queue/${jobId}/items`);
          const items = data.items || [];
          this.queuedJobModalItems = items;
          this._queuedJobItemsCache[jobId] = { items, ts: Date.now() };
        } catch (e2) {
          this.showToast("error", "Error", "Failed to load queued job items");
          this.closeQueuedJobModal();
        } finally {
          this.queuedJobModalLoading = false;
          this.$nextTick(() => this.initQueuedJobModalSortable());
        }
      }, 200);
    },

destroyQueuedJobModalSortable() {
      this.destroySortableInstance("_queuedJobModalSortable");
    },

initQueuedJobModalSortable() {
      this.destroyQueuedJobModalSortable();
      if (!this.showQueuedJobModal || this.queuedJobModalLoading) return;
      if ((this.queuedJobModalSearch || "").trim()) return;
      if (this.queuedJobModalItems.length < 2) return;
      const container = this.$refs.queuedJobModalSortableContainer;
      if (!container) return;
      this.createSortableInstance("_queuedJobModalSortable", container, {
        handle: ".queued-job-drag-handle",
        draggable: ".queued-job-row",
        animation: 180,
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        dragClass: "sortable-drag",
        onEnd: async () => {
          const rowEls = container.querySelectorAll(".queued-job-row[data-path]");
          const byPath = new Map(this.queuedJobModalItems.map((item) => [item.path, item]));
          const reordered = [];
          rowEls.forEach((el2) => {
            const path = el2.dataset.path;
            if (path && byPath.has(path)) reordered.push(byPath.get(path));
          });
          if (reordered.length !== this.queuedJobModalItems.length) return;
          this.queuedJobModalItems = reordered.map((item, i2) => ({ ...item, index: i2 + 1 }));
          await this.persistQueuedJobModalOrder(false);
        }
      });
    },

async persistQueuedJobModalOrder(showSuccessToast = true) {
      if (!this.queuedJobModalJob) return;
      const paths = this.queuedJobModalItems.map((item) => item.path);
      this.queuedJobModalSaving = true;
      try {
        await this.apiPut(`/api/uploads/queue/${this.queuedJobModalJob.job_id}/items/reorder`, { paths });
        if (showSuccessToast) {
          this.showToast("success", "Saved", "Queued job order updated");
        }
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to save queued job order");
        await this.loadQueuedJobModalItems(this.queuedJobModalJob.job_id);
      } finally {
        this.queuedJobModalSaving = false;
      }
    },

async removeQueuedJobModalItem(path) {
      if (!this.queuedJobModalJob) return;
      const jobId = this.queuedJobModalJob.job_id;
      const prev = [...this.queuedJobModalItems];
      this.queuedJobModalItems = this.queuedJobModalItems.filter((item) => item.path !== path).map((item, i2) => ({ ...item, index: i2 + 1 }));
      this.$nextTick(() => this.initQueuedJobModalSortable());
      try {
        await this.apiFetch(`/api/uploads/queue/${jobId}/items`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path })
        });
        await this.loadJobs();
        if (this.queuedJobModalItems.length === 0) {
          this.closeQueuedJobModal();
        }
      } catch (e2) {
        this.queuedJobModalItems = prev;
        this.$nextTick(() => this.initQueuedJobModalSortable());
        this.showToast("error", "Error", "Failed to remove item from queued job");
      }
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
        const res = await this.apiFetch(`/api/uploads/queue/items/${item.id}/start`, { method: "POST" });
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

// ============================================================
    //  Queue Display Helpers
    // ============================================================
    categoryBadgeClass(cat) {
      const map = {
        tv: "bg-cyan-500/15 text-cyan-400",
        movies: "bg-purple-500/15 text-purple-400",
        anime: "bg-pink-500/15 text-pink-400",
        disc: "bg-[#E0E0E0] text-[#2A2A2A] border-[#B9B9B9]",
        books: "bg-emerald-500/15 text-emerald-400",
        ebooks: "bg-emerald-500/15 text-emerald-400",
        audiobooks: "bg-orange-500/15 text-orange-400",
        music: "bg-blue-500/15 text-blue-400",
        apps: "bg-red-500/15 text-red-400",
        misc: "bg-orange-500/15 text-orange-400"
      };
      return map[cat] || "bg-notion-bg-hover text-notion-text-tertiary";
    },

categorySelectWidthClass(cat) {
      const map = {
        tv: "w-[3.4rem]",
        disc: "w-[4.2rem]",
        apps: "w-[4.2rem]",
        misc: "w-[4.3rem]",
        music: "w-[4.6rem]",
        anime: "w-[4.7rem]",
        books: "w-[4.7rem]",
        movies: "w-[4.9rem]",
        ebooks: "w-[4.9rem]",
        audiobooks: "w-[6.2rem]"
      };
      return map[cat] || "w-[4.9rem]";
    },

jobScheduleLabel(job) {
      if (!job || !job.run_after) return "";
      return this.formatRunAfterLocal(job.run_after);
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
        } catch (_e2) {
        }
      }
    },

async renameJobInline(job) {
      if (!job || !job.job_id) return;
      const currentName = (job.display_name || "").trim();
      const nextName = await this.promptDialog(`Set a custom name for job ${job.job_id}.`, {
        title: "Rename Job",
        detail: "Leave blank to clear the custom name.",
        icon: "pencil",
        value: currentName,
        placeholder: "e.g. Weekend TV batch",
        confirmLabel: "Save Name"
      });
      if (nextName === null) return;
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${job.job_id}/name`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: nextName.trim() || null })
        });
        const updatedName = res && res.display_name ? res.display_name : "";
        if (this.queuedJobModalJob && this.queuedJobModalJob.job_id === job.job_id) {
          this.queuedJobModalJob = {
            ...this.queuedJobModalJob,
            display_name: updatedName || null
          };
          this.queuedJobModalName = updatedName;
        }
        await this.loadJobs();
        this.showToast("success", "Saved", updatedName ? "Job name updated" : "Custom job name cleared");
      } catch (e2) {
        this.showToast("error", "Error", "Failed to update job name");
      }
    },

async _resolveScheduleRunAfter(job, currentDate, currentTime) {
      const dateText = await this.promptDialog(`Set a deferred date for job ${job.job_id}.`, {
        title: "Schedule Job",
        detail: "Leave blank to run the job immediately.",
        icon: "calendar-clock",
        value: currentDate,
        placeholder: "YYYY-MM-DD",
        inputType: "date",
        confirmLabel: "Continue"
      });
      if (dateText === null) return void 0;
      const date = dateText.trim();
      if (!date) return null;

      const useTime = await this.confirmDialog("Set a specific time for this job?", {
        title: "Schedule Time",
        detail: `Choose "Use 12:00 PM" to run at noon on ${date}.`,
        confirmLabel: "Pick a Time",
        cancelLabel: "Use 12:00 PM"
      });
      let time = "12:00";
      if (useTime) {
        const timeText = await this.promptDialog(`Time to start job ${job.job_id} on ${date}.`, {
          title: "Schedule Time",
          detail: "24-hour format (HH:MM).",
          icon: "clock",
          value: currentTime,
          placeholder: "HH:MM",
          inputType: "time",
          confirmLabel: "Set Time"
        });
        if (timeText === null) return void 0;
        time = timeText.trim() || "12:00";
      }
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
        this.showToast("error", "Invalid date", "Use YYYY-MM-DD format.");
        return void 0;
      }
      if (!/^\d{2}:\d{2}$/.test(time)) {
        this.showToast("error", "Invalid time", "Use HH:MM format (24-hour).");
        return void 0;
      }
      const dt2 = /* @__PURE__ */ new Date(`${date}T${time}:00`);
      if (Number.isNaN(dt2.getTime())) {
        this.showToast("error", "Invalid date/time", "Please enter a valid date and optional time.");
        return void 0;
      }
      return dt2.toISOString();
    },

async scheduleJobInline(job) {
      if (!job || !job.job_id) return;
      const current = job.run_after ? new Date(job.run_after) : null;
      const hasCurrent = !!(current && !Number.isNaN(current.getTime()));
      const currentDate = hasCurrent ? `${current.getFullYear()}-${String(current.getMonth() + 1).padStart(2, "0")}-${String(current.getDate()).padStart(2, "0")}` : "";
      const currentTime = hasCurrent ? `${String(current.getHours()).padStart(2, "0")}:${String(current.getMinutes()).padStart(2, "0")}` : "12:00";
      const runAfter = await this._resolveScheduleRunAfter(job, currentDate, currentTime);
      if (runAfter === void 0) return;
      try {
        const res = await this.apiFetch(`/api/uploads/queue/${job.job_id}/schedule`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_after: runAfter })
        });
        const updatedRunAfter = res && res.run_after ? res.run_after : null;
        if (this.queuedJobModalJob && this.queuedJobModalJob.job_id === job.job_id) {
          this.queuedJobModalJob = {
            ...this.queuedJobModalJob,
            run_after: updatedRunAfter
          };
          this.hydrateQueuedJobSchedule(updatedRunAfter);
        }
        await this.loadJobs();
        this.showToast(
          "success",
          "Saved",
          updatedRunAfter ? `Deferred to ${this.formatRunAfterLocal(updatedRunAfter)}` : "Job set to Immediate"
        );
      } catch (e2) {
        const detail = e2?.message || "Failed to update deferred schedule";
        this.showToast("error", "Error", detail);
      }
    },

finishedStatusBg(job) {
      const cfg = this.getStatusConfig(job.status);
      return `${cfg.bg}`;
    },

finishedStatusBadge(job) {
      const map = {
        completed: "bg-green-500/15 text-notion-success",
        failed: "bg-red-500/15 text-notion-error",
        stopped: "bg-yellow-500/15 text-notion-warning",
        cancelled: "bg-yellow-500/15 text-notion-warning"
      };
      return map[job.status] || "bg-notion-bg-hover text-notion-text-tertiary";
    },

recentJobEvents(job, limit = 3) {
      if (!job || !Array.isArray(job.events)) return [];
      return job.events.slice(-Math.max(1, Number(limit) || 3)).reverse();
    },

// ============================================================
    //  Job Actions
    // ============================================================
    isJobPaused(job) {
      return job && (job.status === "paused" || !!job.pause_requested);
    },

isJobPausing(job) {
      return job && job.status === "running" && !!job.pause_requested;
    },

isJobStopping(job) {
      return job && job.status === "stopping";
    },

isJobActionPending(jobId) {
      return !!jobId && this.pendingJobActions.has(jobId);
    },

setJobActionPending(jobId, pending) {
      const next = new Set(this.pendingJobActions);
      if (pending) next.add(jobId);
      else next.delete(jobId);
      this.pendingJobActions = next;
    },

async pauseJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/pause`, { method: "POST" });
        this.showToast("info", res.status === "paused" ? "Paused" : "Pause Requested", res.message || "Pause requested");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to pause job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },

async resumeJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/resume`, { method: "POST" });
        this.showToast("success", "Resumed", res.message || "Job resumed");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to resume job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },

async retryJob(job) {
      const jobId = job && job.job_id;
      if (!jobId || this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/retry`, { method: "POST" });
        this.showToast("success", "Retry Queued", res.message || `Retry queued as ${res.job_id}`);
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Retry Failed", e2?.message || "Failed to queue retry");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },

async stopJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/stop`, { method: "POST" });
        this.showToast("info", "Stopping", res.message || "Stop signal sent");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to stop job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },

async stopAndClearJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      const ok = await this.confirmDialog("Stop this active job and remove it from the queue view?", {
        title: "Stop And Clear Job",
        detail: "The backend will still kill any active tool processes and clean temp files.",
        danger: true,
        confirmLabel: "Stop And Clear"
      });
      if (!ok) return;
      this.setJobActionPending(jobId, true);
      try {
        const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/stop-clear`, { method: "POST" });
        this.running = this.running.filter((j2) => j2.job_id !== jobId);
        this.queued = this.queued.filter((j2) => j2.job_id !== jobId);
        this.counts.running = this.running.length;
        this.counts.queued = this.queued.length;
        this.showToast("warning", "Clearing", res.message || "Job stopping and clearing");
        this.startTimeout(() => this.loadJobs(), 600);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to stop and clear job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },

async pauseQueueProcessing() {
      try {
        const res = await this.apiFetch("/api/uploads/queue/pause", { method: "POST" });
        this.showToast("info", "Queue Paused", res.message || "Queue processing paused");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to pause queue processing");
      }
    },

async resumeQueueProcessing() {
      try {
        const res = await this.apiFetch("/api/uploads/queue/resume", { method: "POST" });
        this.showToast("success", "Queue Resumed", res.message || "Queue processing resumed");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to resume queue processing");
      }
    },

async stopQueueProcessing() {
      const ok = await this.confirmDialog("Stop the active job and pause queue processing?", {
        title: "Stop Queue Processing",
        detail: "Queued jobs will remain in the job queue.",
        danger: true,
        confirmLabel: "Stop Queue"
      });
      if (!ok) return;
      try {
        const res = await this.apiFetch("/api/uploads/queue/stop", { method: "POST" });
        this.showToast("warning", "Queue Stopping", res.message || "Queue processing stopping");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to stop queue processing");
      }
    },
};
