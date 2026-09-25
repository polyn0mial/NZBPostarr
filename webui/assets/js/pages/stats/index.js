import {
    appendHistoryPoint,
    computePositiveRateDelta,
    createVuePage,
    mapDeltaHistorySeries,
    mapHistorySeries,
    seedHistorySeries,
    sparkWindow,
    sumNumericFields,
} from 'page-base';

let vue = null;

vue = createVuePage({
    data() {
        return {
            fullStats: null,
            topDirs: [],
            loadingTopDirs: false,
            topDirsLoaded: false,
            topDirsStatus: 'Click to load',
            maxDirSize: 0,

            // Sparkline History
            history: {
                cpu: [],
                load: [],
                memory: [],
                swap: [],
                disk: [],
                freeSpace: [],
                upload: [],
                download: [],
                sent: [],
                recv: [],
                conns: [],
                read: [],
                write: [],
                errors: []
            },
            ifaceHistory: {}, // { ifaceName: { upload: [], download: [] } }
            historyMax: 45,
            lastTotals: { sent: null, recv: null, errors: null },
            historyLoaded: false,

            expandedSections: {
                'system-info': true,
                'disk-partitions-info': true,
                'top-dirs': false,
                'cpu-cores': true,
                'network': true,
                'disk-io': true,
                'cpu-procs': false,
                'mem-procs': true,
                'net-procs': true,
                'disk-procs': true,
                'users': false,
                'sensors': false
            },
            statsInterval: null
        };
    },
    computed: {
        sparkCpu() { return sparkWindow(this.history.cpu, 45); },
        sparkLoad() { return sparkWindow(this.history.load, 45); },
        sparkMemory() { return sparkWindow(this.history.memory, 45); },
        sparkSwap() { return sparkWindow(this.history.swap, 45); },
        sparkDisk() { return sparkWindow(this.history.disk, 45); },
        sparkFreeSpace() { return sparkWindow(this.history.freeSpace, 45); },
        sparkUpload() { return sparkWindow(this.history.upload, 45); },
        sparkDownload() { return sparkWindow(this.history.download, 45); },
        sparkSent() { return sparkWindow(this.history.sent, 45); },
        sparkRecv() { return sparkWindow(this.history.recv, 45); },
        sparkConns() { return sparkWindow(this.history.conns, 45); },
        sparkRead() { return sparkWindow(this.history.read, 45); },
        sparkWrite() { return sparkWindow(this.history.write, 45); },
        sparkErrors() { return sparkWindow(this.history.errors, 45); },

        cpuGridStyle() {
            return {
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(80px, 1fr))',
                gap: '0.75rem',
                width: '100%'
            };
        }
    },
    methods: {
        async loadStatsHistory() {
            if (this.historyLoaded || !this.isConnected) return;
            try {
                const data = await this.apiFetch('/api/stats/history');
                const h = data.history || {};

                const targetLen = this.historyMax || 45;
                const nextHistoryId = () => this.historyIdCounter++;

                this.history.cpu = mapHistorySeries(h.cpu, targetLen, nextHistoryId);
                this.history.load = mapHistorySeries(h.load, targetLen, nextHistoryId);
                this.history.memory = mapHistorySeries(h.memory, targetLen, nextHistoryId);
                this.history.swap = mapHistorySeries(h.swap, targetLen, nextHistoryId);
                this.history.disk = mapHistorySeries(h.disk, targetLen, nextHistoryId);
                this.history.freeSpace = mapHistorySeries(h.free, targetLen, nextHistoryId);
                this.history.upload = mapHistorySeries(h.upload_mbps, targetLen, nextHistoryId);
                this.history.download = mapHistorySeries(h.download_mbps, targetLen, nextHistoryId);
                this.history.sent = mapDeltaHistorySeries(h.total_sent_mb, targetLen, nextHistoryId, 10);
                this.history.recv = mapDeltaHistorySeries(h.total_recv_mb, targetLen, nextHistoryId, 10);
                this.history.errors = mapDeltaHistorySeries(h.network_errors, targetLen, nextHistoryId, 10);
                this.history.conns = mapHistorySeries(h.connections, targetLen, nextHistoryId);
                this.history.read = mapHistorySeries(h.disk_read, targetLen, nextHistoryId);
                this.history.write = mapHistorySeries(h.disk_write, targetLen, nextHistoryId);

                // Load per-interface history
                if (data.interfaces) {
                    Object.entries(data.interfaces).forEach(([name, speeds]) => {
                        this.ifaceHistory[name] = {
                            upload: mapHistorySeries(speeds.upload, targetLen, nextHistoryId),
                            download: mapHistorySeries(speeds.download, targetLen, nextHistoryId)
                        };
                    });
                }

                const dataFull = await this.apiFetch('/api/stats/full');
                if (dataFull) {
                    this.lastTotals.sent = dataFull.network.total_sent_mb || 0;
                    this.lastTotals.recv = dataFull.network.total_recv_mb || 0;
                    this.lastTotals.errors = sumNumericFields(dataFull.network, ['errors_in', 'errors_out', 'drops_in', 'drops_out']);
                }

                this.historyLoaded = true;
            } catch (e) {
                console.error('Failed to load stats history:', e);
            }
        },

        async loadStats(manual = false) {
            if (!this.isConnected) return;
            try {
                const collapsed = Object.keys(this.expandedSections).filter(k => !this.expandedSections[k]).join(',');
                const data = await this.apiFetch(`/api/stats/full?collapsed=${collapsed}`);
                this.fullStats = data;
                this.markUpdated();

                // Calculate deltas
                const currentSent = data.network.total_sent_mb || 0;
                const currentRecv = data.network.total_recv_mb || 0;
                const currentErrors = sumNumericFields(data.network, ['errors_in', 'errors_out', 'drops_in', 'drops_out']);

                const intervalSecs = (this.uiRefreshRate / 1000) || 1;
                const sentDelta = computePositiveRateDelta(currentSent, this.lastTotals.sent, intervalSecs);
                const recvDelta = computePositiveRateDelta(currentRecv, this.lastTotals.recv, intervalSecs);
                const errorDelta = computePositiveRateDelta(currentErrors, this.lastTotals.errors, intervalSecs);

                this.lastTotals.sent = currentSent;
                this.lastTotals.recv = currentRecv;
                this.lastTotals.errors = currentErrors;

                const nextHistoryId = () => this.historyIdCounter++;
                appendHistoryPoint(this.history.cpu, data.cpu?.percent || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.load, data.cpu?.load?.[0] || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.memory, data.memory?.percent || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.swap, data.swap?.percent || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.disk, data.disk?.percent || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.freeSpace, data.disk?.free_gb || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.upload, data.network.upload_mbps || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.download, data.network.download_mbps || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.sent, sentDelta, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.recv, recvDelta, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.conns, data.network.connections_count || 0, nextHistoryId, this.historyMax);
                appendHistoryPoint(this.history.errors, errorDelta, nextHistoryId, this.historyMax);

                if (data.disk.io) {
                    appendHistoryPoint(this.history.read, (data.disk.io.read_bytes || 0) / (1024 * 1024), nextHistoryId, this.historyMax);
                    appendHistoryPoint(this.history.write, (data.disk.io.write_bytes || 0) / (1024 * 1024), nextHistoryId, this.historyMax);
                }

                // Per-Interface history
                if (data.network.interfaces) {
                    data.network.interfaces.forEach(iface => {
                        if (!this.ifaceHistory[iface.name]) {
                            this.ifaceHistory[iface.name] = {
                                upload: seedHistorySeries(30, nextHistoryId),
                                download: seedHistorySeries(30, nextHistoryId)
                            };
                        }
                        const h = this.ifaceHistory[iface.name];
                        const speeds = iface.speeds || { upload: 0, download: 0 };
                        appendHistoryPoint(h.upload, speeds.upload, nextHistoryId, this.historyMax);
                        appendHistoryPoint(h.download, speeds.download, nextHistoryId, this.historyMax);
                    });
                }

                // Only refresh top directories on manual button click or mount, not background poll (prevents flashing)
                if (manual && this.isSectionExpanded('top-dirs')) {
                    this.loadTopDirectories();
                }
            } catch (e) {
                console.error('Failed to load stats:', e);
            }
        },

        toggleStatsSection(sectionId) {
            this.toggleSection(sectionId);
            if (sectionId === 'top-dirs' && this.isSectionExpanded('top-dirs') && !this.topDirsLoaded) {
                this.loadTopDirectories();
            }
        },

        async loadTopDirectories() {
            if (this.loadingTopDirs) return;
            this.loadingTopDirs = true;
            this.topDirsStatus = this.topDirsLoaded ? 'Refreshing...' : 'Loading...';

            try {
                const data = await this.apiFetch('/api/stats/top-directories?limit=25');
                if (data.directories) {
                    // Update max size BEFORE assigning list to prevent bar flickering
                    const newMax = Math.max(...data.directories.map(d => d.size), 0);
                    this.maxDirSize = newMax;
                    this.topDirs = data.directories;
                    this.topDirsStatus = `${data.directories.length} directories`;
                    this.topDirsLoaded = true;
                }
            } catch (e) {
                console.error('Failed to load top directories:', e);
                this.topDirsStatus = 'Error loading';
            } finally {
                this.loadingTopDirs = false;
            }
        },

        getDirWidth(size) {
            return this.maxDirSize > 0 ? Math.max(2, (size / this.maxDirSize) * 100) : 0;
        },

        // ============================================================
        //  PRETTIER-SAFE CLASS HELPERS
        // ============================================================

        // Battery icon class
        batteryIconClass() {
            const base = 'size-5';
            const lowBattery = (this.fullStats?.battery?.percent || 0) < 20 && !this.fullStats?.battery?.plugged;
            return lowBattery ? `${base} text-red-400` : `${base} text-green-400`;
        },

        // Process status badge class
        processStatusClass(status) {
            const base = 'px-1 py-0.5 text-[10px] rounded';
            return status === 'running' ? `${base} bg-green-500/15 text-green-400` : `${base} bg-notion-bg-hover text-notion-text-tertiary`;
        },

        // CPU cell class (highlight high usage)
        cpuCellClass(cpu) {
            return cpu > 50 ? 'text-orange-400' : 'text-notion-text-secondary';
        },

        // Memory cell class (highlight high usage)
        memoryCellClass(memory) {
            return memory > 20 ? 'text-green-400' : 'text-notion-text-secondary';
        },

        // Connections cell class (highlight many connections)
        connectionsCellClass(connections) {
            return connections > 10 ? 'text-purple-400' : 'text-notion-text-secondary';
        },

        // Temperature sensor class (color by temperature)
        temperatureClass(temp) {
            if (temp >= 80) return 'text-red-400';
            if (temp >= 60) return 'text-yellow-400';
            return 'text-green-400';
        },

        // Battery status text
        getBatteryStatus() {
            if (this.fullStats?.battery?.plugged) return 'Plugged In';
            if (this.fullStats?.battery?.secs_left) {
                return Math.floor(this.fullStats.battery.secs_left / 60) + ' min remaining';
            }
            return 'On Battery';
        }
    },
    mounted() {
        // Load initial refresh rate from settings first
        this.apiFetch('/api/settings').then(settings => {
            if (settings && settings.ui && settings.ui.ui_refresh_seconds) {
                this.uiRefreshRate = settings.ui.ui_refresh_seconds * 1000;
            }

            this.loadStatsHistory().then(() => {
                this.loadStats();

                // Load top directories if expanded on mount
                if (this.isSectionExpanded('top-dirs') && !this.topDirsLoaded) {
                    this.loadTopDirectories();
                }
            });
            this.startInterval(this.loadStats, this.uiRefreshRate);
        });
    }
});
