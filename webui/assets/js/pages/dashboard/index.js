import { badgeClass, createVuePage } from 'page-base';

import { DASHBOARD_CARD_DEFAULTS, DASHBOARD_CARD_IDS, cardMethods, normalizeEnabledMap, normalizeOrderedIds } from './cards.js';
import { SERVER_STATS_MODULES, serverStatsComputed, serverStatsMethods } from './server-stats.js';
import { streamFormComputed, streamFormMethods } from './stream-form.js';
import { normalizeStringArray, uploadFormMethods } from './upload-form.js';

const CONSOLE_MAX_LINES = 300;

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
        ...streamFormComputed,
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
        ...serverStatsComputed,
    },
    methods: {
        ...uploadFormMethods,
        ...cardMethods,
        ...serverStatsMethods,
        ...streamFormMethods,
        dismissUpdateBanner() {
            const latest = String((this.updateStatus && this.updateStatus.latest_version) || '').trim();
            if (!latest) return;
            this.dismissedUpdateVersion = latest;
            localStorage.setItem('nzbpostarr_update_banner_dismissed', latest);
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
            return 'badge flex items-center gap-1.5 bg-notion-accent/10 font-medium text-notion-accent whitespace-nowrap';
        },
        jobStatusBadgeClass() {
            // 'gray' in colorClassMap is exactly this neutral tint; going through
            // the shared helper keeps the badge shape in one place.
            return badgeClass('gray', 'font-medium uppercase whitespace-nowrap');
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
                this._applyDashboardSummary(data);
            } catch (e) {
                console.error('Failed to load dashboard:', e);
            } finally {
                this.isPollingDashboard = false;
            }
        },

        // Extracted from loadDashboard: applies one summary payload to
        // `this.stats`/`this.poster_name`/`this.loadedIndexers`. Same
        // behavior, split out to keep loadDashboard's own branching down.
        _applyDashboardSummary(data) {
            if (data.uploads) {
                const u = data.uploads;
                const cat = u.by_category || {};
                this.stats.total = u.total || 0;
                this.stats.today = u.today || 0;
                this.stats.episodes = cat.episodes || 0;
                this.stats.seasonPacks = cat.season_packs || 0;
            }
            if (data.pending) {
                this._applyPendingStats(data.pending);
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
        },

        // Extracted from _applyDashboardSummary: applies the `pending` slice
        // of the dashboard summary payload to `this.stats`. Same behavior,
        // split out to keep _applyDashboardSummary's own branching down.
        _applyPendingStats(p) {
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
        },

        async loadUiSettings() {
            try {
                const settings = await this.apiFetch('/api/settings');
                if (settings) {
                    this.poster_name = (settings.upload && settings.upload.poster_name) || 'Anonymous';
                    this._applyUiSettingsPayload(settings);
                }
            } catch (e) {
                console.error('Failed to load UI settings:', e);
            }
        },

        // Extracted from loadUiSettings: applies the rest of the /api/settings
        // payload once `settings` is known truthy. Same behavior, split out
        // to keep loadUiSettings's own branching down.
        _applyUiSettingsPayload(settings) {
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
        },

        async loadJobs() {
            if (this.isPollingJobs) return;
            this.isPollingJobs = true;
            try {
                const jobs = await this.apiFetch('/api/uploads/jobs');
                const seenIds = new Set();
                this.jobs = jobs.filter(j => !seenIds.has(j.job_id) && seenIds.add(j.job_id))
                    .sort((a, b) => (a.status === 'running' ? -1 : 1) || new Date(b.started_at) - new Date(a.started_at));
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

        async loadNextQueueItem() {
            try {
                const data = await this.apiFetch('/api/uploads/queue/items');
                const items = data.items || [];
                this.queueItemCount = items.length;
                this.nextQueueItem = items.length > 0 ? items[0] : null;
            } catch (e) { /* non-critical */ }
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
