// Auto-split from queue.js - verbatim methods bodies.
export default {
async stopQueueAndClearProcessing() {
      const ok = await this.confirmDialog("Stop the active job and clear all waiting jobs?", {
        title: "Stop And Clear Queue",
        detail: "The active row is hidden while the job shuts down in the background.",
        danger: true,
        confirmLabel: "Stop And Clear"
      });
      if (!ok) return;
      try {
        const res = await this.apiFetch("/api/uploads/queue/stop-clear", { method: "POST" });
        this.running = [];
        this.queued = [];
        this.counts.running = 0;
        this.counts.queued = 0;
        this.queueControl = res.control || this.queueControl;
        this.showToast("warning", "Queue Clearing", res.message || "Queue stopping and clearing");
        this.startTimeout(() => this.loadJobs(), 700);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to stop and clear queue");
      }
    },

async cancelJob(jobId) {
      try {
        await this.apiFetch(`/api/uploads/jobs/${jobId}/stop`, { method: "POST" });
        this.queued = this.queued.filter((j2) => j2.job_id !== jobId);
        this.counts.queued = this.queued.length;
        if (this.queuedJobModalJob && this.queuedJobModalJob.job_id === jobId) {
          this.closeQueuedJobModal();
        }
        this.showToast("success", "Removed", "Job removed from queue");
        this.startTimeout(() => this.loadJobs(), 300);
      } catch (e2) {
        this.showToast("error", "Error", "Failed to cancel job");
      }
    },

async promoteJob(jobId) {
      if (this.isJobActionPending(jobId)) return;
      this.setJobActionPending(jobId, true);
      try {
        await this.apiFetch(`/api/uploads/queue/${jobId}/promote`, { method: "POST" });
        this.showToast("success", "Promoted", "Job moved to front of queue");
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to promote job");
      } finally {
        this.setJobActionPending(jobId, false);
      }
    },

async clearJobQueue() {
      const ok = await this.confirmDialog(`Cancel all ${this.counts.queued} waiting job(s)?`, {
        title: "Clear Waiting Jobs",
        detail: "This does NOT clear staged items.",
        danger: true,
        confirmLabel: "Cancel Jobs",
        cancelLabel: "Keep Jobs"
      });
      if (!ok) return;
      try {
        const res = await this.apiFetch("/api/uploads/queue/clear", { method: "POST" });
        this.closeQueuedJobModal();
        this.showToast("success", "Waiting Jobs Cleared", `${res.cleared} queued job(s) cancelled`);
        await this.loadJobs();
      } catch (e2) {
        this.showToast("error", "Error", "Failed to clear job queue");
      }
    },

async clearFinished() {
      try {
        await this.apiFetch("/api/uploads/jobs/completed", { method: "DELETE" });
        this.finished = [];
        this.counts.finished = 0;
        this.showFinishedModal = false;
        this.showToast("success", "Cleared", "Finished jobs dismissed");
      } catch (e2) {
        this.showToast("error", "Error", "Failed to clear finished jobs");
      }
    },

async deleteJob(jobId) {
      this.finished = this.finished.filter((j2) => j2.job_id !== jobId);
      this.counts.finished = this.finished.length;
      try {
        await this.apiFetch(`/api/uploads/jobs/${jobId}`, { method: "DELETE" });
      } catch (e2) {
        this.showToast("error", "Error", "Failed to delete job");
        await this.loadJobs();
      }
    },

// ============================================================
    //  Refresh All
    // ============================================================
    refreshAll(forceRefresh = false) {
      this.loadPending(forceRefresh);
      this.loadQueueItems();
      this.loadJobs();
      this.loadQueuedPaths();
    },
};
