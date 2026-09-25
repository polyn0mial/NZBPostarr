// The Server Stats card: stat modules, their order editor and the sparkline history.
// Spread into the dashboard page by pages/dashboard/index.js.
import {
    appendHistoryPoint,
    computePositiveRateDelta,
    mapDeltaHistorySeries,
    mapHistorySeries,
    sparkWindow,
    sumNumericFields,
} from 'page-base';

import { normalizeOrderedIds } from './cards.js';

const SPARKLINE_MAX_POINTS = 90;

export const SERVER_STATS_MODULES = [
    { id: 'cpu', label: 'CPU', icon: 'cpu', color: 'orange-400' },
    { id: 'memory', label: 'Memory', icon: 'memory-stick', color: 'green-400' },
    { id: 'disk', label: 'Disk', icon: 'hard-drive', color: 'blue-400' },
    { id: 'free_space', label: 'Free Space', icon: 'database', color: 'cyan-400' },
    { id: 'upload', label: 'Upload', icon: 'arrow-up', color: 'purple-400' },
    { id: 'download', label: 'Download', icon: 'arrow-down', color: 'pink-400' },
    { id: 'connections', label: 'Connections', icon: 'share-2', color: 'indigo-400' },
    { id: 'net_errors', label: 'Net Errors', icon: 'alert-octagon', color: 'rose-400' },
];

export const serverStatsComputed = {
    // Sparkline data (limited to 30 points for visibility)
    sparkCpu() { return sparkWindow(this.history.cpu, 30); },
    sparkMemory() { return sparkWindow(this.history.memory, 30); },
    sparkUpload() { return sparkWindow(this.history.upload, 30); },
    sparkDownload() { return sparkWindow(this.history.download, 30); },
    sparkDisk() { return sparkWindow(this.history.disk, 30); },
    sparkFreeSpace() { return sparkWindow(this.history.freeSpace, 30); },
    sparkConns() { return sparkWindow(this.history.conns, 30); },
    sparkNetErrors() { return sparkWindow(this.history.netErrors, 30); },

    normalizedServerStatsOrder() {
        return normalizeOrderedIds(this.dashboardServerStatsOrder, SERVER_STATS_MODULES.map(module => module.id));
    },
    orderedEnabledServerStatsModules() {
        const enabledSet = new Set((this.uiSettings.dashboard_stats_modules || []).slice(0, 6));
        return this.normalizedServerStatsOrder.filter(moduleId => enabledSet.has(moduleId)).slice(0, 6);
    },
    // Unified list for drop-in repeated modules
    serverStatsList() {
        return this.orderedEnabledServerStatsModules.map(moduleId => this.buildServerStatsModule(moduleId)).filter(Boolean);
    },
    serverStatsEditorModules() {
        const enabledSet = new Set(this.orderedEnabledServerStatsModules);
        return this.normalizedServerStatsOrder.map(moduleId => {
            const module = this.buildServerStatsModule(moduleId);
            if (!module) return null;
            return {
                ...module,
                enabled: enabledSet.has(moduleId),
                order: enabledSet.has(moduleId) ? this.orderedEnabledServerStatsModules.indexOf(moduleId) + 1 : null,
            };
        }).filter(Boolean);
    }
};

export const serverStatsMethods = {
    buildServerStatsModule(moduleId) {
        switch (moduleId) {
            case 'cpu':
                return {
                    id: 'cpu',
                    title: 'CPU',
                    icon: 'cpu',
                    color: 'orange-400',
                    value: this.serverStats.cpu,
                    unit: '%',
                    progress: this.serverStats.cpu,
                    sparkData: this.sparkCpu,
                    peakKey: 'cpu',
                    valueSuffix: '%'
                };
            case 'memory':
                return {
                    id: 'memory',
                    title: 'Memory',
                    icon: 'memory-stick',
                    color: 'green-400',
                    value: this.serverStats.memory.percent,
                    unit: '%',
                    progress: this.serverStats.memory.percent,
                    sparkData: this.sparkMemory,
                    peakKey: 'memory',
                    valueSuffix: '%',
                    extra: `${this.formatBytes(this.serverStats.memory.used)} / ${this.formatBytes(this.serverStats.memory.total)}`
                };
            case 'disk':
                return {
                    id: 'disk',
                    title: 'Disk',
                    icon: 'hard-drive',
                    color: 'blue-400',
                    value: this.serverStats.disk.percent,
                    unit: '%',
                    progress: this.serverStats.disk.percent,
                    sparkData: this.sparkDisk,
                    peakKey: 'disk',
                    valueSuffix: '%',
                    extra: `${this.formatBytes(this.serverStats.disk.used)} / ${this.formatBytes(this.serverStats.disk.total)}`
                };
            case 'free_space':
                return {
                    id: 'free_space',
                    title: 'Free Space',
                    icon: 'database',
                    color: 'cyan-400',
                    value: this.formatBytes(this.serverStats.disk.free),
                    unit: '',
                    progress: (this.serverStats.disk.free / Math.max(1, this.serverStats.disk.total) * 100),
                    sparkData: this.sparkFreeSpace,
                    peakKey: 'freeSpace',
                    valueSuffix: ''
                };
            case 'upload':
                return {
                    id: 'upload',
                    title: 'Upload',
                    icon: 'arrow-up',
                    color: 'purple-400',
                    value: this.formatSpeed(this.serverStats.network.upload),
                    unit: '',
                    progress: Math.min(100, (this.serverStats.network.upload / 100 * 100)),
                    sparkData: this.sparkUpload,
                    peakKey: 'upload',
                    valueSuffix: 'MB/s'
                };
            case 'download':
                return {
                    id: 'download',
                    title: 'Download',
                    icon: 'arrow-down',
                    color: 'pink-400',
                    value: this.formatSpeed(this.serverStats.network.download),
                    unit: '',
                    progress: Math.min(100, (this.serverStats.network.download / 100 * 100)),
                    sparkData: this.sparkDownload,
                    peakKey: 'download',
                    valueSuffix: 'MB/s'
                };
            case 'connections':
                return {
                    id: 'connections',
                    title: 'Connections',
                    icon: 'share-2',
                    color: 'indigo-400',
                    value: this.serverStats.network.conns,
                    unit: '',
                    progress: Math.min(100, (this.serverStats.network.conns / 1000 * 100)),
                    sparkData: this.sparkConns,
                    peakKey: 'conns',
                    valueSuffix: ''
                };
            case 'net_errors': {
                const lastErr = this.history.netErrors.length > 0 ? this.history.netErrors[this.history.netErrors.length - 1].v : 0;
                return {
                    id: 'net_errors',
                    title: 'Net Errors',
                    icon: 'alert-octagon',
                    color: 'rose-400',
                    value: lastErr,
                    unit: '',
                    progress: Math.min(100, lastErr * 10),
                    sparkData: this.sparkNetErrors,
                    peakKey: 'netErrors',
                    valueSuffix: '',
                    extra: `Delta: ${lastErr}`
                };
            }
            default:
                return null;
        }
    },
    syncEnabledServerStatsModules() {
        const enabledSet = new Set((this.uiSettings.dashboard_stats_modules || []).slice(0, 6));
        this.uiSettings = {
            ...this.uiSettings,
            dashboard_stats_modules: this.normalizedServerStatsOrder.filter(moduleId => enabledSet.has(moduleId)).slice(0, 6),
        };
    },
    toggleServerStatsModule(moduleId) {
        const enabledSet = new Set((this.uiSettings.dashboard_stats_modules || []).slice(0, 6));
        if (enabledSet.has(moduleId)) {
            enabledSet.delete(moduleId);
        } else {
            if (enabledSet.size >= 6) {
                this.showToast('info', 'Limit Reached', 'You can enable a maximum of 6 server stats cards.');
                return;
            }
            enabledSet.add(moduleId);
        }
        this.uiSettings = {
            ...this.uiSettings,
            dashboard_stats_modules: this.normalizedServerStatsOrder.filter(id => enabledSet.has(id)).slice(0, 6),
        };
    },
    serverStatsModuleDragClass(moduleId) {
        if (!this.editMode) return '';
        if (this.serverStatsDragState.dragging === moduleId) return 'opacity-60 scale-[0.98]';
        if (this.serverStatsDragState.over === moduleId && this.serverStatsDragState.dragging && this.serverStatsDragState.dragging !== moduleId) {
            return 'ring-2 ring-notion-accent shadow-lg';
        }
        return '';
    },
    onServerStatsModuleDragStart(e, moduleId) {
        if (!this.editMode) {
            e.preventDefault();
            return;
        }
        this.serverStatsDragState.dragging = moduleId;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', moduleId);
    },
    onServerStatsModuleDragOver(e, moduleId) {
        if (!this.editMode || !this.serverStatsDragState.dragging) return;
        e.dataTransfer.dropEffect = 'move';
        this.serverStatsDragState.over = moduleId;
    },
    onServerStatsModuleDrop(e, moduleId) {
        if (!this.editMode) return;
        const fromId = this.serverStatsDragState.dragging;
        if (!fromId || fromId === moduleId) {
            this.serverStatsDragState = { dragging: null, over: null };
            return;
        }
        const order = [...this.normalizedServerStatsOrder];
        const fromIdx = order.indexOf(fromId);
        const toIdx = order.indexOf(moduleId);
        if (fromIdx < 0 || toIdx < 0) {
            this.serverStatsDragState = { dragging: null, over: null };
            return;
        }
        order.splice(fromIdx, 1);
        order.splice(toIdx, 0, fromId);
        this.dashboardServerStatsOrder = order;
        this.syncEnabledServerStatsModules();
        this.serverStatsDragState = { dragging: null, over: null };
    },
    onServerStatsModuleDragEnd() {
        this.serverStatsDragState = { dragging: null, over: null };
    },

    // --- SERVER STATS & SPARKLINES ---
    async loadStatsHistory() {
        if (!this.dashboardServerStatsActive()) return;
        try {
            const data = await this.apiFetch('/api/stats/history');
            const h = data.history || {};
            const nextHistoryId = () => this.historyIdCounter++;
            const targetLen = 30;

            this.history.cpu = mapHistorySeries(h.cpu || [], targetLen, nextHistoryId);
            this.history.memory = mapHistorySeries(h.memory || [], targetLen, nextHistoryId);
            this.history.upload = mapHistorySeries(h.upload_mbps || [], targetLen, nextHistoryId);
            this.history.download = mapHistorySeries(h.download_mbps || [], targetLen, nextHistoryId);
            this.history.disk = mapHistorySeries(h.disk || [], targetLen, nextHistoryId);
            this.history.freeSpace = mapHistorySeries(h.free || [], targetLen, nextHistoryId);
            this.history.conns = mapHistorySeries(h.connections || [], targetLen, nextHistoryId);
            this.history.netErrors = mapDeltaHistorySeries(h.network_errors || [], targetLen, nextHistoryId, 10);
        } catch (e) { console.error('Failed to load stats history:', e); }
    },

    async refreshServerStats() {
        if (!this.dashboardServerStatsActive()) return;
        if (this.isPollingStats) return;
        this.isPollingStats = true;
        try {
            const stats = await this.apiFetch('/api/dashboard/system-stats');
            this.serverStats = {
                hostname: stats.hostname,
                platform: stats.platform,
                uptime_seconds: stats.uptime_seconds,
                cpu: stats.cpu_percent,
                memory: {
                    used: (stats.memory_used_gb || 0) * 1024 * 1024 * 1024,
                    total: (stats.memory_total_gb || 0) * 1024 * 1024 * 1024,
                    percent: stats.memory_percent
                },
                disk: {
                    used: (stats.disk_used_gb || 0) * 1024 * 1024 * 1024,
                    total: (stats.disk_total_gb || 0) * 1024 * 1024 * 1024,
                    percent: stats.disk_percent,
                    free: (stats.disk_free_gb || 0) * 1024 * 1024 * 1024
                },
                network: {
                    upload: (stats.network_upload_mbps || 0) * 1024 * 1024,
                    download: (stats.network_download_mbps || 0) * 1024 * 1024,
                    conns: stats.conns,
                    errors: sumNumericFields(stats, ['errin', 'errout', 'dropin', 'dropout'])
                }
            };

            const currentErrors = this.serverStats.network.errors;
            const intervalSecs = Math.max(1, (this.uiRefreshRate || 2000) / 1000);
            const errorDelta = computePositiveRateDelta(currentErrors, this.lastTotals.errors, intervalSecs);
            this.lastTotals.errors = currentErrors;

            const nextHistoryId = () => this.historyIdCounter++;
            appendHistoryPoint(this.history.cpu, stats.cpu_percent, nextHistoryId, SPARKLINE_MAX_POINTS);
            appendHistoryPoint(this.history.memory, stats.memory_percent, nextHistoryId, SPARKLINE_MAX_POINTS);
            appendHistoryPoint(this.history.upload, stats.network_upload_mbps, nextHistoryId, SPARKLINE_MAX_POINTS);
            appendHistoryPoint(this.history.download, stats.network_download_mbps, nextHistoryId, SPARKLINE_MAX_POINTS);
            appendHistoryPoint(this.history.disk, stats.disk_percent, nextHistoryId, SPARKLINE_MAX_POINTS);
            appendHistoryPoint(this.history.freeSpace, stats.disk_free_gb, nextHistoryId, SPARKLINE_MAX_POINTS);
            appendHistoryPoint(this.history.conns, stats.conns, nextHistoryId, SPARKLINE_MAX_POINTS);
            appendHistoryPoint(this.history.netErrors, errorDelta, nextHistoryId, SPARKLINE_MAX_POINTS);
        } catch (e) { console.error('Failed to refresh server stats:', e); }
        finally { this.isPollingStats = false; }
    },
    dashboardServerStatsActive() {
        return !!(this.uiSettings.dashboard_stats_enabled && (this.uiSettings.dashboard_stats_modules || []).length);
    }
};
