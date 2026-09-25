// The Usenet Stream card: stream form, folder browser and stream monitors.
// Spread into the dashboard page by pages/dashboard/index.js.
export const streamFormComputed = {
    streamCategories() {
        const categories = this.availableCategories.filter(cat => !['all', 'both'].includes(cat.value));
        return categories.length ? categories : [{ value: 'misc', label: 'Misc' }];
    },
    streamPostingServers() {
        const enabled = (this.nntpServers || []).filter(server => server.enabled !== false);
        return enabled.length ? enabled : (this.nntpServers || []);
    },
    isStreamUploadMode() {
        return this.streamForm.source_mode !== 'server_path';
    },
    isStreamPathMode() {
        return !this.isStreamUploadMode;
    },
    streamUsesIndexerSubmission() {
        return this.streamForm.submit_mode !== 'post_only';
    },
    streamMonitorRows() {
        const jobsById = new Map((this.jobs || []).map(job => [job.job_id, job]));
        return (this.streamMonitors || []).map(monitor => {
            const lastJob = monitor && typeof monitor.last_job === 'object' ? monitor.last_job : null;
            const currentJob = lastJob && lastJob.job_id ? jobsById.get(lastJob.job_id) || null : null;
            const displayJob = currentJob || lastJob;
            const active = currentJob && ['queued', 'running', 'paused', 'stopping'].includes(currentJob.status);
            const updatedAt = (displayJob && (displayJob.updated_at || displayJob.started_at)) || monitor.created_at || '';
            return {
                ...monitor,
                currentJob,
                displayJob,
                active: Boolean(active),
                updatedAt,
            };
        }).sort((left, right) => {
            if (left.active !== right.active) return left.active ? -1 : 1;
            return String(right.updatedAt || '').localeCompare(String(left.updatedAt || ''));
        });
    },
};

export const streamFormMethods = {
    monitorRowStatusClass(monitor) {
        const job = monitor.displayJob;
        if (!job) return 'bg-notion-bg-hover text-notion-text-tertiary';
        const status = String(job.status || '').toLowerCase();
        if (status === 'completed') return 'bg-notion-success/10 text-notion-success';
        if (status === 'failed' || status === 'stopped' || status === 'cancelled') return 'bg-notion-error/10 text-notion-error';
        if (status === 'paused') return 'bg-notion-warning/10 text-notion-warning';
        if (status === 'running' || status === 'stopping' || status === 'queued') return 'bg-notion-accent/10 text-notion-accent';
        return 'bg-notion-bg-hover text-notion-text-secondary';
    },
    monitorRowStatusLabel(monitor) {
        const job = monitor.displayJob;
        if (!job) return 'Watching';
        const status = String(job.status || '').toLowerCase();
        if (status === 'completed') return 'Last Job Completed';
        if (status === 'failed') return 'Last Job Failed';
        if (status === 'paused') return 'Paused';
        if (status === 'queued') return 'Queued';
        if (status === 'stopping') return 'Stopping';
        if (status === 'cancelled') return 'Cancelled';
        if (status === 'running') return 'Running';
        return 'Watching';
    },
    monitorRowTitle(monitor) {
        const job = monitor.displayJob;
        if (job && (job.current_item || job.display_name)) {
            return job.current_item || job.display_name;
        }
        const parts = String(monitor.folder_path || '').replace(/\\/g, '/').split('/').filter(Boolean);
        return parts.length ? parts[parts.length - 1] : 'Stream Monitor';
    },
    monitorRowInfo(monitor) {
        const job = monitor.displayJob;
        if (job) return this.jobItemInfo(job);
        return 'Watching for new NZB files';
    },
    monitorRowPercent(monitor) {
        const job = monitor.displayJob;
        return job ? this.jobItemPercent(job) : '0%';
    },
    monitorRowMeta(monitor) {
        const pieces = [];
        if (monitor.posting_server_name) pieces.push(monitor.posting_server_name);
        pieces.push(monitor.submit_mode === 'post_only' ? 'Post Only' : 'Post and Submit');
        if (monitor.indexer_id) pieces.push(`Indexer ${monitor.indexer_id}`);
        return pieces.join(' • ');
    },
    monitorRowUpdated(monitor) {
        if (!monitor.updatedAt) return 'No jobs yet';
        return this.formatDate(monitor.updatedAt);
    },

    browseStreamFile() { if (this.$refs.streamNzbPicker) this.$refs.streamNzbPicker.click(); },
    async openStreamFolderBrowser() {
        this.streamFolderBrowser.open = true;
        await this.browseStreamTo(this.streamForm.server_path || '/');
    },
    closeStreamFolderBrowser() {
        this.streamFolderBrowser.open = false;
    },
    async browseStreamTo(path) {
        this.streamFolderBrowser.loading = true;
        try {
            const data = await this.apiFetch(`/api/settings/browse?path=${encodeURIComponent(path)}`);
            this.streamFolderBrowser.currentPath = data.path;
            this.streamFolderBrowser.parentPath = data.parent;
            this.streamFolderBrowser.dirs = data.dirs || [];
        } catch (e) {
            if (path !== '/') { await this.browseStreamTo('/'); return; }
            this.streamFolderBrowser.dirs = [];
        } finally {
            this.streamFolderBrowser.loading = false;
        }
    },
    selectStreamBrowsedFolder() {
        this.streamForm.server_path = this.streamFolderBrowser.currentPath;
        this.closeStreamFolderBrowser();
    },
    setStreamSourceMode(mode) {
        if (mode !== 'upload' && mode !== 'server_path') return;
        this.streamForm.source_mode = mode;
        if (mode === 'upload') {
            this.streamForm.server_path = '';
            this.streamForm.monitor_folder = false;
        } else {
            this.clearStreamFile();
        }
    },
    handleStreamNzbSelect(e) {
        const file = e.target.files && e.target.files[0];
        if (file) {
            this.streamForm.source_mode = 'upload';
            this.streamForm.selectedFile = file;
            this.streamForm.selectedFileName = file.name;
            if (!this.streamForm.release_name) {
                this.streamForm.release_name = file.name.replace(/\.nzb$/i, '');
            }
        }
        e.target.value = '';
    },
    clearStreamFile() {
        this.streamForm.selectedFile = null;
        this.streamForm.selectedFileName = '';
    },

    async loadStreamMonitors() {
        try {
            const data = await this.apiFetch('/api/uploads/stream-monitors');
            this.streamMonitors = Array.isArray(data.monitors) ? data.monitors : [];
        } catch (e) {
            console.error('Failed to load stream monitors:', e);
        }
    },

    async removeStreamMonitor(monitorId) {
        try {
            await this.apiFetch(`/api/uploads/stream-monitors/${monitorId}`, { method: 'DELETE' });
            await this.loadStreamMonitors();
            this.showToast('success', 'Monitor Removed', 'Stream folder monitor removed');
        } catch (e) {
            this.showToast('error', 'Error', e.message || 'Failed to remove stream monitor');
        }
    },

    async handleUsenetStreamSubmit() {
        if (this.isStreamUploadMode && !this.streamForm.selectedFile) {
            this.showToast('warning', 'Select NZB', 'Choose an NZB file to stream first');
            return;
        }
        if (this.isStreamPathMode && !this.streamForm.server_path.trim()) {
            this.showToast('warning', 'Enter Path', 'Provide an NZB file or folder path on the server');
            return;
        }

        this.streamLoading = true;
        const form = new FormData();
        if (this.isStreamUploadMode) {
            form.append('file', this.streamForm.selectedFile);
        } else {
            form.append('source_path', this.streamForm.server_path.trim());
            form.append('monitor_folder', String(Boolean(this.streamForm.monitor_folder)));
        }
        if (this.streamForm.category) {
            form.append('category', this.streamForm.category);
        }
        if (this.streamForm.release_name && this.streamForm.release_name.trim()) {
            form.append('release_name', this.streamForm.release_name.trim());
        }
        if (this.streamForm.indexer_id && this.streamUsesIndexerSubmission) {
            form.append('indexer_id', this.streamForm.indexer_id);
        }
        if (this.streamForm.posting_server_name) {
            form.append('posting_server_name', this.streamForm.posting_server_name);
        }
        form.append('submit_mode', this.streamForm.submit_mode);
        form.append('test_mode', String(Boolean(this.streamForm.test_mode)));
        form.append('enable_duplicate_check', String(!this.streamForm.skip_duplicate_check));

        try {
            const result = await this.apiFetch('/api/uploads/stream-nzb', {
                method: 'POST',
                headers: {},
                body: form,
            });
            this.expandedSections['usenet-stream'] = false;
            this.expandedSections['console'] = true;
            if (result.mode === 'monitor') {
                this.showToast('success', 'Monitor Saved', result.message || 'Stream monitor enabled');
                await this.loadStreamMonitors();
            } else if (Array.isArray(result.job_ids) && result.job_ids.length > 1) {
                this.showToast('success', 'Stream Jobs Started', result.message || `Queued ${result.job_ids.length} stream jobs`);
            } else {
                this.showToast('success', 'Stream Job Started', result.message || `Job ${result.job_id} queued`);
            }
            if (this.isStreamUploadMode) {
                this.clearStreamFile();
            }
            await this.loadJobs();
        } catch (e) {
            this.showToast('error', 'Error', e.message || 'Failed to start stream job');
        } finally {
            this.streamLoading = false;
        }
    },
};
