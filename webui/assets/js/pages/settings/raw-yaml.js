// Settings page methods: raw yaml. Spread into the page's methods by index.js.
export const rawYamlMethods = {
    async loadRawSettings() {
        try {
            const data = await this.apiFetch('/api/settings/raw');
            this.rawYaml = data.content;
            this.rawYamlOriginal = data.content;
            this.rawConfigPath = data.path;
        } catch (e) {
            console.error('Failed to load raw settings:', e);
        }
    },

    openYamlModal() {
        this.showYamlModal = true;
        this.updateYamlPreview();
    },

    async closeYamlModal() {
        if (this.rawYamlDirty) {
            const ok = await this.confirmDialog('You have unsaved changes. Close without saving?', {
                title: 'Discard Changes',
                detail: 'Your edits to the raw YAML will be lost.',
                danger: true,
                confirmLabel: 'Discard',
                cancelLabel: 'Keep Editing',
            });
            if (!ok) return;
        }
        this.showYamlModal = false;
        this.isEditingRawYaml = false;
    },

    resetYaml() {
        this.rawYaml = this.rawYamlOriginal;
    },

    async openReadmeModal() {
        try {
            const data = await this.apiFetch('/api/settings/readme');
            this.readmeContent = data.content;
            this.readmeOriginal = data.content;
            this.readmeFilePath = data.path;
        } catch (e) {
            this.readmeContent = '';
            this.readmeOriginal = '';
            this.readmeFilePath = 'indexers/readme/readme.txt';
        }
        this.showReadmeModal = true;
    },

    async closeReadmeModal() {
        if (this.readmeDirty) {
            const ok = await this.confirmDialog('You have unsaved changes. Close without saving?', {
                title: 'Discard Changes',
                detail: 'Your edits to readme.txt will be lost.',
                danger: true,
                confirmLabel: 'Discard',
                cancelLabel: 'Keep Editing',
            });
            if (!ok) return;
        }
        this.showReadmeModal = false;
    },

    async saveReadme() {
        try {
            await this.apiPost('/api/settings/readme', { content: this.readmeContent });
            this.readmeOriginal = this.readmeContent;
            this.showToast('success', 'Readme Saved', 'readme.txt updated successfully.');
            this.showReadmeModal = false;
        } catch (e) {
            this.showToast('error', 'Save Failed', e.message || 'Failed to save readme.');
        }
    },

    syncYamlScroll(e) {
        const hl = this.$refs.yamlHighlight;
        if (hl) {
            hl.scrollTop = e.target.scrollTop;
            hl.scrollLeft = e.target.scrollLeft;
        }
    },

    async updateYamlPreview() {
        // Only update if the raw section is open OR if it was recently edited
        // This prevents excessive polling if the user isn't even looking at the YAML
        if ((!this.expandedSections.raw && !this.showYamlModal) || this.loading) return;

        // Don't update if we're currently processing a save or update
        if (this.isUpdatingYaml) return;
        this.isUpdatingYaml = true;

        try {
            // Map frontend settings back to the structure the backend expects for config.yaml
            const destinations = {
                enable_backfill: this.settings.destinations.enable_backfill,
                enable_duplicate_bypass: this.settings.destinations.enable_duplicate_bypass,
            };
            this.indexers.forEach(indexer => {
                destinations[`enable_${indexer.id}`] = this.settings.destinations[`enable_${indexer.id}`];
                destinations[`backfill_${indexer.id}`] = this.settings.destinations[`backfill_${indexer.id}`];
                destinations[`priority_${indexer.id}`] = this.settings.destinations[`priority_${indexer.id}`];
            });

            const previewData = {
                ...this.settings.processing,
                ...this.settings.upload,
                ...this.settings.ui,
                ...destinations,
                skip_files: this.settings.skip_files,
                base_folder: this.settings.folders.base,
                backup_folder: this.settings.folders.backup_folder,
                folder_paths: this.buildFolderPathPayload(),
                nntp_servers: this.servers,
                api_keys: this.settings.api_keys,
                usernames: this.settings.usernames
            };

            const res = await this.apiPost('/api/settings/preview', previewData);

            if (res.ok) {
                const data = await res.json();

                // Use Vue state to check if user is editing YAML (replaces document.activeElement check)
                // rawYamlOriginal is only updated by loadRawSettings() (disk saves), never by live preview
                if (!this.isEditingRawYaml) {
                    this.rawYaml = data.content;
                }
            }
        } catch (e) {
            console.warn('YAML preview update failed:', e);
        } finally {
            this.isUpdatingYaml = false;
        }
    },

    async saveRawSettings() {
        const ok = await this.confirmDialog('Save raw YAML configuration?', {
            title: 'Overwrite Config',
            detail: 'This overwrites the config file on disk immediately.',
            danger: true,
            confirmLabel: 'Overwrite',
        });
        if (!ok) return;

        try {
            await this.apiPost('/api/settings/raw', { content: this.rawYaml });
            this.rawYamlOriginal = this.rawYaml;
            this.showToast('success', 'Config Saved', 'Raw YAML configuration updated.');
            this.showYamlModal = false;
            await this.loadSettings();
        } catch (e) {
            const detail = e.message || 'Failed to save raw config.';
            if (e.isOffline) {
                this.showToast('error', 'Error', 'A network error occurred.');
            } else {
                this.showToast('error', 'Save Failed', detail);
            }
        }
    },
};
