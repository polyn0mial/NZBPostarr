// Settings page methods: indexers. Spread into the page's methods by index.js.
export const indexersMethods = {
    // Check if backfill should be available for an indexer
    canBackfill(indexerId) {
        return this.settings.destinations[`enable_${indexerId}`];
    },

    // ============================================================
    //  Backfill State Management
    // ============================================================
    updateBackfillStates() {
        this.indexers.forEach(indexer => {
            const enabled = this.settings.destinations[`enable_${indexer.id}`];
            if (!enabled) {
                // Disable and uncheck backfill/priority if indexer itself is disabled
                this.settings.destinations[`backfill_${indexer.id}`] = false;
                this.settings.destinations[`priority_${indexer.id}`] = false;
            }
        });
    },

    // ============================================================
    //  Data Loading
    // ============================================================
    async loadIndexers() {
        try {
            const data = await this.apiFetch('/api/indexers');
            this.indexers = data.map(idx => ({ ...idx, faviconError: false }));

            // Initialize settings for each indexer
            this.indexers.forEach(indexer => {
                if (this.settings.destinations[`enable_${indexer.id}`] === undefined) {
                    this.settings.destinations[`enable_${indexer.id}`] = false;
                }
                if (this.settings.destinations[`backfill_${indexer.id}`] === undefined) {
                    this.settings.destinations[`backfill_${indexer.id}`] = false;
                }
            });
        } catch (e) {
            console.error('Failed to load indexers:', e);
            this.showToast('error', 'Error', 'Failed to load indexers');
        }
    },

    async reloadIndexers() {
        try {
            await this.apiFetch('/api/indexers/reload', { method: 'POST' });
            await this.loadIndexers();
            this.showToast('success', 'Reloaded', 'Indexer plugins have been refreshed');
        } catch (e) {
            if (!e.isOffline) {
                this.showToast('error', 'Error', 'Network error reloading indexers');
            }
        }
    },

    // ============================================================
    //  Indexer Credential Modal
    // ============================================================
    openKeyModal(indexer) {
        this.activeIndexer = indexer;
        this.modalKey = this.settings.api_keys[indexer.id] || '';
        this.modalUsername = this.settings.usernames[indexer.id] || '';
        this.showKey = false;
        this.showKeyModal = true;
    },

    toggleKeyVisibility() {
        this.showKey = !this.showKey;
    },

    async saveIndexerKey() {
        const id = this.activeIndexer.id;

        // Update local settings object
        this.settings.api_keys[id] = this.modalKey;

        if (this.activeIndexer.requires_username) {
            this.settings.usernames[id] = this.modalUsername;
        }

        // Save to backend using centralized apiPut
        try {
            await this.apiPut('/api/settings/credentials', {
                api_keys: this.settings.api_keys,
                usernames: this.settings.usernames
            });
            this.showToast('success', 'Credentials Saved', `Updated ${this.activeIndexer.name} credentials.`);
            this.showKeyModal = false;
        } catch (e) {
            if (e.isOffline) {
                this.showToast('error', 'Network Error', 'Could not reach the backend.');
            } else {
                this.showToast('error', 'Save Failed', 'Failed to update credentials in config.');
            }
        }
    },
};
