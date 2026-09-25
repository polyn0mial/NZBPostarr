// Queue page: A queued job's modal: name, schedule, items, reorder and remove.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

export default {
  computed: {
    queuedJobModalFilteredItems() {
      const q2 = (this.queuedJobModalSearch || "").trim().toLowerCase();
      if (!q2) return this.queuedJobModalItems;
      return this.queuedJobModalItems.filter((item) => {
        return (item.name || "").toLowerCase().includes(q2) || (item.path || "").toLowerCase().includes(q2);
      });
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
  },
  methods: {
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
  },
};
