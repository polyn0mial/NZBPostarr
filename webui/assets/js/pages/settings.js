import { createVuePage, colorClassMap, categoryIcon as _categoryIcon } from 'page-base';
import debounce from 'lodash.debounce';

// ============================================================
//  SETTINGS PAGE - Full Vue Reactive Implementation
// ============================================================

// loadSettings() below hands each section of the /api/settings response to one of these -
// `self` is the Vue page instance. Splitting by section (rather than leaving one long sequence
// of defaulting assignments) is what keeps each piece's branch count - one per `||`/`??`/ternary
// default - clear of the complexity gate; the sections themselves are independent of each other.
function applySettingsDestinations(self, data) {
    self.settings.destinations.enable_backfill = data.destinations.enable_backfill || false;
    self.settings.destinations.enable_duplicate_bypass = data.destinations.enable_duplicate_bypass || false;
    self.indexers.forEach(indexer => {
        self.settings.destinations[`enable_${indexer.id}`] = data.destinations[`enable_${indexer.id}`] || false;
        self.settings.destinations[`backfill_${indexer.id}`] = data.destinations[`backfill_${indexer.id}`] || false;
        self.settings.destinations[`priority_${indexer.id}`] = data.destinations[`priority_${indexer.id}`] || (indexer.priority || false);
    });
}

function applySettingsProcessing(self, data) {
    self.settings.processing = {
        verbose: data.processing.verbose || false,
        process_tv_episodes: data.processing.process_tv_episodes !== undefined ? data.processing.process_tv_episodes : true,
        enable_duplicate_checking: data.processing.enable_duplicate_checking !== undefined ? data.processing.enable_duplicate_checking : true,
        enable_anime_checking: data.processing.enable_anime_checking !== undefined ? data.processing.enable_anime_checking : false,
        item_limit_per_category: data.processing.item_limit_per_category || null,
        folder_size_limit_gb: data.processing.folder_size_limit_gb ?? 99,
        folder_size_limit_enabled: data.processing.folder_size_limit_enabled !== undefined ? data.processing.folder_size_limit_enabled : true,
        file_size_limit_gb: data.processing.file_size_limit_gb ?? 0,
        file_size_limit_enabled: data.processing.file_size_limit_enabled !== undefined ? data.processing.file_size_limit_enabled : true,
        dynamic_packs: data.processing.dynamic_packs !== undefined ? data.processing.dynamic_packs : true,
        tv_pack_ignore: {
            enabled: data.processing.tv_pack_ignore?.enabled !== undefined ? data.processing.tv_pack_ignore.enabled : true,
            ignore_non_video: data.processing.tv_pack_ignore?.ignore_non_video !== undefined ? data.processing.tv_pack_ignore.ignore_non_video : true,
            ignore_extras: data.processing.tv_pack_ignore?.ignore_extras !== undefined ? data.processing.tv_pack_ignore.ignore_extras : true,
            require_sxxexx: data.processing.tv_pack_ignore?.require_sxxexx !== undefined ? data.processing.tv_pack_ignore.require_sxxexx : true,
            require_resolution: data.processing.tv_pack_ignore?.require_resolution !== undefined ? data.processing.tv_pack_ignore.require_resolution : true,
            require_source: data.processing.tv_pack_ignore?.require_source !== undefined ? data.processing.tv_pack_ignore.require_source : true,
        },
    };
}

function applySettingsUiAndSkipFiles(self, data) {
    self.settings.ui = {
        dashboard_stats_enabled: data.ui.dashboard_stats_enabled !== undefined ? data.ui.dashboard_stats_enabled : true,
        stats_page_enabled: data.ui.stats_page_enabled !== undefined ? data.ui.stats_page_enabled : true,
        ui_refresh_seconds: data.ui.ui_refresh_seconds || 2,
        dashboard_stats_modules: (data.ui.dashboard_stats_modules || ["cpu", "memory", "disk", "free_space", "upload", "download"]).slice(0, 6),
    };

    const sf = data.skip_files || {};
    self.settings.skip_files = {
        enabled: sf.enabled || false,
        display_mode: sf.display_mode || 'disabled',
        patterns: (sf.patterns || []).map(p => ({
            pattern: p.pattern || '',
            categories: Array.isArray(p.categories) ? [...p.categories] : [],
            is_regex: p.is_regex || false,
        })),
    };
}

function applySettingsAuthUploadFolders(self, data) {
    const auth = data.auth || {};
    self.settings.auth = {
        enable_password: auth.enable_password !== undefined ? auth.enable_password : false,
        web_username: auth.web_username || 'admin',
        new_password: '',
    };

    self.settings.upload = {
        poster_name: data.upload.poster_name || '',
        poster_email: data.upload.poster_email || '',
        rar_size: data.upload.rar_size || '100m',
        article_size: data.upload.article_size || '1M',
        include_readme: data.upload.include_readme !== undefined ? data.upload.include_readme : true,
        upload_max_retries: data.upload.upload_max_retries || 3,
        upload_retry_delay_seconds: data.upload.upload_retry_delay_seconds || 5,
    };

    const f = data.folders || {};
    self.settings.folders = {
        base: f.base_folder || '',
        folder_paths: Array.isArray(f.folder_paths) && f.folder_paths.length > 0
            ? f.folder_paths.map(fp => self.normalizeFolderPathEntry(fp))
            : [],
    };
}

function applySettingsCategoriesAndServers(self, data) {
    // Populate available categories from indexer plugins
    if (Array.isArray(data.categories) && data.categories.length > 0) {
        self.availableCategories = data.categories;
        // Also update skip file categories to match
        self.skipFileCategories = data.categories.map(c => ({
            value: c.id,
            label: c.label,
        }));
    }

    self.settings.api_keys = data.api_keys || {};
    self.settings.usernames = data.usernames || {};

    self.servers = data.nntp_servers || [];
}

const vm = createVuePage({
    data() {
        return {
            // Section expansion state
            expandedSections: {
                global: true,
                auth: false,
                'tv-pack-ignore': false,
                'skip-files': false,
                updates: false,
                destinations: false,
                credentials: false,
                upload: false,
                nntp: false,
                folders: false,
                raw: false,
                'service-controls': false,
            },

            // Service controls state
            serviceControls: {
                stopping: false,
                restarting: false,
                lastAction: null,
            },

            // Loaded indexers from backend
            indexers: [],

            // Settings data model (bound via v-model in template)
            settings: {
                processing: {
                    verbose: false,
                    process_tv_episodes: false,
                    enable_duplicate_checking: true,
                    enable_anime_checking: false,
                    item_limit_per_category: null,
                    folder_size_limit_gb: 99,
                    folder_size_limit_enabled: true,
                    file_size_limit_gb: 0,
                    file_size_limit_enabled: true,
                    dynamic_packs: true,
                    tv_pack_ignore: {
                        enabled: true,
                        ignore_non_video: true,
                        ignore_extras: true,
                        require_sxxexx: true,
                        require_resolution: true,
                        require_source: true,
                    },
                },
                ui: {
                    dashboard_stats_enabled: true,
                    stats_page_enabled: true,
                    ui_refresh_seconds: 2,
                    dashboard_stats_modules: ["cpu", "memory", "disk", "free_space", "upload", "download"],
                },
                skip_files: {
                    enabled: false,
                    display_mode: 'disabled',
                    patterns: [],
                },
                auth: {
                    enable_password: false,
                    web_username: 'admin',
                    new_password: '',
                },
                destinations: {
                    enable_backfill: false,
                    enable_duplicate_bypass: false,
                    // Dynamic indexer enables/backfills populated on load
                },
                upload: {
                    poster_name: '',
                    poster_email: '',
                    rar_size: '100m',
                    article_size: '1M',
                    include_readme: true,
                    upload_max_retries: 3,
                    upload_retry_delay_seconds: 5,
                },
                folders: {
                    base: '',
                    folder_paths: [],
                },
                api_keys: {},
                usernames: {},
            },

            // Skip Files categories (populated dynamically from API)
            skipFileCategories: [
                { value: 'movies', label: 'Movies' },
                { value: 'tv', label: 'TV Shows' },
                { value: 'misc', label: 'Misc' },
            ],

            // Available categories from indexer plugins (populated on load)
            availableCategories: [],

            tvPackIgnoreRules: [
                {
                    key: 'ignore_non_video',
                    label: 'Non-video files',
                    icon: 'file-x',
                    description: 'Marks sidecar files as ignored inside TV/anime season packs.',
                    examples: '.nfo, .txt, .srt, .ass, .ssa, .sub, .idx, .sup, .jpg, .png, .webp, .sfv, .md5, .par2, .url',
                },
                {
                    key: 'ignore_extras',
                    label: 'Extras and samples',
                    icon: 'scissors',
                    description: 'Marks sample and bonus-style media as ignored.',
                    examples: 'sample, samples, proof, screens, subtitles, extras, featurettes, trailers, NCOP, NCED',
                },
                {
                    key: 'require_sxxexx',
                    label: 'Require S##E##',
                    icon: 'list-checks',
                    description: 'Only episode files with S##E## numbering are allowed into a filtered season pack.',
                    examples: 'Allowed: S01E01. Ignored: 01.Title.mkv, Episode 01.mkv, 1x01.mkv',
                },
                {
                    key: 'require_resolution',
                    label: 'Require resolution',
                    icon: 'scan',
                    description: 'Episode filenames must include a quality resolution.',
                    examples: '2160p, 1080p, 1080i, 720p, 576p, 480p',
                },
                {
                    key: 'require_source',
                    label: 'Require source',
                    icon: 'badge-check',
                    description: 'Episode filenames must include a source/network/source-like token.',
                    examples: 'WEB-DL, WEBRip, WEBHD, BluRay, BDRip, BRRip, REMUX, HDTV, PDTV, SDTV, TVRip, SATRip, DVDRip, DVD, VHS, AMZN, NF, DSNP, PCOK, HMAX, HULU, ATVP, CR',
                },
            ],

            // NNTP Servers
            servers: [],
            newServer: {
                name: '',
                host: '',
                port: null,
                user: '',
                password: '',
                max_connections: null,
                ssl: true,
                enabled: true,
            },

            // Raw YAML config
            rawYaml: '',
            rawConfigPath: '',
            savePassword: '',

            // UI State
            saveStatus: '',
            saveStatusError: false,
            searchQuery: '',
            loading: true,
            // Folder Browser State
            folderBrowser: {
                open: false,
                targetIdx: null,       // Which folder_path index to fill, or 'base'
                currentPath: '/',
                parentPath: null,
                dirs: [],
                loading: false,
            },

            // Indexer Modal State
            showKeyModal: false,
            activeIndexer: null,
            modalKey: '',
            modalUsername: '',
            showKey: false,

            // Raw YAML Preview control
            yamlPreviewTimer: null,
            isUpdatingYaml: false,

            // Updater state
            updater: {
                status: null,
                releases: [],
                backups: [],
                selectedVersion: '',
                selectedUploadFile: null,
                restartAfterAction: true,
                checking: false,
                installing: false,
                uploading: false,
                rollingBack: false,
                showManualUpdate: false,
            },

            // Track YAML editor focus as Vue state (replaces document.activeElement checks)
            isEditingRawYaml: false,

            // YAML Modal State
            showYamlModal: false,
            rawYamlOriginal: '',

            // Readme Modal State
            showReadmeModal: false,
            readmeContent: '',
            readmeOriginal: '',
            readmeFilePath: '',

            // Reactive settings search - tracks which sections have matches
        };
    },

    created() {
        this.debouncedUpdateYamlPreview = debounce(() => this.updateYamlPreview(), 1000);
    },

    computed: {
        // Split indexers into two columns
        leftColumnIndexers() {
            const midpoint = Math.ceil(this.indexers.length / 2);
            return this.indexers.slice(0, midpoint);
        },

        rightColumnIndexers() {
            const midpoint = Math.ceil(this.indexers.length / 2);
            return this.indexers.slice(midpoint);
        },

        // Save status class - Prettier-safe
        saveStatusClass() {
            return this.saveStatusError ? 'text-notion-error' : 'text-notion-success';
        },

        // Skip Files example visibility
        hasGlobPatterns() {
            // Show Globs if list is empty or there are glob patterns
            return this.settings.skip_files.patterns.length === 0 ||
                this.settings.skip_files.patterns.some(p => !p.is_regex);
        },

        hasRegexPatterns() {
            // Show Regex if any pattern is set to regex
            return this.settings.skip_files.patterns.some(p => p.is_regex);
        },

        // YAML diff state
        rawYamlDirty() {
            return this.rawYaml !== this.rawYamlOriginal;
        },

        yamlDiffLines() {
            const orig = (this.rawYamlOriginal || '').split('\n');
            const curr = (this.rawYaml || '').split('\n');
            const len = Math.max(orig.length, curr.length);
            return Array.from({ length: len }, (_, i) => orig[i] !== curr[i]);
        },

        yamlChangedLineCount() {
            return this.yamlDiffLines.filter(Boolean).length;
        },

        rawYamlLines() {
            return (this.rawYaml || '').split('\n');
        },

        readmeDirty() {
            return this.readmeContent !== this.readmeOriginal;
        },
    },

    methods: {
        // ============================================================
        //  PRETTIER-SAFE CLASS HELPERS
        // ============================================================

        // Toggle button class (for switch track)
        toggleBtnClass(key, section = 'processing') {
            const base = 'toggle-switch relative inline-flex h-3.5 w-6 items-center rounded-full transition-colors focus:outline-none';
            const isOn = this.getToggleState(key, section);
            return isOn ? `${base} bg-notion-accent` : `${base} bg-notion-bg-hover`;
        },

        // Toggle knob class (for switch circle)
        toggleKnobClass(key, section = 'processing') {
            const base = 'inline-block size-2.5 transform rounded-full bg-white transition-transform';
            const isOn = this.getToggleState(key, section);
            return isOn ? `${base} translate-x-3` : `${base} translate-x-0.5`;
        },

        // Indexer toggle button class
        indexerToggleBtnClass(indexerId) {
            const base = 'toggle-switch relative inline-flex h-3.5 w-6 items-center rounded-full transition-colors focus:outline-none';
            const isOn = this.settings.destinations[`enable_${indexerId}`];
            return isOn ? `${base} bg-notion-accent` : `${base} bg-notion-bg-hover`;
        },

        // Indexer toggle knob class
        indexerToggleKnobClass(indexerId) {
            const base = 'inline-block size-2.5 transform rounded-full bg-white transition-transform';
            const isOn = this.settings.destinations[`enable_${indexerId}`];
            return isOn ? `${base} translate-x-3` : `${base} translate-x-0.5`;
        },

        // Backfill pill class
        backfillPillClass(indexerId) {
            const base = 'flex items-center gap-1.5 px-2 py-0.5 bg-notion-bg-hover/20 rounded-full backfill-pill transition-opacity';
            const isEnabled = this.settings.destinations[`backfill_${indexerId}`];
            return this.canBackfill(indexerId)
                ? `${base} ${isEnabled ? 'opacity-100' : 'opacity-60'}`
                : `${base} opacity-40 pointer-events-none`;
        },

        // ============================================================
        //  Toggle Switch Helper (for custom toggle components)
        // ============================================================
        getToggleState(key, section = 'processing') {
            if (this.settings[section]) {
                return this.settings[section][key] || false;
            }
            return false;
        },

        setToggleState(key, value, section = 'processing') {
            if (this.settings[section]) {
                this.settings[section][key] = value;
            }

            // Update backfill availability when relevant toggles change
            if (section === 'destinations' && (key === 'enable_backfill' || key.startsWith('enable_'))) {
                this.updateBackfillStates();
            }
        },

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

        toggleSetting(key, section = 'processing') {
            const current = this.getToggleState(key, section);
            this.setToggleState(key, !current, section);
        },

        defaultTvPackIgnore() {
            return {
                enabled: true,
                ignore_non_video: true,
                ignore_extras: true,
                require_sxxexx: true,
                require_resolution: true,
                require_source: true,
            };
        },

        ensureTvPackIgnore() {
            if (!this.settings.processing.tv_pack_ignore) {
                this.settings.processing.tv_pack_ignore = this.defaultTvPackIgnore();
            }
            return this.settings.processing.tv_pack_ignore;
        },

        tvPackIgnoreOn(key = 'enabled') {
            const rules = this.ensureTvPackIgnore();
            return !!rules[key];
        },

        toggleTvPackIgnore(key = 'enabled') {
            const rules = this.ensureTvPackIgnore();
            rules[key] = !rules[key];
        },

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
        //  Settings Search/Filter
        // ============================================================
        _normQuery() {
            return (this.searchQuery || '').trim().toLowerCase();
        },

        matchesSearch(corpus) {
            const q = this._normQuery();
            if (!q) return true;
            return String(corpus || '').toLowerCase().includes(q);
        },

        _globalSectionVisible() {
            return (
                this.matchesSearch('theme light dark mode appearance') ||
                this.matchesSearch('verbose logging debug logs') ||
                this.matchesSearch('dynamic packs virtual season auto generate unsorted') ||
                this.matchesSearch('enable duplicate checking fast processing') ||
                this.matchesSearch('enable anime checking jikan myanimelist identification') ||
                this.matchesSearch('stats page system stats page disable history collector navigation') ||
                this.matchesSearch('dashboard stats server stats modules sparkline') ||
                this.matchesSearch('ui refresh rate seconds polling') ||
                this.matchesSearch('folder size limit pack skip tv season') ||
                this.matchesSearch('file size limit movie skip individual')
            );
        },

        _skipFilesSectionVisible() {
            return (
                this.matchesSearch('skip files display mode hidden disabled queue') ||
                (this.settings?.skip_files?.patterns || []).some(r =>
                    this.matchesSearch(`skip files pattern filename glob wildcard ${r.pattern || ''} ${(r.categories || []).join(' ')}`)
                )
            );
        },

        _updatesSectionVisible() {
            return (
                this.matchesSearch('updates update updater github release rollback backup zip restart') ||
                this.matchesSearch('check for updates install latest upload zip')
            );
        },

        _serviceControlsSectionVisible() {
            return (
                this.matchesSearch('stop all jobs uploads emergency halt kill abort cancel clear queue') ||
                this.matchesSearch('restart service reboot process danger zone')
            );
        },

        _destinationsSectionVisible() {
            return (
                this.matchesSearch('enable backfill global master switch') ||
                this.matchesSearch('duplicate error bypass retry indexer repeat') ||
                (this.indexers || []).some(idx => this.matchesSearch(`${idx.name || ''} ${idx.id || ''} indexer api`))
            );
        },

        _uploadSectionVisible() {
            return (
                this.matchesSearch('poster username uploader name') ||
                this.matchesSearch('poster email') ||
                this.matchesSearch('rar size split volume bits') ||
                this.matchesSearch('article size chunk segment') ||
                this.matchesSearch('max retries upload failure attempts') ||
                this.matchesSearch('retry delay wait time seconds') ||
                this.matchesSearch('include readme branding file info')
            );
        },

        sectionVisible(sectionId) {
            const q = this._normQuery();
            if (!q) return true;

            switch (sectionId) {
                case 'global':
                    return this._globalSectionVisible();
                case 'tv-pack-ignore':
                    return this.matchesSearch('tv pack ignore seasonal pack sxxexx source resolution sample nfo extras subtitles sidecar proof screens nced ncop');
                case 'skip-files':
                    return this._skipFilesSectionVisible();
                case 'updates':
                    return this._updatesSectionVisible();
                case 'service-controls':
                    return this._serviceControlsSectionVisible();
                case 'destinations':
                    return this._destinationsSectionVisible();
                case 'upload':
                    return this._uploadSectionVisible();
                default:
                    // Sections without item-level filtering (NNTP/Folders/Raw) stay hidden during searches,
                    // matching the previous behavior.
                    return false;
            }
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

                this.refreshIcons();
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

        async loadSettings() {
            try {
                this.loading = true;

                // Load indexers first
                await this.loadIndexers();

                // Load settings
                const data = await this.apiFetch('/api/settings');

                applySettingsDestinations(this, data);
                applySettingsProcessing(this, data);
                applySettingsUiAndSkipFiles(this, data);
                applySettingsAuthUploadFolders(this, data);
                applySettingsCategoriesAndServers(this, data);

                // Load raw config
                await this.loadRawSettings();
                await this.loadUpdateData();

                this.updateBackfillStates();
                this.refreshIcons();

            } catch (e) {
                console.error('Failed to load settings:', e);
                this.showStatus('Failed to load settings', true);
            } finally {
                this.loading = false;
            }
        },

        async loadRawSettings() {
            try {
                const data = await this.apiFetch('/api/settings/raw');
                this.rawYaml = data.content;
                this.rawYamlOriginal = data.content;
                this.rawConfigPath = data.path;
            } catch (e) {
                console.error('Failed to load raw settings:', e);
            }
        },

        openYamlModal() {
            this.showYamlModal = true;
            this.updateYamlPreview();
        },

        async closeYamlModal() {
            if (this.rawYamlDirty) {
                const ok = await this.confirmDialog('You have unsaved changes. Close without saving?', {
                    title: 'Discard Changes',
                    detail: 'Your edits to the raw YAML will be lost.',
                    danger: true,
                    confirmLabel: 'Discard',
                    cancelLabel: 'Keep Editing',
                });
                if (!ok) return;
            }
            this.showYamlModal = false;
            this.isEditingRawYaml = false;
        },

        resetYaml() {
            this.rawYaml = this.rawYamlOriginal;
        },

        async openReadmeModal() {
            try {
                const data = await this.apiFetch('/api/settings/readme');
                this.readmeContent = data.content;
                this.readmeOriginal = data.content;
                this.readmeFilePath = data.path;
            } catch (e) {
                this.readmeContent = '';
                this.readmeOriginal = '';
                this.readmeFilePath = 'indexers/readme/readme.txt';
            }
            this.showReadmeModal = true;
        },

        async closeReadmeModal() {
            if (this.readmeDirty) {
                const ok = await this.confirmDialog('You have unsaved changes. Close without saving?', {
                    title: 'Discard Changes',
                    detail: 'Your edits to readme.txt will be lost.',
                    danger: true,
                    confirmLabel: 'Discard',
                    cancelLabel: 'Keep Editing',
                });
                if (!ok) return;
            }
            this.showReadmeModal = false;
        },

        async saveReadme() {
            try {
                await this.apiPost('/api/settings/readme', { content: this.readmeContent });
                this.readmeOriginal = this.readmeContent;
                this.showToast('success', 'Readme Saved', 'readme.txt updated successfully.');
                this.showReadmeModal = false;
            } catch (e) {
                this.showToast('error', 'Save Failed', e.message || 'Failed to save readme.');
            }
        },

        syncYamlScroll(e) {
            const hl = this.$refs.yamlHighlight;
            if (hl) {
                hl.scrollTop = e.target.scrollTop;
                hl.scrollLeft = e.target.scrollLeft;
            }
        },

        async loadUpdateData(force = false) {
            try {
                const statusUrl = force ? '/api/system/update/status?force=true' : '/api/system/update/status';
                const [status, releasesRes, backupsRes] = await Promise.all([
                    this.apiFetch(statusUrl),
                    this.apiFetch('/api/system/update/releases?limit=20'),
                    this.apiFetch('/api/system/update/backups?limit=20'),
                ]);

                this.updater.status = status || null;
                this.updater.releases = (releasesRes && releasesRes.releases) || [];
                this.updater.backups = (backupsRes && backupsRes.backups) || [];

                if (!this.updater.selectedVersion && this.updater.releases.length > 0) {
                    this.updater.selectedVersion = this.updater.releases[0].version || '';
                }
            } catch (e) {
                console.error('Failed to load updater data:', e);
                this.showToast('error', 'Updater Error', e.message || 'Failed to load updater data');
            }
        },

        formatVersionLabel(v) {
            const raw = String(v || '').trim();
            if (!raw) return 'unknown';
            return raw.startsWith('v') ? raw : `v${raw}`;
        },

        formatBytesCompact(bytes) {
            const n = Number(bytes || 0);
            if (!Number.isFinite(n) || n <= 0) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            let idx = 0;
            let val = n;
            while (val >= 1024 && idx < units.length - 1) {
                val /= 1024;
                idx += 1;
            }
            const precision = val >= 100 ? 0 : val >= 10 ? 1 : 2;
            return `${val.toFixed(precision)} ${units[idx]}`;
        },

        async checkUpdatesNow() {
            if (this.updater.checking) return;
            this.updater.checking = true;
            try {
                await this.apiFetch('/api/system/update/check', { method: 'POST' });
                await this.loadUpdateData(true);
                this.showToast('success', 'Updater', 'Checked GitHub for new versions');
            } catch (e) {
                this.showToast('error', 'Updater', e.message || 'Update check failed');
            } finally {
                this.updater.checking = false;
            }
        },

        async installLatestFromGithub() {
            if (this.updater.installing) return;
            this.updater.installing = true;
            try {
                const res = await this.apiFetch('/api/system/update/install/github', {
                    method: 'POST',
                    body: JSON.stringify({
                        version: null,
                        restart: this.updater.restartAfterAction,
                    }),
                });
                this.showToast('success', 'Updater', `Installed ${res.installed_version_label || 'update'}${res.restart_scheduled ? '; restarting...' : ''}`);
                await this.loadUpdateData(true);
            } catch (e) {
                this.showToast('error', 'Updater', e.message || 'GitHub install failed');
            } finally {
                this.updater.installing = false;
            }
        },

        async installSelectedRelease() {
            if (this.updater.installing) return;
            if (!this.updater.selectedVersion) {
                this.showToast('warning', 'Updater', 'Select a release first');
                return;
            }

            this.updater.installing = true;
            try {
                const res = await this.apiFetch('/api/system/update/install/github', {
                    method: 'POST',
                    body: JSON.stringify({
                        version: this.updater.selectedVersion,
                        restart: this.updater.restartAfterAction,
                    }),
                });
                this.showToast('success', 'Updater', `Installed ${res.installed_version_label || this.updater.selectedVersion}${res.restart_scheduled ? '; restarting...' : ''}`);
                await this.loadUpdateData(true);
            } catch (e) {
                this.showToast('error', 'Updater', e.message || 'Release install failed');
            } finally {
                this.updater.installing = false;
            }
        },

        onUpdateZipChosen(e) {
            const file = (e.target && e.target.files && e.target.files[0]) || null;
            this.updater.selectedUploadFile = file;
        },

        async installUpdateZip() {
            if (this.updater.uploading) return;
            if (!this.updater.selectedUploadFile) {
                this.showToast('warning', 'Updater', 'Choose a ZIP file first');
                return;
            }

            this.updater.uploading = true;
            try {
                const form = new FormData();
                form.append('file', this.updater.selectedUploadFile);
                form.append('restart', String(this.updater.restartAfterAction));

                const res = await fetch('/api/system/update/install/upload', {
                    method: 'POST',
                    body: form,
                    cache: 'no-store',
                });

                const payload = await res.json().catch(() => ({}));
                if (!res.ok) {
                    throw new Error(payload.detail || `Install failed (HTTP ${res.status})`);
                }

                this.showToast('success', 'Updater', `Installed ${payload.installed_version_label || 'uploaded update'}${payload.restart_scheduled ? '; restarting...' : ''}`);
                this.updater.selectedUploadFile = null;
                await this.loadUpdateData(true);
            } catch (e) {
                this.showToast('error', 'Updater', e.message || 'ZIP install failed');
            } finally {
                this.updater.uploading = false;
            }
        },

        // ============================================================
        //  Service Controls
        // ============================================================
        async stopAllJobs() {
            if (this.serviceControls.stopping) return;
            const ok = await this.confirmDialog('Stop all running uploads and clear the queue?', {
                title: 'Stop All Uploads',
                detail: 'This will interrupt any active upload immediately.',
                danger: true,
                confirmLabel: 'Stop Everything',
            });
            if (!ok) return;

            this.serviceControls.stopping = true;
            try {
                const result = await this.apiFetch('/api/system/stop-all', {
                    method: 'POST',
                    body: JSON.stringify({
                        clear_staged_items: true,
                        wait_timeout_seconds: 20,
                    }),
                });
                this.serviceControls.lastAction = result;

                const remaining = Number(result?.stop?.active_count || 0);
                const status = result?.status === 'partial' ? 'warning' : 'success';
                const title = result?.status === 'partial' ? 'Stopping Timed Out' : 'Uploads Stopped';
                const summary = result?.status === 'partial'
                    ? `Stop requested. ${remaining} active job${remaining === 1 ? '' : 's'} still shutting down when the timeout expired.`
                    : 'All jobs stopped and queues cleared.';

                this.showToast(status, title, summary, result?.status === 'partial' ? 12000 : 6000);
            } catch (e) {
                this.showToast('error', 'Stop Failed', e.message || 'Could not stop jobs');
            } finally {
                this.serviceControls.stopping = false;
            }
        },

        async restartService() {
            if (this.serviceControls.restarting) return;
            const ok = await this.confirmDialog('Restart the NZBPostarr service?', {
                title: 'Restart Service',
                detail: 'Active uploads are stopped first. The page will reload automatically.',
                danger: true,
                confirmLabel: 'Restart',
            });
            if (!ok) return;

            this.serviceControls.restarting = true;
            try {
                const result = await this.apiFetch('/api/system/restart', {
                    method: 'POST',
                    body: JSON.stringify({
                        delay_seconds: 2.0,
                        stop_before_restart: true,
                        clear_staged_items: true,
                        wait_timeout_seconds: 20,
                    }),
                });
                this.serviceControls.lastAction = result;

                const remaining = Number(result?.stop?.active_count || 0);
                const message = remaining > 0
                    ? `Restart scheduled. ${remaining} active job${remaining === 1 ? '' : 's'} were still winding down at timeout.`
                    : 'Service is restarting. The page will refresh when it comes back.';

                this.showToast('success', 'Restarting', message, 10000);
                await this.waitForServiceReload(45000);
            } catch (e) {
                this.showToast('error', 'Restart Failed', e.message || 'Could not restart service');
                this.serviceControls.restarting = false;
            }
        },

        async waitForServiceReload(timeoutMs = 45000) {
            const started = Date.now();
            let sawOffline = false;

            while ((Date.now() - started) < timeoutMs) {
                try {
                    await this.apiFetch('/api/tests/health');
                    if (sawOffline) {
                        window.location.reload();
                        return;
                    }
                } catch (e) {
                    if (e?.isOffline || e?.status >= 500) {
                        sawOffline = true;
                    }
                }
                await new Promise(resolve => setTimeout(resolve, 1000));
            }

            window.location.reload();
        },

        async rollbackBackup(backupId) {
            if (this.updater.rollingBack) return;
            if (!backupId) return;

            this.updater.rollingBack = true;
            try {
                const res = await this.apiFetch('/api/system/update/rollback', {
                    method: 'POST',
                    body: JSON.stringify({
                        backup_id: backupId,
                        restart: this.updater.restartAfterAction,
                    }),
                });
                this.showToast('success', 'Rollback', `Restored snapshot ${backupId}${res.restart_scheduled ? '; restarting...' : ''}`);
                await this.loadUpdateData(true);
            } catch (e) {
                this.showToast('error', 'Rollback', e.message || 'Rollback failed');
            } finally {
                this.updater.rollingBack = false;
            }
        },

        async updateYamlPreview() {
            // Only update if the raw section is open OR if it was recently edited
            // This prevents excessive polling if the user isn't even looking at the YAML
            if ((!this.expandedSections.raw && !this.showYamlModal) || this.loading) return;

            // Don't update if we're currently processing a save or update
            if (this.isUpdatingYaml) return;
            this.isUpdatingYaml = true;

            try {
                // Map frontend settings back to the structure the backend expects for config.yaml
                const destinations = {
                    enable_backfill: this.settings.destinations.enable_backfill,
                    enable_duplicate_bypass: this.settings.destinations.enable_duplicate_bypass,
                };
                this.indexers.forEach(indexer => {
                    destinations[`enable_${indexer.id}`] = this.settings.destinations[`enable_${indexer.id}`];
                    destinations[`backfill_${indexer.id}`] = this.settings.destinations[`backfill_${indexer.id}`];
                    destinations[`priority_${indexer.id}`] = this.settings.destinations[`priority_${indexer.id}`];
                });

                const previewData = {
                    ...this.settings.processing,
                    ...this.settings.upload,
                    ...this.settings.ui,
                    ...destinations,
                    skip_files: this.settings.skip_files,
                    base_folder: this.settings.folders.base,
                    folder_paths: this.buildFolderPathPayload(),
                    nntp_servers: this.servers,
                    api_keys: this.settings.api_keys,
                    usernames: this.settings.usernames
                };

                const res = await this.apiPost('/api/settings/preview', previewData);

                if (res.ok) {
                    const data = await res.json();

                    // Use Vue state to check if user is editing YAML (replaces document.activeElement check)
                    // rawYamlOriginal is only updated by loadRawSettings() (disk saves), never by live preview
                    if (!this.isEditingRawYaml) {
                        this.rawYaml = data.content;
                    }
                }
            } catch (e) {
                console.warn('YAML preview update failed:', e);
            } finally {
                this.isUpdatingYaml = false;
            }
        },

        // ============================================================
        //  Data Saving
        // ============================================================
        async saveAllSettings() {
            this.showStatus('Saving...', false);

            try {
                // Build destinations object
                const destinations = {
                    enable_backfill: this.settings.destinations.enable_backfill,
                    enable_duplicate_bypass: this.settings.destinations.enable_duplicate_bypass,
                };
                this.indexers.forEach(indexer => {
                    destinations[`enable_${indexer.id}`] = this.settings.destinations[`enable_${indexer.id}`] || false;
                    destinations[`backfill_${indexer.id}`] = this.settings.destinations[`backfill_${indexer.id}`] || false;
                    destinations[`priority_${indexer.id}`] = this.settings.destinations[`priority_${indexer.id}`] || false;
                });

                const sections = [
                    { name: 'destinations', data: destinations },
                    { name: 'processing', data: this.settings.processing },
                    { name: 'ui', data: this.settings.ui },
                    { name: 'upload', data: this.settings.upload },
                    { name: 'nntp_servers', data: { nntp_servers: this.servers } },
                    {
                        name: 'folders',
                        data: {
                            base_folder: this.settings.folders.base,
                            folder_paths: this.buildFolderPathPayload(),
                        }
                    },
                    {
                        name: 'credentials',
                        data: {
                            api_keys: this.settings.api_keys,
                            usernames: this.settings.usernames,
                        }
                    },
                    {
                        name: 'skip_files',
                        data: {
                            skip_files: this.settings.skip_files,
                        }
                    },
                    {
                        name: 'auth',
                        data: {
                            enable_password: this.settings.auth.enable_password,
                            web_username: this.settings.auth.web_username,
                            ...(this.settings.auth.new_password ? { web_password: this.settings.auth.new_password } : {}),
                        }
                    },
                ];

                const results = await Promise.all(sections.map(async ({ name, data }) => {
                    try {
                        await this.apiPut(`/api/settings/${name}`, data);
                        return true;
                    } catch (e) {
                        console.error(`Failed to save ${name}:`, e);
                        return false;
                    }
                }));

                if (results.every(r => r)) {
                    this.showStatus('All settings saved!', false);
                    this.showToast('success', 'Saved', 'All configurations have been updated');
                    this.settings.auth.new_password = '';
                    // Refresh the raw YAML view to match the newly saved state
                    await this.loadRawSettings();
                } else {
                    this.showStatus('Partial save failure', true);
                    this.showToast('error', 'Error', 'Some configuration groups failed to save');
                }
            } catch (e) {
                console.error('Failed to save all settings:', e);
                this.showStatus('Failed to save', true);
            }
        },

        async saveRawSettings() {
            const ok = await this.confirmDialog('Save raw YAML configuration?', {
                title: 'Overwrite Config',
                detail: 'This overwrites the config file on disk immediately.',
                danger: true,
                confirmLabel: 'Overwrite',
            });
            if (!ok) return;

            try {
                await this.apiPost('/api/settings/raw', { content: this.rawYaml });
                this.rawYamlOriginal = this.rawYaml;
                this.showToast('success', 'Config Saved', 'Raw YAML configuration updated.');
                this.showYamlModal = false;
                await this.loadSettings();
            } catch (e) {
                const detail = e.message || 'Failed to save raw config.';
                if (e.isOffline) {
                    this.showToast('error', 'Error', 'A network error occurred.');
                } else {
                    this.showToast('error', 'Save Failed', detail);
                }
            }
        },

        // ============================================================
        //  Server Management
        // ============================================================
        addServer(event) {
            if (event) event.stopPropagation();

            if (!this.newServer.host || !this.newServer.user || !this.newServer.password) {
                this.showToast('error', 'Missing Info', 'Host, Username, and Password are required');
                return;
            }

            this.servers.push({
                name: this.newServer.name || this.newServer.host,
                host: this.newServer.host,
                port: this.newServer.port,
                user: this.newServer.user,
                password: this.newServer.password,
                max_connections: this.newServer.max_connections,
                ssl: true,
                enabled: true,
            });

            // Reset new server form
            this.newServer = {
                name: '',
                host: '',
                port: null,
                user: '',
                password: '',
                max_connections: null,
                ssl: true,
                enabled: true,
            };

            this.refreshIcons();
        },

        removeServer(index) {
            this.servers.splice(index, 1);
        },

        // ============================================================
        //  Skip Files Management
        // ============================================================
        addSkipPattern() {
            this.settings.skip_files.patterns.push({
                pattern: '',
                categories: [],
                is_regex: false,
            });
        },

        toggleSkipCategory(rule, cat) {
            const idx = rule.categories.indexOf(cat);
            if (idx > -1) {
                rule.categories.splice(idx, 1);
            } else {
                rule.categories.push(cat);
            }
        },

        // ============================================================
        //  Folder Path Management
        // ============================================================
        addFolderPath() {
            this.settings.folders.folder_paths.push(this.normalizeFolderPathEntry());
            this.refreshIcons();
        },

        // Direct toggle class helpers (for inline boolean props like fp.monitor)
        toggleBtnClassDirect(isOn) {
            const base = 'toggle-switch relative inline-flex h-3.5 w-6 items-center rounded-full transition-colors focus:outline-none';
            return isOn ? `${base} bg-notion-accent` : `${base} bg-notion-bg-hover`;
        },

        toggleKnobClassDirect(isOn) {
            const base = 'inline-block h-2.5 w-2.5 transform rounded-full bg-white transition-transform';
            return isOn ? `${base} translate-x-3` : `${base} translate-x-0.5`;
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
            } else if (idx !== null && this.settings.folders.folder_paths[idx]) {
                this.settings.folders.folder_paths[idx].path = this.folderBrowser.currentPath;
            }
            this.closeFolderBrowser();
        },

        getCategoryIcon(category) {
            // Check dynamic categories from the API first
            const found = this.availableCategories.find(c => c.id === category);
            if (found && found.icon) return found.icon;
            return _categoryIcon(category);
        },

        getCategoryIndexers(category) {
            const cat = this.availableCategories.find(c => c.id === category);
            return cat ? (cat.indexers || []) : [];
        },

        toggleServer(index) {
            this.servers[index].enabled = !this.servers[index].enabled;
        },

        updateServerProp(index, prop, value) {
            if (prop === 'pass' && value === '') return;
            this.servers[index][prop] = value;
        },

        // ============================================================
        //  UI Helpers
        // ============================================================
        showStatus(msg, isError) {
            this.saveStatus = msg;
            this.saveStatusError = isError;
            setTimeout(() => {
                if (this.saveStatus === msg) {
                    this.saveStatus = '';
                }
            }, 3000);
        },

        getIndexerColors(color) {
            return colorClassMap[color] || colorClassMap['gray'];
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
    },

    watch: {
        searchQuery() {
            const q = this._normQuery();
            if (!q) return;

            // Auto-expand visible sections while searching (matches previous UX)
            Object.keys(this.expandedSections || {}).forEach(sectionId => {
                if (this.sectionVisible(sectionId)) {
                    this.expandedSections[sectionId] = true;
                }
            });
        },
        // Live YAML updating
        settings: {
            handler() {
                this.debouncedUpdateYamlPreview();
            },
            deep: true
        },
        servers: {
            handler() {
                this.debouncedUpdateYamlPreview();
            },
            deep: true
        },
        // Update preview immediately when raw section is opened
        'expandedSections.raw'(isExpanded) {
            if (isExpanded) {
                this.updateYamlPreview();
            }
        }
    },

    mounted() {
        this.loadSettings();
        // Click-away for dropdowns now handled via v-click-outside directive in the template.
        // No manual document.addEventListener needed.
    }
});
