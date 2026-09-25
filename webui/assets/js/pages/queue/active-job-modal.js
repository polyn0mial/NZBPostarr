// Queue page: The running job's modal: its items, reorder and remove.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

export default {
  computed: {
    activeJobModalFilteredItems() {
      const q2 = (this.activeJobModalSearch || "").trim().toLowerCase();
      if (!q2) return this.activeJobModalItems;
      return this.activeJobModalItems.filter((item) => {
        return (item.name || "").toLowerCase().includes(q2) || (item.path || "").toLowerCase().includes(q2);
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
  },
  methods: {
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

    activeJobHasInspectableItems(job) {
      return this.jobHasExplicitPaths(job) || !!job?.current_item;
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
  },
};
