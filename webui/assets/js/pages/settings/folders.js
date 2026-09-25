// Settings page methods: folders. Spread into the page's methods by index.js.
export const foldersMethods = {
    normalizeFolderPathEntry(fp = {}) {
        return {
            path: String(fp.path || ''),
            category: String(fp.category || 'auto'),
            monitor: !!fp.monitor,
            allow_bulk_selection: fp.allow_bulk_selection !== false,
        };
    },

    buildFolderPathPayload() {
        return this.settings.folders.folder_paths
            .filter(fp => fp.path && fp.path.trim())
            .map(fp => {
                const normalized = this.normalizeFolderPathEntry(fp);
                return {
                    path: normalized.path.trim(),
                    category: normalized.category,
                    monitor: normalized.monitor,
                    allow_bulk_selection: normalized.allow_bulk_selection,
                };
            });
    },

    buildFoldersSettingsPayload() {
        return {
            base_folder: this.settings.folders.base,
            backup_folder: this.settings.folders.backup_folder,
            folder_paths: this.buildFolderPathPayload(),
        };
    },

    // ============================================================
    //  Folder Path Management
    // ============================================================
    addFolderPath() {
        this.settings.folders.folder_paths.push(this.normalizeFolderPathEntry());
    },

    removeFolderPath(idx) {
        this.settings.folders.folder_paths.splice(idx, 1);
    },

    // ── Folder Browser ──────────────────────────
    async openFolderBrowser(idx) {
        this.folderBrowser.targetIdx = idx;
        this.folderBrowser.open = true;
        const current = idx === 'base'
            ? this.settings.folders.base || '/'
            : idx === 'backup'
                ? this.settings.folders.backup_folder || '/'
                : this.settings.folders.folder_paths[idx]?.path || '/';
        await this.browseTo(current);
    },

    closeFolderBrowser() {
        this.folderBrowser.open = false;
        this.folderBrowser.targetIdx = null;
    },

    async browseTo(path) {
        this.folderBrowser.loading = true;
        try {
            const data = await this.apiFetch(`/api/settings/browse?path=${encodeURIComponent(path)}`);
            this.folderBrowser.currentPath = data.path;
            this.folderBrowser.parentPath = data.parent;
            this.folderBrowser.dirs = data.dirs || [];
        } catch (e) {
            // If path doesn't exist, fall back to root
            if (path !== '/') {
                await this.browseTo('/');
                return;
            }
            this.folderBrowser.dirs = [];
        } finally {
            this.folderBrowser.loading = false;
        }
    },

    selectBrowsedFolder() {
        const idx = this.folderBrowser.targetIdx;
        if (idx === 'base') {
            this.settings.folders.base = this.folderBrowser.currentPath;
        } else if (idx === 'backup') {
            this.settings.folders.backup_folder = this.folderBrowser.currentPath;
        } else if (idx !== null && this.settings.folders.folder_paths[idx]) {
            this.settings.folders.folder_paths[idx].path = this.folderBrowser.currentPath;
        }
        this.closeFolderBrowser();
    },
};
