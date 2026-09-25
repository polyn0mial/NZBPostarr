// The New Upload card: upload form state, dropdown pickers and submit.
// Spread into the dashboard page by pages/dashboard/index.js.
export function normalizeStringArray(values) {
    if (!Array.isArray(values)) return [];
    const seen = new Set();
    const normalized = [];
    values.forEach(value => {
        const text = String(value || '').trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        normalized.push(text);
    });
    return normalized;
}

export const uploadFormMethods = {
    normalizeUploadFormState() {
        const current = this.uploadForm || {};
        const normalizedCategories = normalizeStringArray(Array.isArray(current.categories) ? current.categories : [current.category || 'all']);
        const categories = normalizedCategories.includes('all') || normalizedCategories.length === 0
            ? ['all']
            : normalizedCategories.filter(value => value !== 'all');

        this.uploadForm = {
            file_path: current.file_path || '',
            upload_type: current.upload_type || 'movie',
            categories,
            folder_paths: normalizeStringArray(current.folder_paths),
            limit: current.limit ?? null,
            indexer_ids: normalizeStringArray(Array.isArray(current.indexer_ids) ? current.indexer_ids : (current.indexer_id ? [current.indexer_id] : [])),
            test_mode: Boolean(current.test_mode),
            full_tv_test: Boolean(current.full_tv_test),
            skip_duplicate_check: Boolean(current.skip_duplicate_check),
            skip_packs: Boolean(current.skip_packs),
            skip_episodes: Boolean(current.skip_episodes),
        };
    },
    handleDocumentClick(event) {
        if (!event.target.closest('[data-upload-filter-dropdown]')) {
            this.activeUploadDropdown = null;
        }
    },
    toggleUploadDropdown(dropdownId) {
        this.activeUploadDropdown = this.activeUploadDropdown === dropdownId ? null : dropdownId;
    },
    getNormalizedUploadCategories() {
        const categories = normalizeStringArray(this.uploadForm.categories);
        if (categories.includes('all') || categories.length === 0) return ['all'];
        return categories;
    },
    isUploadCategorySelected(value) {
        return this.getNormalizedUploadCategories().includes(value);
    },
    toggleUploadCategory(value) {
        if (value === 'all') {
            this.uploadForm.categories = ['all'];
            return;
        }

        const next = new Set(this.getNormalizedUploadCategories().filter(item => item !== 'all'));
        if (next.has(value)) next.delete(value);
        else next.add(value);

        this.uploadForm.categories = next.size ? Array.from(next) : ['all'];
    },
    areAllUploadIndexersSelected() {
        return this.uploadForm.indexer_ids.length === 0 || this.uploadForm.indexer_ids.length === this.uploadIndexerOptions.length;
    },
    isUploadIndexerSelected(indexerId) {
        return this.areAllUploadIndexersSelected() || this.uploadForm.indexer_ids.includes(indexerId);
    },
    clearUploadIndexers() {
        this.uploadForm.indexer_ids = [];
    },
    toggleUploadIndexer(indexerId) {
        if (this.areAllUploadIndexersSelected()) {
            this.uploadForm.indexer_ids = [indexerId];
            return;
        }
        const next = new Set(normalizeStringArray(this.uploadForm.indexer_ids));
        if (next.has(indexerId)) next.delete(indexerId);
        else next.add(indexerId);
        this.uploadForm.indexer_ids = next.size === this.uploadIndexerOptions.length ? [] : Array.from(next);
    },
    areAllUploadFolderPathsSelected() {
        return this.uploadForm.folder_paths.length === 0 || this.uploadForm.folder_paths.length === this.uploadFolderPathOptions.length;
    },
    isUploadFolderPathSelected(folderPath) {
        return this.areAllUploadFolderPathsSelected() || this.uploadForm.folder_paths.includes(folderPath);
    },
    clearUploadFolderPaths() {
        this.uploadForm.folder_paths = [];
    },
    toggleUploadFolderPath(folderPath) {
        if (this.areAllUploadFolderPathsSelected()) {
            this.uploadForm.folder_paths = [folderPath];
            return;
        }
        const next = new Set(normalizeStringArray(this.uploadForm.folder_paths));
        if (next.has(folderPath)) next.delete(folderPath);
        else next.add(folderPath);
        this.uploadForm.folder_paths = next.size === this.uploadFolderPathOptions.length ? [] : Array.from(next);
    },
    uploadCategorySummary() {
        const categories = this.getNormalizedUploadCategories();
        if (categories.includes('all')) return 'All';
        if (categories.length === 1) {
            const option = this.uploadCategoryOptions.find(cat => cat.value === categories[0]);
            return option ? option.label : categories[0];
        }
        return `${categories.length} selected`;
    },
    uploadIndexerSummary() {
        if (!this.uploadIndexerOptions.length) return 'No Indexers';
        if (this.areAllUploadIndexersSelected()) return 'All Enabled';
        if (this.uploadForm.indexer_ids.length === 1) {
            const option = this.uploadIndexerOptions.find(indexer => indexer.id === this.uploadForm.indexer_ids[0]);
            return option ? option.name : this.uploadForm.indexer_ids[0];
        }
        return `${this.uploadForm.indexer_ids.length} selected`;
    },
    formatUploadFolderLabel(folder) {
        const path = String(folder?.path || '');
        const leaf = path.split(/[/\\]/).filter(Boolean).pop() || path || 'Folder';
        const category = String(folder?.category || '').trim();
        return category ? `${leaf} (${category})` : leaf;
    },
    uploadFolderSummary() {
        if (!this.uploadFolderPathOptions.length) return 'No Folder Paths';
        if (this.areAllUploadFolderPathsSelected()) return 'All Paths';
        if (this.uploadForm.folder_paths.length === 1) {
            const option = this.uploadFolderPathOptions.find(folder => folder.value === this.uploadForm.folder_paths[0]);
            return option ? option.label : this.uploadForm.folder_paths[0];
        }
        return `${this.uploadForm.folder_paths.length} selected`;
    },

    // --- UPLOAD HANDLERS ---
    async handleUploadSubmit() {
        if (this.isJobRunning) {
            // If a job is running, this button acts as a stop trigger
            const job = this.primaryRunningJob;
            if (job) {
                await this.stopJob(job.job_id);
            }
            return;
        }

        if (this.uploadForm.file_path && !this.uploadForm.upload_type) {
            this.showToast('warning', 'Select Type', 'Please select Movie, TV (Episode), or TV (Show)');
            return;
        }

        this.loading = true;
        const selectedCategories = this.uploadForm.file_path
            ? [this.uploadForm.upload_type.startsWith('tv') ? 'tv' : 'movies']
            : this.getNormalizedUploadCategories();
        const data = {
            category: selectedCategories[0] || 'all',
            categories: selectedCategories,
            limit: this.uploadForm.limit ? parseInt(this.uploadForm.limit) : null,
            test_mode: this.uploadForm.test_mode,
            enable_duplicate_check: !this.uploadForm.skip_duplicate_check,
            skip_packs: this.uploadForm.skip_packs,
            skip_episodes: this.uploadForm.skip_episodes,
            full_tv_test: this.uploadForm.full_tv_test,
            file_path: this.uploadForm.file_path || null,
            upload_type: this.uploadForm.upload_type || null,
            indexer_id: this.uploadForm.indexer_ids.length === 1 ? this.uploadForm.indexer_ids[0] : null,
            indexer_ids: [...this.uploadForm.indexer_ids],
            folder_paths: [...this.uploadForm.folder_paths],
            source: 'dashboard-upload',
        };

        try {
            const result = await this.apiFetch('/api/uploads/start', {
                method: 'POST',
                body: JSON.stringify(data),
            });
            this.expandedSections['new-upload'] = false;
            this.expandedSections['console'] = true;
            const jobIds = Array.isArray(result.job_ids) && result.job_ids.length ? result.job_ids : (result.job_id ? [result.job_id] : []);
            this.showToast('success', 'Upload Started', jobIds.length > 1 ? `${jobIds.length} jobs started` : `Job ${jobIds[0]} started`);
            this.activeUploadDropdown = null;
            await this.loadJobs();
        } catch (e) {
            this.showToast('error', 'Error', e?.message || 'Failed to start upload');
        } finally {
            this.loading = false;
        }
    },
};
