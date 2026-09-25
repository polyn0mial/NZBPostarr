// Settings page methods: updates. Spread into the page's methods by index.js.
export const updatesMethods = {
    async loadUpdateData(force = false) {
        try {
            const statusUrl = force ? '/api/system/update/status?force=true' : '/api/system/update/status';
            const [status, releasesRes, backupsRes] = await Promise.all([
                this.apiFetch(statusUrl),
                this.apiFetch('/api/system/update/releases?limit=20'),
                this.apiFetch('/api/system/update/backups?limit=20'),
            ]);

            this.updater.status = status || null;
            this.updater.releases = (releasesRes && releasesRes.releases) || [];
            this.updater.backups = (backupsRes && backupsRes.backups) || [];

            if (!this.updater.selectedVersion && this.updater.releases.length > 0) {
                this.updater.selectedVersion = this.updater.releases[0].version || '';
            }
        } catch (e) {
            console.error('Failed to load updater data:', e);
            this.showToast('error', 'Updater Error', e.message || 'Failed to load updater data');
        }
    },

    formatVersionLabel(v) {
        const raw = String(v || '').trim();
        if (!raw) return 'unknown';
        return raw.startsWith('v') ? raw : `v${raw}`;
    },

    formatBytesCompact(bytes) {
        const n = Number(bytes || 0);
        if (!Number.isFinite(n) || n <= 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let idx = 0;
        let val = n;
        while (val >= 1024 && idx < units.length - 1) {
            val /= 1024;
            idx += 1;
        }
        const precision = val >= 100 ? 0 : val >= 10 ? 1 : 2;
        return `${val.toFixed(precision)} ${units[idx]}`;
    },

    async checkUpdatesNow() {
        if (this.updater.checking) return;
        this.updater.checking = true;
        try {
            await this.apiFetch('/api/system/update/check', { method: 'POST' });
            await this.loadUpdateData(true);
            this.showToast('success', 'Updater', 'Checked GitHub for new versions');
        } catch (e) {
            this.showToast('error', 'Updater', e.message || 'Update check failed');
        } finally {
            this.updater.checking = false;
        }
    },

    async installLatestFromGithub() {
        if (this.updater.installing) return;
        this.updater.installing = true;
        try {
            const res = await this.apiFetch('/api/system/update/install/github', {
                method: 'POST',
                body: JSON.stringify({
                    version: null,
                    restart: this.updater.restartAfterAction,
                }),
            });
            this.showToast('success', 'Updater', `Installed ${res.installed_version_label || 'update'}${res.restart_scheduled ? '; restarting...' : ''}`);
            await this.loadUpdateData(true);
        } catch (e) {
            this.showToast('error', 'Updater', e.message || 'GitHub install failed');
        } finally {
            this.updater.installing = false;
        }
    },

    async installSelectedRelease() {
        if (this.updater.installing) return;
        if (!this.updater.selectedVersion) {
            this.showToast('warning', 'Updater', 'Select a release first');
            return;
        }

        this.updater.installing = true;
        try {
            const res = await this.apiFetch('/api/system/update/install/github', {
                method: 'POST',
                body: JSON.stringify({
                    version: this.updater.selectedVersion,
                    restart: this.updater.restartAfterAction,
                }),
            });
            this.showToast('success', 'Updater', `Installed ${res.installed_version_label || this.updater.selectedVersion}${res.restart_scheduled ? '; restarting...' : ''}`);
            await this.loadUpdateData(true);
        } catch (e) {
            this.showToast('error', 'Updater', e.message || 'Release install failed');
        } finally {
            this.updater.installing = false;
        }
    },

    onUpdateZipChosen(e) {
        const file = (e.target && e.target.files && e.target.files[0]) || null;
        this.updater.selectedUploadFile = file;
    },

    async installUpdateZip() {
        if (this.updater.uploading) return;
        if (!this.updater.selectedUploadFile) {
            this.showToast('warning', 'Updater', 'Choose a ZIP file first');
            return;
        }

        this.updater.uploading = true;
        try {
            const form = new FormData();
            form.append('file', this.updater.selectedUploadFile);
            form.append('restart', String(this.updater.restartAfterAction));

            const res = await fetch('/api/system/update/install/upload', {
                method: 'POST',
                body: form,
                cache: 'no-store',
            });

            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(payload.detail || `Install failed (HTTP ${res.status})`);
            }

            this.showToast('success', 'Updater', `Installed ${payload.installed_version_label || 'uploaded update'}${payload.restart_scheduled ? '; restarting...' : ''}`);
            this.updater.selectedUploadFile = null;
            await this.loadUpdateData(true);
        } catch (e) {
            this.showToast('error', 'Updater', e.message || 'ZIP install failed');
        } finally {
            this.updater.uploading = false;
        }
    },

    // ============================================================
    //  Service Controls
    // ============================================================
    async stopAllJobs() {
        if (this.serviceControls.stopping) return;
        const ok = await this.confirmDialog('Stop all running uploads and clear the queue?', {
            title: 'Stop All Uploads',
            detail: 'This will interrupt any active upload immediately.',
            danger: true,
            confirmLabel: 'Stop Everything',
        });
        if (!ok) return;

        this.serviceControls.stopping = true;
        try {
            const result = await this.apiFetch('/api/system/stop-all', {
                method: 'POST',
                body: JSON.stringify({
                    clear_staged_items: true,
                    wait_timeout_seconds: 20,
                }),
            });
            this.serviceControls.lastAction = result;

            const remaining = Number(result?.stop?.active_count || 0);
            const status = result?.status === 'partial' ? 'warning' : 'success';
            const title = result?.status === 'partial' ? 'Stopping Timed Out' : 'Uploads Stopped';
            const summary = result?.status === 'partial'
                ? `Stop requested. ${remaining} active job${remaining === 1 ? '' : 's'} still shutting down when the timeout expired.`
                : 'All jobs stopped and queues cleared.';

            this.showToast(status, title, summary, result?.status === 'partial' ? 12000 : 6000);
        } catch (e) {
            this.showToast('error', 'Stop Failed', e.message || 'Could not stop jobs');
        } finally {
            this.serviceControls.stopping = false;
        }
    },

    async restartService() {
        if (this.serviceControls.restarting) return;
        const ok = await this.confirmDialog('Restart the NZBPostarr service?', {
            title: 'Restart Service',
            detail: 'Active uploads are stopped first. The page will reload automatically.',
            danger: true,
            confirmLabel: 'Restart',
        });
        if (!ok) return;

        this.serviceControls.restarting = true;
        try {
            const result = await this.apiFetch('/api/system/restart', {
                method: 'POST',
                body: JSON.stringify({
                    delay_seconds: 2.0,
                    stop_before_restart: true,
                    clear_staged_items: true,
                    wait_timeout_seconds: 20,
                }),
            });
            this.serviceControls.lastAction = result;

            const remaining = Number(result?.stop?.active_count || 0);
            const message = remaining > 0
                ? `Restart scheduled. ${remaining} active job${remaining === 1 ? '' : 's'} were still winding down at timeout.`
                : 'Service is restarting. The page will refresh when it comes back.';

            this.showToast('success', 'Restarting', message, 10000);
            await this.waitForServiceReload(45000);
        } catch (e) {
            this.showToast('error', 'Restart Failed', e.message || 'Could not restart service');
            this.serviceControls.restarting = false;
        }
    },

    async waitForServiceReload(timeoutMs = 45000) {
        const started = Date.now();
        let sawOffline = false;

        while ((Date.now() - started) < timeoutMs) {
            try {
                await this.apiFetch('/api/tests/health');
                if (sawOffline) {
                    window.location.reload();
                    return;
                }
            } catch (e) {
                if (e?.isOffline || e?.status >= 500) {
                    sawOffline = true;
                }
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
        }

        window.location.reload();
    },
};
