// Dashboard card layout: card order, enabled cards and layout edit mode.
// Spread into the dashboard page by pages/dashboard/index.js.
export const DASHBOARD_CARD_IDS = ['overview', 'new-upload', 'usenet-stream', 'active-jobs', 'console', 'server-stats'];
export const DASHBOARD_CARD_DEFAULTS = {
    overview: true,
    'new-upload': true,
    'usenet-stream': true,
    'active-jobs': true,
    console: true,
    'server-stats': true,
};

export function normalizeOrderedIds(savedOrder, allowedIds) {
    const normalized = Array.isArray(savedOrder) ? savedOrder.filter(id => allowedIds.includes(id)) : [];
    const seen = new Set();
    const unique = [];
    normalized.forEach(id => {
        if (!seen.has(id)) {
            seen.add(id);
            unique.push(id);
        }
    });
    allowedIds.forEach(id => {
        if (!seen.has(id)) {
            unique.push(id);
        }
    });
    return unique;
}

export function normalizeEnabledMap(savedMap) {
    const normalized = { ...DASHBOARD_CARD_DEFAULTS };
    if (savedMap && typeof savedMap === 'object') {
        Object.keys(DASHBOARD_CARD_DEFAULTS).forEach(cardId => {
            normalized[cardId] = savedMap[cardId] !== false;
        });
    }
    return normalized;
}

export const cardMethods = {
    // --- LAYOUT EDITING ---
    async toggleEditMode() {
        if (this.editMode) {
            const saved = await this.saveDashboardPreferences();
            if (saved) {
                this.showToast('success', 'Layout Saved', 'Dashboard layout and server stats preferences have been saved');
            } else {
                this.showToast('error', 'Save Failed', 'Dashboard layout was saved locally, but server stats preferences could not be saved');
            }
        }
        this.editMode = !this.editMode;
    },
    isDashboardCardEnabled(cardId) {
        return this.dashboardCardEnabled[cardId] !== false;
    },
    shouldRenderCard(cardId) {
        return this.editMode || this.isDashboardCardEnabled(cardId);
    },
    toggleDashboardCard(cardId) {
        this.dashboardCardEnabled = {
            ...this.dashboardCardEnabled,
            [cardId]: !this.isDashboardCardEnabled(cardId),
        };
    },
    dashboardCardStateClass(cardId) {
        if (!this.editMode || this.isDashboardCardEnabled(cardId)) return '';
        return 'dashboard-card-disabled';
    },
    cardOrder(cardId) {
        const idx = this.dashboardCardOrder.indexOf(cardId);
        return idx >= 0 ? idx : 99;
    },
    cardDragClass(cardId) {
        if (!this.editMode) return '';
        if (this.dragState.dragging === cardId) return 'opacity-50 scale-[0.98]';
        if (this.dragState.over === cardId && this.dragState.dragging && this.dragState.dragging !== cardId) return 'ring-2 ring-notion-accent shadow-lg';
        return 'ring-1 ring-notion-accent/25';
    },
    onCardDragStart(e, cardId) {
        if (!this.editMode) { e.preventDefault(); return; }
        this.dragState.dragging = cardId;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', cardId);
    },
    onCardDragOver(e, cardId) {
        if (!this.editMode || !this.dragState.dragging) return;
        e.dataTransfer.dropEffect = 'move';
        this.dragState.over = cardId;
    },
    onCardDrop(e, cardId) {
        if (!this.editMode) return;
        const fromId = this.dragState.dragging;
        if (!fromId || fromId === cardId) { this.dragState = { dragging: null, over: null }; return; }
        const arr = [...this.dashboardCardOrder];
        const fromIdx = arr.indexOf(fromId);
        const toIdx = arr.indexOf(cardId);
        if (fromIdx < 0 || toIdx < 0) { this.dragState = { dragging: null, over: null }; return; }
        arr.splice(fromIdx, 1);
        arr.splice(toIdx, 0, fromId);
        this.dashboardCardOrder = arr;
        this.dragState = { dragging: null, over: null };
    },
    onCardDragEnd() {
        this.dragState = { dragging: null, over: null };
    },
    async saveDashboardPreferences() {
        this.syncEnabledServerStatsModules();
        try {
            await this.apiPut('/api/settings/ui', {
                dashboard_stats_enabled: this.uiSettings.dashboard_stats_enabled,
                ui_refresh_seconds: 2,
                dashboard_stats_modules: this.orderedEnabledServerStatsModules,
            });
            return true;
        } catch (e) {
            console.error('Failed to save dashboard preferences:', e);
            return false;
        }
    },
};
