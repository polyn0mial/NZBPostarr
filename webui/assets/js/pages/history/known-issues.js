// Known Issues panel on the History page: grouped failed-submission signatures with a
// mute/unmute action. Spread into the page's methods.
export const knownIssuesMethods = {
    toggleKnownIssuesPanel() {
        this.knownIssuesOpen = !this.knownIssuesOpen;
        if (this.knownIssuesOpen && this.knownIssues.length === 0) {
            this.loadKnownIssues();
        }
    },

    async loadKnownIssues() {
        if (!this.isConnected) return;
        this.knownIssuesLoading = true;
        try {
            const params = new URLSearchParams({ include_muted: String(this.knownIssuesShowMuted) });
            const result = await this.apiFetch(`/api/uploads/errors/grouped?${params}`);
            this.knownIssues = Array.isArray(result?.issues) ? result.issues : [];
        } catch (e) {
            if (!e.isOffline) {
                this.showToast('error', 'Error', 'Failed to load known issues');
            }
        } finally {
            this.knownIssuesLoading = false;
        }
    },

    async toggleIssueMute(issue) {
        const wasMuted = issue.muted;
        try {
            const endpoint = wasMuted ? '/api/uploads/errors/unmute' : '/api/uploads/errors/mute';
            await this.apiPost(endpoint, { indexer_id: issue.indexer_id, signature: issue.signature });
            issue.muted = !wasMuted;
            this.showToast('success', wasMuted ? 'Unmuted' : 'Muted', wasMuted ? 'Issue restored to the default view' : 'Issue silenced');
            if (!this.knownIssuesShowMuted && issue.muted) {
                this.knownIssues = this.knownIssues.filter(i => i !== issue);
            }
        } catch (e) {
            if (!e.isOffline) {
                this.showToast('error', 'Error', `Failed to ${wasMuted ? 'unmute' : 'mute'} issue`);
            }
        }
    },
};
