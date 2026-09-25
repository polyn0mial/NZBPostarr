// Queue page: Per-job and queue-wide controls: pause, resume, stop, retry, run next, rename, schedule, delete.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

export default {
  methods: {
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
        try { localStorage.removeItem("nzb_finished_jobs"); } catch (_storageError) { /* Storage full or disabled: keep the in-memory state. */ }
        this.showToast("success", "Cleared", "Finished jobs dismissed");
      } catch (e2) {
        this.showToast("error", "Error", "Failed to clear finished jobs");
      }
    },

    async deleteJob(jobId) {
      this.finished = this.finished.filter((j2) => j2.job_id !== jobId);
      this.counts.finished = this.finished.length;
      try { localStorage.setItem("nzb_finished_jobs", JSON.stringify({ ts: Date.now(), jobs: this.finished })); } catch (_storageError) { /* Storage full or disabled: keep the in-memory state. */ }
      try {
        await this.apiFetch(`/api/uploads/jobs/${jobId}`, { method: "DELETE" });
      } catch (e2) {
        this.showToast("error", "Error", "Failed to delete job");
        await this.loadJobs();
      }
    },
  },
};
