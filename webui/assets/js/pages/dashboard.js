import {
    appendHistoryPoint,
    computePositiveRateDelta,
    createVuePage,
    mapDeltaHistorySeries,
    mapHistorySeries,
    sumNumericFields,
} from 'page-base';

const SPARKLINE_MAX_POINTS = 90;
const CONSOLE_MAX_LINES = 300;
const DASHBOARD_CARD_IDS = ['overview', 'new-upload', 'usenet-stream', 'active-jobs', 'console', 'server-stats'];
const DASHBOARD_CARD_DEFAULTS = {
    overview: true,
    'new-upload': true,
    'usenet-stream': true,
    'active-jobs': true,
    console: true,
    'server-stats': true,
};
const SERVER_STATS_MODULES = [
    { id: 'cpu', label: 'CPU', icon: 'cpu', color: 'orange-400' },
    { id: 'memory', label: 'Memory', icon: 'memory-stick', color: 'green-400' },
    { id: 'disk', label: 'Disk', icon: 'hard-drive', color: 'blue-400' },
    { id: 'free_space', label: 'Free Space', icon: 'database', color: 'cyan-400' },
    { id: 'upload', label: 'Upload', icon: 'arrow-up', color: 'purple-400' },
    { id: 'download', label: 'Download', icon: 'arrow-down', color: 'pink-400' },
    { id: 'connections', label: 'Connections', icon: 'share-2', color: 'indigo-400' },
    { id: 'net_errors', label: 'Net Errors', icon: 'alert-octagon', color: 'rose-400' },
];

function normalizeOrderedIds(savedOrder, allowedIds) {
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

function normalizeEnabledMap(savedMap) {
    const normalized = { ...DASHBOARD_CARD_DEFAULTS };
    if (savedMap && typeof savedMap === 'object') {
        Object.keys(DASHBOARD_CARD_DEFAULTS).forEach(cardId => {
            normalized[cardId] = savedMap[cardId] !== false;
        });
    }
    return normalized;
}

function normalizeStringArray(values) {
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

const dashboard = createVuePage({
    persist: ['consoleConfig', 'uploadForm', 'dashboardCardOrder', 'dashboardCardEnabled', 'dashboardServerStatsOrder'],
    data() {
        return {
            loading: false,
            // Layout editing
            editMode: false,
            dashboardCardOrder: [...DASHBOARD_CARD_IDS],
            dashboardCardEnabled: { ...DASHBOARD_CARD_DEFAULTS },
            dashboardServerStatsOrder: SERVER_STATS_MODULES.map(module => module.id),
            dragState: { dragging: null, over: null },
            serverStatsDragState: { dragging: null, over: null },
            streamLoading: false,
            // New Upload Form State
            uploadForm: {
                file_path: '',
                upload_type: 'movie',
                categories: ['all'],
                folder_paths: [],
                limit: null,
                indexer_ids: [],
                test_mode: false,
                full_tv_test: false,
                skip_duplicate_check: false,
                skip_packs: false,
                skip_episodes: false
            },
            activeUploadDropdown: null,
            configuredFolderPaths: [],
            streamForm: {
                source_mode: 'upload',
                server_path: '',
                monitor_folder: false,
                selectedFile: null,
                selectedFileName: '',
                release_name: '',
                category: '',
                indexer_id: null,
                submit_mode: 'post_and_submit',
                posting_server_name: null,
                test_mode: false,
                skip_duplicate_check: false,
                show_more_options: false,
            },
            streamFolderBrowser: {
                open: false,
                currentPath: '/',
                parentPath: null,
                dirs: [],
                loading: false,
            },
            // Indexer Data
            loadedIndexers: [],
            nntpServers: [],
            streamMonitors: [],
            // Server Context
            serverStats: {
                hostname: '',
                platform: '',
                uptime_seconds: 0,
                cpu: 0,
                memory: { used: 0, total: 0, percent: 0 },
                disk: { used: 0, total: 0, percent: 0, free: 0 },
                network: { upload: 0, download: 0 }
            },
            // UI Settings & Identity
            poster_name: 'Anonymous',
            uiSettings: {
                dashboard_stats_enabled: true,
                stats_page_enabled: true,
                ui_refresh_seconds: 2,
                dashboard_stats_modules: ["cpu", "memory", "disk", "free_space", "upload", "download"]
            },
            // Processing filters (from settings)
            processingFilters: {
                process_tv_episodes: true,
            },
            // Available categories from backend
            backendCategories: [],
            // History for Sparklines
            history: {
                cpu: [],
                memory: [],
                upload: [],
                download: [],
                disk: [],
                freeSpace: [],
                conns: [],
                netErrors: []
            },
            historyIdCounter: 0,
            lastTotals: { errors: null },
            // Overview & More Stats
            stats: {
                tv: { pending: 0, complete: 0, episodes: { uploaded: 0, total: 0 }, total: 0 },
                movies: { pending: 0, complete: 0, total: 0 },
                today: 0,
                total: 0,
                speed: 0,
                gbPerHour: 0,
                episodes: 0,
                seasonPacks: 0,
                totalInventory: { pending: 0, complete: 0 },
                breakdown: { movies: {}, tv: {}, misc: {} }
            },
            jobs: [],

            // Queue items peek (for "Up Next" display)
            nextQueueItem: null,
            queueItemCount: 0,

            // Console State
            consoleLogs: [],
            consoleSeq: 0,
            isPollingConsole: false,
            consoleConfig: {
                wide: false,
                wordWrap: true,
                autoScroll: true,
                height: 384 // h-96
            },
            activeUploadLines: new Map(), // Element index -> destination
            activePreparingLine: null, // Element index

            // Section Expansion
            expandedSections: {
                'new-upload': true,
                'usenet-stream': true,
                'overview': true,
                'console': true,
                'active-jobs': false,
                'server-stats': true,
            },

            // Polling Locks
            isPollingDashboard: false,
            isPollingJobs: false,
            isPollingStats: false,

            // Update banner state
            dismissedUpdateVersion: localStorage.getItem('nzbpostarr_update_banner_dismissed') || ''
        };
    },
    created() {
        this.dashboardCardOrder = normalizeOrderedIds(this.dashboardCardOrder, DASHBOARD_CARD_IDS);
        this.dashboardCardEnabled = normalizeEnabledMap(this.dashboardCardEnabled);
        this.dashboardServerStatsOrder = normalizeOrderedIds(this.dashboardServerStatsOrder, SERVER_STATS_MODULES.map(module => module.id));
        this.normalizeUploadFormState();
    },
    computed: {
        isJobRunning() {
            return this.jobs.some(j => j.status === 'running');
        },
        runningJobsCount() {
            return this.jobs.filter(j => j.status === 'running' || j.status === 'queued').length;
        },
        primaryRunningJob() {
            return this.jobs.find(j => j.status === 'running');
        },
        showUpdateBanner() {
            if (!this.updateStatus || !this.updateStatus.update_available) return false;
            const latest = String(this.updateStatus.latest_version || '').trim();
            if (!latest) return false;
            return this.dismissedUpdateVersion !== latest;
        },
        // Processing filter helpers
        isTvEnabled() {
            return true;
        },
        isMoviesEnabled() {
            return true;
        },
        availableCategories() {
            const cats = [];
            if (this.backendCategories.length > 0) {
                // Dynamic: use categories from backend
                const hasMovies = this.isMoviesEnabled && this.backendCategories.some(c => c.id === 'movies');
                const hasTv = this.isTvEnabled && this.backendCategories.some(c => c.id === 'tv');
                if (hasMovies) cats.push({ value: 'movies', label: 'Movies' });
                if (hasTv) cats.push({ value: 'tv', label: 'TV Shows' });
                if (hasMovies && hasTv) cats.push({ value: 'both', label: 'Both (Movies → TV)' });
                // Add all other non-tv categories (misc, and any custom)
                this.backendCategories.forEach(c => {
                    if (c.id !== 'movies' && c.id !== 'tv') {
                        cats.push({ value: c.id, label: c.label });
                    }
                });
            } else {
                // Fallback when categories haven't loaded yet
                if (this.isMoviesEnabled) cats.push({ value: 'movies', label: 'Movies' });
                if (this.isTvEnabled) cats.push({ value: 'tv', label: 'TV Shows' });
                if (this.isMoviesEnabled && this.isTvEnabled) cats.push({ value: 'both', label: 'Both (Movies → TV)' });
                cats.push({ value: 'misc', label: 'Misc' });
            }
            if (cats.length > 1) cats.push({ value: 'all', label: 'All' });
            return cats;
        },
        uploadCategoryOptions() {
            return this.availableCategories.filter(cat => cat.value !== 'both');
        },
        uploadIndexerOptions() {
            return (this.loadedIndexers || []).filter(indexer => indexer.enabled);
        },
        uploadFolderPathOptions() {
            return (this.configuredFolderPaths || []).map(folder => ({
                value: folder.path,
                label: this.formatUploadFolderLabel(folder),
                category: folder.category,
                path: folder.path,
            }));
        },
        streamCategories() {
            const categories = this.availableCategories.filter(cat => !['all', 'both'].includes(cat.value));
            return categories.length ? categories : [{ value: 'misc', label: 'Misc' }];
        },
        streamPostingServers() {
            const enabled = (this.nntpServers || []).filter(server => server.enabled !== false);
            return enabled.length ? enabled : (this.nntpServers || []);
        },
        isStreamUploadMode() {
            return this.streamForm.source_mode !== 'server_path';
        },
        isStreamPathMode() {
            return !this.isStreamUploadMode;
        },
        streamUsesIndexerSubmission() {
            return this.streamForm.submit_mode !== 'post_only';
        },
        streamMonitorRows() {
            const jobsById = new Map((this.jobs || []).map(job => [job.job_id, job]));
            return (this.streamMonitors || []).map(monitor => {
                const lastJob = monitor && typeof monitor.last_job === 'object' ? monitor.last_job : null;
                const currentJob = lastJob && lastJob.job_id ? jobsById.get(lastJob.job_id) || null : null;
                const displayJob = currentJob || lastJob;
                const active = currentJob && ['queued', 'running', 'paused', 'stopping'].includes(currentJob.status);
                const updatedAt = (displayJob && (displayJob.updated_at || displayJob.started_at)) || monitor.created_at || '';
                return {
                    ...monitor,
                    currentJob,
                    displayJob,
                    active: Boolean(active),
                    updatedAt,
                };
            }).sort((left, right) => {
                if (left.active !== right.active) return left.active ? -1 : 1;
                return String(right.updatedAt || '').localeCompare(String(left.updatedAt || ''));
            });
        },
        showFullTvTest() {
            if (!this.uploadForm.test_mode) return false;
            if (this.uploadForm.file_path) {
                return this.uploadForm.upload_type === 'tv_episode' || this.uploadForm.upload_type === 'tv_show';
            }
            const categories = this.getNormalizedUploadCategories();
            return categories.includes('all') || categories.includes('tv');
        },
        uploadTargetsMoviesOnly() {
            if (this.uploadForm.file_path) {
                return !String(this.uploadForm.upload_type || '').startsWith('tv');
            }
            const categories = this.getNormalizedUploadCategories();
            return !categories.includes('all') && categories.length === 1 && categories[0] === 'movies';
        },
        // Computed class strings for Prettier-safe templates
        submitButtonText() {
            if (this.loading) return 'Starting...';
            if (this.isJobRunning) return 'Stop Upload';
            return 'Start Bulk Upload';
        },
        categorySelectClass() {
            return this.uploadForm.file_path ? 'opacity-50 cursor-not-allowed' : '';
        },
        connectionDotClass() {
            return this.isConnected ? 'bg-green-500' : 'bg-yellow-500';
        },
        consoleSectionClass() {
            return this.consoleConfig.wide
                ? 'z-20 -mx-[10vw] w-[calc(100%+20vw)] max-w-none bg-notion-bg-secondary border border-notion-border rounded-lg overflow-hidden relative'
                : 'bg-notion-bg-secondary border border-notion-border rounded-lg overflow-hidden relative';
        },
        consoleInnerClass() {
            const wrapClass = this.consoleConfig.wordWrap ? 'whitespace-pre-wrap' : 'whitespace-nowrap';
            const overflowX = this.consoleConfig.wordWrap ? 'overflow-x-hidden' : 'overflow-x-auto';
            return `overflow-y-auto ${overflowX} p-3 font-mono text-xs bg-notion-bg space-y-0.5 ${wrapClass}`;
        },
        // Sparkline data (limited to 30 points for visibility)
        sparkCpu() { return this.history.cpu.slice(-30); },
        sparkMemory() { return this.history.memory.slice(-30); },
        sparkUpload() { return this.history.upload.slice(-30); },
        sparkDownload() { return this.history.download.slice(-30); },
        sparkDisk() { return this.history.disk.slice(-30); },
        sparkFreeSpace() { return this.history.freeSpace.slice(-30); },
        sparkConns() { return this.history.conns.slice(-30); },
        sparkNetErrors() { return this.history.netErrors.slice(-30); },

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
    },
    methods: {
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
        dismissUpdateBanner() {
            const latest = String((this.updateStatus && this.updateStatus.latest_version) || '').trim();
            if (!latest) return;
            this.dismissedUpdateVersion = latest;
            localStorage.setItem('nzbpostarr_update_banner_dismissed', latest);
        },
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
        isServerStatsModuleEnabled(moduleId) {
            return (this.uiSettings.dashboard_stats_modules || []).includes(moduleId);
        },
        getServerStatsModuleOrder(moduleId) {
            const idx = this.orderedEnabledServerStatsModules.indexOf(moduleId);
            return idx >= 0 ? idx + 1 : null;
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
        // --- TEMPLATE HELPERS ---
        jobInfoClass() { return 'text-notion-text-secondary truncate max-w-[70%]'; },
        jobPctClass() { return 'text-notion-text-primary font-medium'; },
        formatIndexerStats(stats) {
            if (!stats) return '0 / 0';

            let sObj = stats;
            if (typeof stats === 'string') {
                try {
                    sObj = JSON.parse(stats);
                } catch (e) {
                    return stats; // Return raw string if not JSON
                }
            }

            const success = sObj.success ?? sObj.complete ?? 0;
            const failed = sObj.failed ?? sObj.fail ?? 0;

            if (failed === 0) return success.toLocaleString();
            return `${success.toLocaleString()} / ${failed.toLocaleString()}`;
        },
        jobStatusBgClass(job) {
            const cfg = this.getStatusConfig(job.status);
            return `size-8 rounded-lg flex items-center justify-center ${cfg.bg}`;
        },
        jobSpeedBadgeClass() {
            return 'flex items-center px-2 bg-notion-accent/10 rounded font-medium text-notion-accent gap-1.5 py-0.5 text-[10px] whitespace-nowrap';
        },
        jobStatusBadgeClass() {
            return 'font-medium uppercase px-2 rounded bg-notion-bg-hover text-notion-text-secondary text-[10px] py-0.5 whitespace-nowrap';
        },
        monitorRowStatusClass(monitor) {
            const job = monitor.displayJob;
            if (!job) return 'bg-notion-bg-hover text-notion-text-tertiary';
            const status = String(job.status || '').toLowerCase();
            if (status === 'completed') return 'bg-notion-success/10 text-notion-success';
            if (status === 'failed' || status === 'stopped' || status === 'cancelled') return 'bg-notion-error/10 text-notion-error';
            if (status === 'paused') return 'bg-notion-warning/10 text-notion-warning';
            if (status === 'running' || status === 'stopping' || status === 'queued') return 'bg-notion-accent/10 text-notion-accent';
            return 'bg-notion-bg-hover text-notion-text-secondary';
        },
        monitorRowStatusLabel(monitor) {
            const job = monitor.displayJob;
            if (!job) return 'Watching';
            const status = String(job.status || '').toLowerCase();
            if (status === 'completed') return 'Last Job Completed';
            if (status === 'failed') return 'Last Job Failed';
            if (status === 'paused') return 'Paused';
            if (status === 'queued') return 'Queued';
            if (status === 'stopping') return 'Stopping';
            if (status === 'cancelled') return 'Cancelled';
            if (status === 'running') return 'Running';
            return 'Watching';
        },
        monitorRowTitle(monitor) {
            const job = monitor.displayJob;
            if (job && (job.current_item || job.display_name)) {
                return job.current_item || job.display_name;
            }
            const parts = String(monitor.folder_path || '').replace(/\\/g, '/').split('/').filter(Boolean);
            return parts.length ? parts[parts.length - 1] : 'Stream Monitor';
        },
        monitorRowInfo(monitor) {
            const job = monitor.displayJob;
            if (job) return this.jobItemInfo(job);
            return 'Watching for new NZB files';
        },
        monitorRowPercent(monitor) {
            const job = monitor.displayJob;
            return job ? this.jobItemPercent(job) : '0%';
        },
        monitorRowMeta(monitor) {
            const pieces = [];
            if (monitor.posting_server_name) pieces.push(monitor.posting_server_name);
            pieces.push(monitor.submit_mode === 'post_only' ? 'Post Only' : 'Post and Submit');
            if (monitor.indexer_id) pieces.push(`Indexer ${monitor.indexer_id}`);
            return pieces.join(' • ');
        },
        monitorRowUpdated(monitor) {
            if (!monitor.updatedAt) return 'No jobs yet';
            return this.formatDate(monitor.updatedAt);
        },
        logClass(log) {
            const levelMap = {
                'DEBUG': 'text-notion-text-tertiary',
                'INFO': 'text-notion-text-secondary',
                'SUCCESS': 'text-notion-success',
                'WARN': 'text-notion-warning',
                'ERROR': 'text-notion-error',
                'PROGRESS': 'text-notion-accent'
            };
            return `leading-relaxed ${levelMap[log.level] || 'text-notion-text-secondary'}`;
        },

        parseLogTimestamp(ts) {
            if (!ts) return { date: '', time: '' };

            let date;
            try {
                if (ts.includes('T') || ts.includes('Z')) {
                    date = new Date(ts);
                } else {
                    const parts = ts.trim().split(' ');
                    if (parts.length === 2) {
                        const [datePart, timePart] = parts;
                        const [year, month, day] = datePart.split('-').map(Number);
                        const [hour, minute, second] = timePart.split(':').map(Number);
                        date = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
                    } else {
                        date = new Date(ts);
                    }
                }
            } catch (e) {
                return { date: '', time: ts };
            }

            if (!date || isNaN(date.getTime())) return { date: '', time: ts };

            // Hour 'numeric' avoids leading zero (e.g. 8:46 PM vs 08:46 PM)
            const timeStr = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
            const dateStr = date.toLocaleDateString();
            return { date: dateStr, time: timeStr };
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

        browseFile() { if (this.$refs.filePicker) this.$refs.filePicker.click(); },
        browseFolder() { if (this.$refs.folderPicker) this.$refs.folderPicker.click(); },
        browseStreamFile() { if (this.$refs.streamNzbPicker) this.$refs.streamNzbPicker.click(); },
        async openStreamFolderBrowser() {
            this.streamFolderBrowser.open = true;
            await this.browseStreamTo(this.streamForm.server_path || '/');
        },
        closeStreamFolderBrowser() {
            this.streamFolderBrowser.open = false;
        },
        async browseStreamTo(path) {
            this.streamFolderBrowser.loading = true;
            try {
                const data = await this.apiFetch(`/api/settings/browse?path=${encodeURIComponent(path)}`);
                this.streamFolderBrowser.currentPath = data.path;
                this.streamFolderBrowser.parentPath = data.parent;
                this.streamFolderBrowser.dirs = data.dirs || [];
            } catch (e) {
                if (path !== '/') { await this.browseStreamTo('/'); return; }
                this.streamFolderBrowser.dirs = [];
            } finally {
                this.streamFolderBrowser.loading = false;
            }
        },
        selectStreamBrowsedFolder() {
            this.streamForm.server_path = this.streamFolderBrowser.currentPath;
            this.closeStreamFolderBrowser();
        },
        setStreamSourceMode(mode) {
            if (mode !== 'upload' && mode !== 'server_path') return;
            this.streamForm.source_mode = mode;
            if (mode === 'upload') {
                this.streamForm.server_path = '';
                this.streamForm.monitor_folder = false;
            } else {
                this.clearStreamFile();
            }
        },
        handleFileSelect(e) {
            const file = e.target.files[0];
            if (file) this.uploadForm.file_path = file.name;
            e.target.value = '';
        },
        handleFolderSelect(e) {
            const files = e.target.files;
            if (files && files.length > 0) {
                const pathParts = files[0].webkitRelativePath.split('/');
                this.uploadForm.file_path = pathParts[0] || files[0].name;
            }
            e.target.value = '';
        },
        handleStreamNzbSelect(e) {
            const file = e.target.files && e.target.files[0];
            if (file) {
                this.streamForm.source_mode = 'upload';
                this.streamForm.selectedFile = file;
                this.streamForm.selectedFileName = file.name;
                if (!this.streamForm.release_name) {
                    this.streamForm.release_name = file.name.replace(/\.nzb$/i, '');
                }
            }
            e.target.value = '';
        },
        clearFilePath() { this.uploadForm.file_path = ''; },
        clearStreamFile() {
            this.streamForm.selectedFile = null;
            this.streamForm.selectedFileName = '';
        },

        async loadStreamMonitors() {
            try {
                const data = await this.apiFetch('/api/uploads/stream-monitors');
                this.streamMonitors = Array.isArray(data.monitors) ? data.monitors : [];
            } catch (e) {
                console.error('Failed to load stream monitors:', e);
            }
        },

        async removeStreamMonitor(monitorId) {
            try {
                await this.apiFetch(`/api/uploads/stream-monitors/${monitorId}`, { method: 'DELETE' });
                await this.loadStreamMonitors();
                this.showToast('success', 'Monitor Removed', 'Stream folder monitor removed');
            } catch (e) {
                this.showToast('error', 'Error', e.message || 'Failed to remove stream monitor');
            }
        },

        async handleUsenetStreamSubmit() {
            if (this.isStreamUploadMode && !this.streamForm.selectedFile) {
                this.showToast('warning', 'Select NZB', 'Choose an NZB file to stream first');
                return;
            }
            if (this.isStreamPathMode && !this.streamForm.server_path.trim()) {
                this.showToast('warning', 'Enter Path', 'Provide an NZB file or folder path on the server');
                return;
            }

            this.streamLoading = true;
            const form = new FormData();
            if (this.isStreamUploadMode) {
                form.append('file', this.streamForm.selectedFile);
            } else {
                form.append('source_path', this.streamForm.server_path.trim());
                form.append('monitor_folder', String(Boolean(this.streamForm.monitor_folder)));
            }
            if (this.streamForm.category) {
                form.append('category', this.streamForm.category);
            }
            if (this.streamForm.release_name && this.streamForm.release_name.trim()) {
                form.append('release_name', this.streamForm.release_name.trim());
            }
            if (this.streamForm.indexer_id && this.streamUsesIndexerSubmission) {
                form.append('indexer_id', this.streamForm.indexer_id);
            }
            if (this.streamForm.posting_server_name) {
                form.append('posting_server_name', this.streamForm.posting_server_name);
            }
            form.append('submit_mode', this.streamForm.submit_mode);
            form.append('test_mode', String(Boolean(this.streamForm.test_mode)));
            form.append('enable_duplicate_check', String(!this.streamForm.skip_duplicate_check));

            try {
                const result = await this.apiFetch('/api/uploads/stream-nzb', {
                    method: 'POST',
                    headers: {},
                    body: form,
                });
                this.expandedSections['usenet-stream'] = false;
                this.expandedSections['console'] = true;
                if (result.mode === 'monitor') {
                    this.showToast('success', 'Monitor Saved', result.message || 'Stream monitor enabled');
                    await this.loadStreamMonitors();
                } else if (Array.isArray(result.job_ids) && result.job_ids.length > 1) {
                    this.showToast('success', 'Stream Jobs Started', result.message || `Queued ${result.job_ids.length} stream jobs`);
                } else {
                    this.showToast('success', 'Stream Job Started', result.message || `Job ${result.job_id} queued`);
                }
                if (this.isStreamUploadMode) {
                    this.clearStreamFile();
                }
                await this.loadJobs();
            } catch (e) {
                this.showToast('error', 'Error', e.message || 'Failed to start stream job');
            } finally {
                this.streamLoading = false;
            }
        },

        getSearchUrl(indexer) {
            if (!indexer.search_url) return '#';
            return indexer.search_url.replace('{poster}', encodeURIComponent(this.poster_name));
        },

        async toggleIndexer(indexer) {
            const newState = !indexer.enabled;
            indexer.enabled = newState;              // optimistic UI update
            try {
                await this.apiPut('/api/settings/destinations', {
                    [`enable_${indexer.id}`]: newState,
                });
            } catch (e) {
                indexer.enabled = !newState;          // revert on failure
                console.error('Failed to toggle indexer:', e);
            }
        },

        // --- DASHBOARD DATA ---
        async loadDashboard() {
            if (this.isPollingDashboard) return;
            this.isPollingDashboard = true;
            try {
                const data = await this.apiFetch('/api/dashboard/summary');
                if (data.uploads) {
                    const u = data.uploads;
                    const cat = u.by_category || {};
                    this.stats.total = u.total || 0;
                    this.stats.today = u.today || 0;
                    this.stats.episodes = cat.episodes || 0;
                    this.stats.seasonPacks = cat.season_packs || 0;
                }
                if (data.pending) {
                    const p = data.pending;
                    this.stats.tv.pending = p.tv || 0;
                    this.stats.tv.complete = p.tv_complete || 0;
                    this.stats.tv.total = (p.tv || 0) + (p.tv_complete || 0);
                    this.stats.tv.episodes = { uploaded: p.tv_episodes_complete || 0, total: (p.tv_episodes_pending || 0) + (p.tv_episodes_complete || 0) };
                    this.stats.movies.pending = p.movies || 0;
                    this.stats.movies.complete = p.movies_complete || 0;
                    this.stats.movies.total = (p.movies || 0) + (p.movies_complete || 0);
                    this.stats.totalInventory = {
                        pending: p.total_tasks ?? ((p.tv || 0) + (p.movies || 0)),
                        complete: (p.tv_complete || 0) + (p.movies_complete || 0)
                    };
                }
                if (data.performance) {
                    this.stats.speed = data.performance.avg_speed_bps || 0;
                    this.stats.gbPerHour = data.performance.gb_per_hour || 0;
                }
                if (data.breakdown) {
                    this.stats.breakdown = data.breakdown;
                }
                if (data.poster_name) {
                    this.poster_name = data.poster_name;
                }

                // Preserve favicon error states to avoid flickering on failed loads
                const prevErrors = new Map(this.loadedIndexers.filter(i => i.faviconError).map(i => [i.id, true]));
                this.loadedIndexers = (data.indexers || []).map(idx => ({
                    ...idx,
                    faviconError: prevErrors.has(idx.id)
                }));

                this.refreshIcons();
            } catch (e) {
                console.error('Failed to load dashboard:', e);
            } finally {
                this.isPollingDashboard = false;
            }
        },

        async loadUiSettings() {
            try {
                const settings = await this.apiFetch('/api/settings');
                if (settings) {
                    this.poster_name = (settings.upload && settings.upload.poster_name) || 'Anonymous';
                    const ui = settings.ui || settings;
                    const refreshSeconds = Number(ui.ui_refresh_seconds) > 0 ? Number(ui.ui_refresh_seconds) : 2;
                    this.uiSettings = {
                        ...this.uiSettings,
                        dashboard_stats_enabled: ui.dashboard_stats_enabled ?? true,
                        stats_page_enabled: ui.stats_page_enabled ?? true,
                        ui_refresh_seconds: refreshSeconds,
                        dashboard_stats_modules: (ui.dashboard_stats_modules || []).slice(0, 6)
                    };
                    this.uiRefreshRate = refreshSeconds * 1000;
                    this.dashboardCardOrder = normalizeOrderedIds(this.dashboardCardOrder, DASHBOARD_CARD_IDS);
                    this.dashboardCardEnabled = normalizeEnabledMap(this.dashboardCardEnabled);
                    this.dashboardServerStatsOrder = normalizeOrderedIds(this.dashboardServerStatsOrder, SERVER_STATS_MODULES.map(module => module.id));
                    this.syncEnabledServerStatsModules();
                    // Hydrate processing filters
                    if (settings.processing) {
                        this.processingFilters = {
                            process_tv_episodes: settings.processing.process_tv_episodes !== undefined ? settings.processing.process_tv_episodes : true,
                        };
                    }
                    // Store dynamic categories from backend
                    if (Array.isArray(settings.categories) && settings.categories.length > 0) {
                        this.backendCategories = settings.categories;
                    }
                    this.configuredFolderPaths = Array.isArray(settings.folders?.folder_paths)
                        ? settings.folders.folder_paths.filter(folder => folder && folder.path)
                        : [];
                    this.normalizeUploadFormState();
                    this.nntpServers = Array.isArray(settings.nntp_servers) ? settings.nntp_servers : [];
                    const validStreamCategories = this.streamCategories.map(c => c.value);
                    if (this.streamForm.category && !validStreamCategories.includes(this.streamForm.category)) {
                        this.streamForm.category = '';
                    }
                    const serverNames = this.streamPostingServers.map(server => server.name);
                    if (!serverNames.includes(this.streamForm.posting_server_name)) {
                        this.streamForm.posting_server_name = serverNames[0] || null;
                    }
                    const validCategoryValues = this.uploadCategoryOptions.map(category => category.value);
                    const nextCategories = this.getNormalizedUploadCategories().filter(value => value === 'all' || validCategoryValues.includes(value));
                    this.uploadForm.categories = nextCategories.includes('all') || nextCategories.length === 0 ? ['all'] : nextCategories;
                    const validIndexerIds = this.uploadIndexerOptions.map(indexer => indexer.id);
                    this.uploadForm.indexer_ids = normalizeStringArray(this.uploadForm.indexer_ids).filter(id => validIndexerIds.includes(id));
                    const validFolderPaths = this.uploadFolderPathOptions.map(folder => folder.value);
                    this.uploadForm.folder_paths = normalizeStringArray(this.uploadForm.folder_paths).filter(path => validFolderPaths.includes(path));
                }
            } catch (e) {
                console.error('Failed to load UI settings:', e);
            }
        },

        async loadJobs() {
            if (this.isPollingJobs) return;
            this.isPollingJobs = true;
            try {
                const jobs = await this.apiFetch('/api/uploads/jobs');
                const seenIds = new Set();
                this.jobs = jobs.filter(j => !seenIds.has(j.job_id) && seenIds.add(j.job_id))
                    .sort((a, b) => (a.status === 'running' ? -1 : 1) || new Date(b.started_at) - new Date(a.started_at));
                this.refreshIcons();
            } catch (e) {
                console.error('Failed to load jobs:', e);
            } finally {
                this.isPollingJobs = false;
            }
        },

        async stopJob(jobId) {
            try {
                const res = await this.apiFetch(`/api/uploads/jobs/${jobId}/stop`, { method: 'POST' });
                this.showToast('info', 'Stopping', res.message);
                await this.loadJobs();
            } catch (e) { this.showToast('error', 'Error', 'Failed to stop job'); }
        },

        async deleteJob(jobId) {
            const idx = this.jobs.findIndex(j => j.job_id === jobId);
            if (idx === -1) return;
            const removed = this.jobs.splice(idx, 1)[0];

            try {
                await this.apiFetch(`/api/uploads/jobs/${jobId}`, { method: 'DELETE' });
            } catch (e) {
                this.jobs.splice(idx, 0, removed); // Rollback
                this.showToast('error', 'Error', 'Failed to dismiss job');
            }
        },

        async clearCompletedJobs() {
            const originalJobs = [...this.jobs];
            const toClear = this.jobs.filter(j => j.status === 'completed' || j.status === 'failed');
            if (toClear.length === 0) return;

            this.jobs = this.jobs.filter(j => j.status !== 'completed' && j.status !== 'failed');

            try {
                await this.apiFetch('/api/uploads/jobs/completed', { method: 'DELETE' });
            } catch (e) {
                this.jobs = originalJobs; // Rollback
                this.showToast('error', 'Error', 'Failed to clear completed jobs');
            }
        },

        async loadNextQueueItem() {
            try {
                const data = await this.apiFetch('/api/uploads/queue/items');
                const items = data.items || [];
                this.queueItemCount = items.length;
                this.nextQueueItem = items.length > 0 ? items[0] : null;
            } catch (e) { /* non-critical */ }
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

        // --- CONSOLE LOGS ---
        async pollConsoleLogs() {
            if (this.isPollingConsole) return;
            this.isPollingConsole = true;
            try {
                // On first load jump straight to the tail so we don't replay
                // the entire buffer and cause the "scroll-scroll-scroll" effect.
                const url = this.consoleSeq === 0
                    ? '/api/console/logs?tail=100'
                    : `/api/console/logs?count=100&after=${this.consoleSeq}`;
                const data = await this.apiFetch(url);
                if (data.logs && data.logs.length > 0) {
                    data.logs.forEach(log => {
                        this.processLogLine(log);
                    });
                    if (this.consoleLogs.length > CONSOLE_MAX_LINES) {
                        this.consoleLogs.splice(0, this.consoleLogs.length - CONSOLE_MAX_LINES);
                    }
                    if (data.seq) this.consoleSeq = data.seq;
                    if (
                        this.consoleConfig.autoScroll
                        && this.expandedSections.console
                        && this.shouldRenderCard('console')
                    ) {
                        this.scrollConsoleToBottom();
                    }
                }
            } catch (e) { console.error('Console poll error:', e); }
            finally { this.isPollingConsole = false; }
        },

        processLogLine(log) {
            // Suppress noisy periodic anime-cache re-scan notifications
            if (/Anime cache:.*confirmed as anime.*re-scanning pending list/i.test(log.msg)) return;
            const isProgress = log.level === 'PROGRESS';
            const tsParts = this.parseLogTimestamp(log.ts);
            const logEntry = {
                ts: log.ts,
                ts_date: tsParts.date,
                ts_time: tsParts.time,
                level: log.level,
                msg: log.msg,
                id: Date.now() + Math.random(),
                segments: this.colorizeLogSegments(log.msg)
            };

            if (/Uploaded to (\w+)/i.test(log.msg) || /Failed to upload to (\w+)/i.test(log.msg) || /Preparation complete:/i.test(log.msg)) {
                this.stopDotsForMessage(log.msg);
            }

            if (isProgress && this.consoleLogs.length > 0 && this.consoleLogs[this.consoleLogs.length - 1].level === 'PROGRESS') {
                this.consoleLogs[this.consoleLogs.length - 1] = logEntry;
            } else {
                this.consoleLogs.push(logEntry);
            }
        },

        colorizeLogSegments(msg) {
            const DOTS = '[[DOTS]]';
            let text = String(msg || '');

            // Insert a placeholder that we later replace with a <span class="dots-anim"></span> segment.
            text = text.replace(/Uploading to (\w+)\.\.\./i, (_m, dest) => `Uploading to ${dest}${DOTS}`);
            text = text.replace(/Preparing:/i, (m) => `${m}${DOTS}`);

            let segments = this._parseLoguruSegments(text);
            segments = this._splitDotsSegments(segments, DOTS);
            segments = this._highlightCounterSegments(segments, /\bSuccess:\s*(\d+)\b/gi, 'text-notion-success');
            segments = this._highlightCounterSegments(segments, /\bFailed:\s*(\d+)\b/gi, 'text-notion-error');
            return segments;
        },

        _parseLoguruSegments(text) {
            const out = [];
            const re = /<fg\s+(#[0-9a-fA-F]{3,6})>(.*?)<\/fg>|<(red|green|yellow|blue|magenta|cyan|white|gray)>(.*?)<\/\3>/gi;
            let last = 0;
            let m;
            while ((m = re.exec(text)) !== null) {
                if (m.index > last) {
                    out.push({ text: text.slice(last, m.index) });
                }

                if (m[1]) {
                    out.push({ text: m[2] || '', style: { color: m[1] } });
                } else {
                    const c = String(m[3] || '').toLowerCase();
                    out.push({ text: m[4] || '', cls: `text-idx-${c}` });
                }
                last = re.lastIndex;
            }

            if (last < text.length) out.push({ text: text.slice(last) });
            return out.filter(s => s.text !== undefined && s.text !== null && String(s.text).length > 0);
        },

        _splitDotsSegments(segments, token) {
            const out = [];
            segments.forEach(seg => {
                const raw = String(seg.text || '');
                const parts = raw.split(token);
                parts.forEach((part, idx) => {
                    if (part) out.push({ text: part, cls: seg.cls, style: seg.style });
                    if (idx < parts.length - 1) {
                        const base = seg.cls ? seg.cls + ' ' : '';
                        out.push({ text: '', cls: (base + 'dots-anim').trim(), style: seg.style });
                    }
                });
            });
            return out;
        },

        _highlightCounterSegments(segments, regex, cls) {
            const out = [];
            segments.forEach(seg => {
                const text = String(seg.text || '');
                let last = 0;
                let m;
                regex.lastIndex = 0;
                while ((m = regex.exec(text)) !== null) {
                    if (m.index > last) out.push({ text: text.slice(last, m.index), cls: seg.cls, style: seg.style });
                    const n = parseInt(m[1], 10);
                    const shouldHighlight = Number.isFinite(n) && n > 0;
                    const mergedCls = shouldHighlight ? [seg.cls, cls].filter(Boolean).join(' ') : seg.cls;
                    out.push({ text: m[0], cls: mergedCls, style: seg.style });
                    last = regex.lastIndex;
                }
                if (last < text.length) out.push({ text: text.slice(last), cls: seg.cls, style: seg.style });
            });
            return out;
        },

        startDotsAnimation() { },
        stopDotsForMessage(msg) { },

        scrollConsoleToBottom() {
            this.$nextTick(() => {
                const scroller = this.$refs.consoleScroller;
                if (scroller) {
                    scroller.scrollTo({
                        top: scroller.scrollHeight,
                        behavior: 'auto'
                    });
                }
            });
        },

        clearConsoleLogs() { this.consoleLogs = []; },
        copyConsoleLogs() {
            const text = this.consoleLogs.map(l => `[${l.ts}] ${l.msg}`).join('\n');
            this.copyToClipboard(text, 'Logs copied');
        },

        initResize(e) {
            const startY = e.clientY;
            const startHeight = this.consoleConfig.height;
            const onMouseMove = (moveEvent) => {
                this.consoleConfig.height = Math.max(100, Math.min(800, startHeight + (moveEvent.clientY - startY)));
            };
            const onMouseUp = () => {
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
            };
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
        },

        getBreakdownTooltip(category) {
            const b = this.stats.breakdown[category];
            if (!b || Object.keys(b).length === 0) return 'No data';
            return Object.entries(b).map(([id, s]) => {
                const total = (s.complete || 0) + (s.pending || 0);
                return `${id.toUpperCase()}: ${s.complete}/${total}`;
            }).join('\n');
        },
        getBreakdownList(category) {
            if (!this.stats || !this.stats.breakdown) return [];
            const b = this.stats.breakdown[category];
            if (!b || typeof b !== 'object' || Object.keys(b).length === 0) return [];

            return Object.entries(b).map(([id, s]) => {
                if (!id) return null;
                const stats = s || {};
                const complete = stats.complete || 0;
                const pending = stats.pending || 0;
                const total = complete + pending;

                const indexer = (this.loadedIndexers || []).find(idx =>
                    idx && idx.id && idx.id.toLowerCase() === id.toLowerCase()
                );

                return {
                    id: id.toUpperCase(),
                    complete: complete,
                    total: total,
                    percent: total > 0 ? Math.round((complete / total) * 100) : 0,
                    favicon: indexer ? indexer.favicon_url : null,
                    icon: indexer ? indexer.icon : 'database',
                    color: indexer ? indexer.color : 'gray'
                };
            }).filter(Boolean);
        },
        dashboardServerStatsActive() {
            return !!(this.uiSettings.dashboard_stats_enabled && (this.uiSettings.dashboard_stats_modules || []).length);
        }
    },
    mounted() {
        document.addEventListener('click', this.handleDocumentClick);
        this.loadUiSettings().then(() => {
            this.loadDashboard();
            this.loadJobs();
            this.loadNextQueueItem();
            if (this.dashboardServerStatsActive()) {
                this.loadStatsHistory();
                this.refreshServerStats();
            }
            this.loadStreamMonitors();

            this.startInterval(this.loadJobs, Math.max(2000, this.uiRefreshRate));
            this.startInterval(() => this.loadNextQueueItem(), 5000);
            this.startInterval(this.loadDashboard, this.uiRefreshRate * 2.5);
            if (this.dashboardServerStatsActive()) {
                this.startInterval(this.refreshServerStats, this.uiRefreshRate);
            }
            this.startInterval(this.pollConsoleLogs, this.uiRefreshRate);
            this.startInterval(this.loadStreamMonitors, 10000);
        });
    },
    beforeUnmount() {
        document.removeEventListener('click', this.handleDocumentClick);
    }
});
