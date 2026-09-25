// Settings page methods: backup. Spread into the page's methods by index.js.
export const backupMethods = {
    async rollbackBackup(backupId) {
        if (this.updater.rollingBack) return;
        if (!backupId) return;

        this.updater.rollingBack = true;
        try {
            const res = await this.apiFetch('/api/system/update/rollback', {
                method: 'POST',
                body: JSON.stringify({
                    backup_id: backupId,
                    restart: this.updater.restartAfterAction,
                }),
            });
            this.showToast('success', 'Rollback', `Restored snapshot ${backupId}${res.restart_scheduled ? '; restarting...' : ''}`);
            await this.loadUpdateData(true);
        } catch (e) {
            this.showToast('error', 'Rollback', e.message || 'Rollback failed');
        } finally {
            this.updater.rollingBack = false;
        }
    },

    async createFullBackup() {
        if (this.backupJob.creating) return;
        this.backupJob.creating = true;
        try {
            const res = await this.apiFetch('/api/system/backup/create', {
                method: 'POST',
                body: JSON.stringify({
                    skip_tmp_contents: this.backupJob.skipTmpContents,
                }),
            });
            this.backupJob.lastArchivePath = res.archive_path || '';
            this.backupJob.lastArchiveSizeBytes = res.size_bytes || 0;
            this.showToast('success', 'Backup Created', res.archive_name || 'Full backup completed.');
        } catch (e) {
            this.showToast('error', 'Backup Failed', e.message || 'Failed to create backup.');
        } finally {
            this.backupJob.creating = false;
        }
    },

    onBackupFolderInput() {
        this.backupJob.folderSaveError = '';
        this.backupJob.lastSavedFolder = '';
        if (!this.backupJob.folderEditable || !this.backupFolderAutosaveDebounced) return;
        this.backupFolderAutosaveDebounced();
    },

    toggleBackupFolderEditing() {
        if (this.backupJob.folderEditable) {
            // Save while still unlocked: persistBackupFolder() only writes an editable field.
            void this.persistBackupFolder().finally(() => {
                this.backupJob.folderEditable = false;
            });
            return;
        }
        this.backupJob.folderEditable = true;
    },

    async persistBackupFolder() {
        if (!this.backupJob.folderEditable) return;
        const nextFolder = String(this.settings?.folders?.backup_folder || '').trim();
        if (!nextFolder) {
            this.backupJob.folderSaveError = 'Backup folder cannot be empty.';
            return;
        }
        if (nextFolder === this.backupJob.lastSavedFolder) return;
        this.backupJob.savingFolder = true;
        this.backupJob.folderSaveError = '';
        try {
            await this.apiPut('/api/settings/folders', this.buildFoldersSettingsPayload());
            this.backupJob.lastSavedFolder = nextFolder;
            this.showStatus('Backup folder saved', false);
        } catch (e) {
            this.backupJob.folderSaveError = e?.message || 'Failed to autosave backup folder.';
        } finally {
            this.backupJob.savingFolder = false;
        }
    },
};
