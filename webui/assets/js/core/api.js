// JSON API helpers for the page root: every call records latency and the connection state and
// picks up the shared UI settings (refresh rate, Stats page switch) that API payloads carry.
// Spread into the root's methods by page-base.js createVuePage; the state they write
// (isConnected, apiLatency, connectionStatus, uiRefreshRate, sharedUiSettings) is root data.
export const apiMethods = {
    // API helpers
    async apiFetch(url, options = {}) {
        const start = performance.now();
        try {
            const res = await fetch(url, {
                headers: { 'Content-Type': 'application/json' },
                cache: 'no-store',
                ...options,
            });

            if (!res.ok) {
                const errorData = await res.json().catch(() => ({ detail: 'Unknown error' }));
                const error = new Error(errorData.detail || res.statusText);
                error.status = res.status;
                throw error;
            }

            // Calculate latency
            const end = performance.now();
            this.apiLatency = Math.round(end - start) + 'ms';
            this.markUpdated();

            // Restore connection state if it was down
            if (!this.isConnected) {
                this.isConnected = true;
                this.connectionStatus = 'Server Online';
                console.info('Server connection restored');
            }

            // Read the response
            const result = await res.json();

            // Extract global settings if present (auto-sync)
            const uiMeta = (result && typeof result === 'object') ? (result.ui || result) : null;
            if (uiMeta && uiMeta.ui_refresh_seconds) {
                const newRate = uiMeta.ui_refresh_seconds * 1000;
                if (this.uiRefreshRate !== newRate) {
                    this.uiRefreshRate = newRate;
                    console.debug(`UI Refresh rate updated: ${uiMeta.ui_refresh_seconds}s`);
                }
            }

            if (uiMeta && typeof uiMeta.stats_page_enabled === 'boolean') {
                this.sharedUiSettings = {
                    ...this.sharedUiSettings,
                    stats_page_enabled: uiMeta.stats_page_enabled,
                };
            }

            return result;
        } catch (e) {
            // Handle network errors (offline)
            const isNetworkError = e.name === 'TypeError' ||
                e.message === 'Failed to fetch' ||
                e.message === 'Load failed' ||
                e.message.includes('ERR_CONNECTION_REFUSED');

            if (isNetworkError) {
                const wasConnected = this.isConnected;
                this.isConnected = false;
                this.connectionStatus = 'Server Offline';

                if (wasConnected) {
                    console.warn(`Server went offline: ${url}`);
                }
                e.isOffline = true;
            } else if (!e.isOffline) {
                // Only log non-offline errors to console
                console.error(`API Error: ${url}`, e);
            }
            throw e;
        }
    },

    async apiPost(url, data) {
        return this.apiFetch(url, {
            method: 'POST',
            body: JSON.stringify(data),
        });
    },

    async apiPut(url, data) {
        return this.apiFetch(url, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    },

    async apiDelete(url) {
        return this.apiFetch(url, { method: 'DELETE' });
    },

    async syncSharedUiSettings() {
        try {
            await this.apiFetch('/api/settings');
        } catch (e) {
            if (!e.isOffline) {
                console.debug('Shared UI settings sync failed:', e.message || e);
            }
        }
    },
};
