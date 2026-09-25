// Queue page: The job queue and finished lists: loading, row labels, badges and the completed-job modal.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

// Pick which job the queue-control panel should track after a refresh:
// keep the current selection if it is still present, otherwise prefer the
// running job, then the paused one, then whatever is first in either list.
function resolvePreferredJobId(currentId, running, queued) {
  const availableJobIds = new Set([...running, ...queued].map((job) => job.job_id));
  if (currentId && !availableJobIds.has(currentId)) {
    currentId = null;
  }
  if (!currentId) {
    const preferredJob = running.find((job) => job.status === "running")
      || running.find((job) => job.status === "paused")
      || running[0]
      || queued[0];
    currentId = preferredJob?.job_id || null;
  }
  return currentId;
}

// Kick off one revalidation of queued jobs after a refresh lands while the
// queue is paused and non-empty; only ever once per pause, until it unpauses.
function maybeRevalidateQueuedJobs(self) {
  if (!self.queueControl?.paused) {
    self._pendingQueuedJobRevalidationDone = false;
    return;
  }
  if (!self._pendingQueuedJobRevalidationDone && ((self.counts?.queued || 0) > 0 || (self.counts?.running || 0) > 0)) {
    self._pendingQueuedJobRevalidationDone = true;
    void self.revalidateQueuedJobs();
  }
}

// Keep the open "active job" modal in sync with a fresh queue snapshot, or
// close it if the job it was showing is gone.
function syncActiveJobModal(self) {
  if (!self.showActiveJobModal || !self.activeJobModalJob) return;
  const updatedActive = self.running.find((j2) => j2.job_id === self.activeJobModalJob.job_id);
  if (updatedActive) {
    self.activeJobModalJob = updatedActive;
    if (!self.activeJobModalSaving) {
      void self.loadActiveJobModalItems(updatedActive.job_id, true);
    }
  } else {
    self.closeActiveJobModal();
  }
}

// Keep the open "queued job" modal in sync with a fresh queue snapshot, or
// close it if the job it was showing is gone.
function syncQueuedJobModal(self) {
  if (!self.showQueuedJobModal || !self.queuedJobModalJob) return;
  const updated = self.queued.find((j2) => j2.job_id === self.queuedJobModalJob.job_id);
  if (updated) {
    self.queuedJobModalJob = updated;
    if (!self.queuedJobModalRenaming) {
      self.queuedJobModalName = updated.display_name || "";
    }
    if (!self.queuedJobModalScheduling) {
      self.hydrateQueuedJobSchedule(updated.run_after || null);
    }
  } else {
    self.closeQueuedJobModal();
  }
}

export default {
  computed: {
    finishedPreview() {
      return this.finished.slice(0, 3);
    },

    jobQueueCount() {
      return this.running.length + this.queued.length;
    },

    jobQueueEntries() {
      const activeEntries = this.running.map((job, index2) => ({
        ...job,
        _queueEntryType: "active",
        _queuePosition: index2 + 1,
        _queuedIndex: null
      }));
      const queuedEntries = this.queued.map((job, index2) => ({
        ...job,
        _queueEntryType: "queued",
        _queuePosition: activeEntries.length + index2 + 1,
        _queuedIndex: index2
      }));
      return [...activeEntries, ...queuedEntries];
    },

    hasMoreFinished() {
      return this.finished.length > 3;
    },

    completedJobModalFilteredItems() {
      const query = (this.completedJobModalSearch || "").trim().toLowerCase();
      if (!query) return this.completedJobModalItems;
      return this.completedJobModalItems.filter((item) => {
        return (item.name || "").toLowerCase().includes(query) || (item.path || "").toLowerCase().includes(query);
      });
    },
  },
  methods: {
    async loadJobs() {
      if (this._loadJobsPromise) return this._loadJobsPromise;
      this._loadJobsPromise = (async () => {
        try {
          const data = await this.apiFetch("/api/uploads/queue");
          this.running = data.running || [];
          this.queued = data.queued || [];
          const freshFinished = data.finished || [];
          if (freshFinished.length > 0) {
            this.finished = freshFinished;
            try { localStorage.setItem("nzb_finished_jobs", JSON.stringify({ ts: Date.now(), jobs: freshFinished })); } catch (_) {}
          } else {
            try {
              const cached = JSON.parse(localStorage.getItem("nzb_finished_jobs") || "null");
              this.finished = (cached && Date.now() - cached.ts < 864e5 && Array.isArray(cached.jobs)) ? cached.jobs : [];
            } catch (_) { this.finished = []; }
          }
          this.counts = data.counts || { running: 0, queued: 0, finished: this.finished.length };
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

    jobScheduleLabel(job) {
      if (!job || !job.run_after) return "";
      return this.formatRunAfterLocal(job.run_after);
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
  },
};

export { resolvePreferredJobId, maybeRevalidateQueuedJobs, syncActiveJobModal, syncQueuedJobModal };
