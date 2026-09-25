import {
    createVuePage,
    categoryIcon as _categoryIcon,
    categoryMeta,
} from 'page-base';
import debounce from 'lodash.debounce';

import { categoryColorsMethods } from './category-colors.js';
import { foldersMethods } from './folders.js';
import { backupMethods } from './backup.js';
import { tvPackIgnoreMethods } from './tv-pack-ignore.js';
import { indexersMethods } from './indexers.js';
import { serversMethods } from './servers.js';
import { updatesMethods } from './updates.js';
import { rawYamlMethods } from './raw-yaml.js';

// ============================================================
//  SETTINGS PAGE - Full Vue Reactive Implementation
// ============================================================

// loadSettings() below hands each section of the /api/settings response to one of these -
// `self` is the Vue page instance. GET /api/settings always returns every typed field read here
// (pinned by tests/webui/test_pins_settings.py), so those fields are read as-is; only the
// free-form config dicts (tv_pack_ignore rules, skip_files) keep per-key defaults.
function applySettingsDestinations(self, data) {
    self.settings.destinations.enable_backfill = data.destinations.enable_backfill;
    self.settings.destinations.enable_duplicate_bypass = data.destinations.enable_duplicate_bypass;
    self.indexers.forEach(indexer => {
        self.settings.destinations[`enable_${indexer.id}`] = data.destinations[`enable_${indexer.id}`];
        self.settings.destinations[`backfill_${indexer.id}`] = data.destinations[`backfill_${indexer.id}`];
        self.settings.destinations[`priority_${indexer.id}`] = data.destinations[`priority_${indexer.id}`];
    });
}

function applySettingsProcessing(self, data) {
    self.settings.processing = {
        verbose: data.processing.verbose,
        process_tv_episodes: data.processing.process_tv_episodes,
        enable_duplicate_checking: data.processing.enable_duplicate_checking,
        enable_anime_checking: data.processing.enable_anime_checking,
        item_limit_per_category: data.processing.item_limit_per_category,
        folder_size_limit_gb: data.processing.folder_size_limit_gb,
        folder_size_limit_enabled: data.processing.folder_size_limit_enabled,
        file_size_limit_gb: data.processing.file_size_limit_gb,
        file_size_limit_enabled: data.processing.file_size_limit_enabled,
        dynamic_packs: data.processing.dynamic_packs,
        tv_pack_ignore: {
            enabled: data.processing.tv_pack_ignore?.enabled !== undefined ? data.processing.tv_pack_ignore.enabled : true,
            ignore_non_episode: data.processing.tv_pack_ignore?.ignore_non_episode !== undefined ? data.processing.tv_pack_ignore.ignore_non_episode : true,
            require_episode: data.processing.tv_pack_ignore?.require_episode !== undefined ? data.processing.tv_pack_ignore.require_episode : true,
            require_resolution: data.processing.tv_pack_ignore?.require_resolution !== undefined ? data.processing.tv_pack_ignore.require_resolution : false,
            require_source: data.processing.tv_pack_ignore?.require_source !== undefined ? data.processing.tv_pack_ignore.require_source : true,
        },
    };
}

function applySettingsUiAndSkipFiles(self, data) {
    self.settings.ui = {
        dashboard_stats_enabled: data.ui.dashboard_stats_enabled,
        stats_page_enabled: data.ui.stats_page_enabled,
        ui_refresh_seconds: data.ui.ui_refresh_seconds,
        dashboard_stats_modules: data.ui.dashboard_stats_modules.slice(0, 6),
        category_appearance_profiles: data.ui.category_appearance_profiles,
    };
    self.loadCategoryAppearanceLibrary(self.settings.ui.category_appearance_profiles);

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
    self.settings.auth = {
        enable_password: data.auth.enable_password,
        web_username: data.auth.web_username,
        new_password: '',
    };

    self.settings.upload = {
        poster_name: data.upload.poster_name,
        poster_email: data.upload.poster_email,
        rar_size: data.upload.rar_size,
        article_size: data.upload.article_size,
        include_readme: data.upload.include_readme,
        upload_max_retries: data.upload.upload_max_retries,
        upload_retry_delay_seconds: data.upload.upload_retry_delay_seconds,
    };

    self.settings.folders = {
        base: data.folders.base_folder,
        backup_folder: data.folders.backup_folder,
        folder_paths: data.folders.folder_paths.map(fp => self.normalizeFolderPathEntry(fp)),
    };
    self.backupJob.lastSavedFolder = self.settings.folders.backup_folder;
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

    self.settings.api_keys = data.api_keys;
    self.settings.usernames = data.usernames;

    self.servers = data.nntp_servers;
}

const vm = createVuePage({
    data() {
        return {
            // Section expansion state
            expandedSections: {
                global: true,
                'category-colors': true,
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
                        ignore_non_episode: true,
                        require_episode: true,
                        require_resolution: false,
                        require_source: true,
                    },
                },
                ui: {
                    dashboard_stats_enabled: true,
                    stats_page_enabled: true,
                    ui_refresh_seconds: 2,
                    dashboard_stats_modules: ["cpu", "memory", "disk", "free_space", "upload", "download"],
                    category_appearance_profiles: {},
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
                    backup_folder: '',
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
                    key: 'ignore_non_episode',
                    label: 'Non-episode files',
                    icon: 'file-x',
                    description: 'Marks sidecar files, samples, and anime/bonus content as ignored inside TV/anime season packs. Matching is case-insensitive.',
                    examples: '.nfo, .txt, .srt, subtitles, sample, proof, screens, extras, featurettes, trailers, NCOP, NCED, OVA, OAD',
                    examplesMore: 'OP, ED, preview, PV, teaser, special, specials, samples, sidecar image files, and other non-episode bonus content',
                },
                {
                    key: 'require_episode',
                    label: 'Require episode format',
                    icon: 'list-checks',
                    description: 'Episode files must include a recognised numbering pattern. Matching is case-insensitive and supports common TV, anime, daily, and multi-episode formats.',
                    examples: 'S01E01, S01E01E02, S01E01-E02, S01.E01, 01x01, 01x01x02, 01x01-02, E01, E01-E02, EP01, Episode 01, 2024.01.15',
                    examplesMore: 'S#E#, S###E###, S##.E##, S##_E##, S## E##, SE##EP##, S##EP##, SEASON##EPISODE##, SEASON#EP#, S##EPISODE##, S##E##V#, 01x01-01x02, [01x02], (01x02), Part1, Pt1, Chapter 01, 1of2, Part1of2, Pilot, Finale, Final, Special, OVA, OAD, YYYY_MM_DD, YYYY-MM-DD, DD.MM.YYYY, MM.DD.YYYY, 101, 102, 1001',
                },
                {
                    key: 'require_resolution',
                    label: 'Require resolution',
                    icon: 'scan',
                    description: 'Episode filenames must include a quality resolution.',
                    examples: 'Any resolution ending in p or i (480p, 576i, 720p, 1080p, 1080i, 2160p, 4320p, etc.), NTSC, PAL',
                },
                {
                    key: 'require_source',
                    label: 'Require source',
                    icon: 'badge-check',
                    description: 'Episode filenames must include a media source token. Matching is case-insensitive and avoids generic title words that caused false positives.',
                    examples: 'WEB-DL, WEBRip, WEB-Cap, BluRay, UHD BluRay, BDRip, BRRip, DVDRip, DVD5, DVD9, VHSRip, Laserdisc, DVDSCR, CAM, TS, TC, R5, HDTV, PDTV, DSR, DVB, TVRip',
                    examplesMore: 'WEBDL, WEBCap, VODRip, DVD-R, LDRip, DVD-Screener, BluRay-Screener, BDScr, DDC, WP, CAMRip, Telesync, Telecine, DCP, HC-HD-Rip, R5-Line, DSRip, DVBRip, SATRip, REMUX',
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
            backupJob: {
                creating: false,
                lastArchivePath: '',
                lastArchiveSizeBytes: 0,
                skipTmpContents: true,
                folderEditable: false,
                savingFolder: false,
                folderSaveError: '',
                lastSavedFolder: '',
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
            activeSettingsTab: 'general',
            categoryAppearanceState: {
                version: 2,
                categories: {},
            },
            expandedTvPackRuleExamples: {},
        };
    },

    created() {
        this.debouncedUpdateYamlPreview = debounce(() => this.updateYamlPreview(), 1000);
        this.backupFolderAutosaveDebounced = debounce(() => {
            void this.persistBackupFolder();
        }, 500);
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

        rawYamlLines() {
            return (this.rawYaml || '').split('\n');
        },

        readmeDirty() {
            return this.readmeContent !== this.readmeOriginal;
        },

        categoryAppearanceCards() {
            const hidden = new Set(['external', 'both']);
            return Object.values(categoryMeta)
                .filter((cat) => !hidden.has(cat.id))
                .map((cat) => {
                    const entry = this.getCategoryAppearance(cat.id);
                    return {
                        ...cat,
                        currentColor: entry.color,
                        currentSaveId: entry.selected_save_id || '',
                        saves: entry.saves,
                        activeSaveName: entry.selected_save_id
                            ? (entry.saves.find((save) => save.id === entry.selected_save_id)?.name || 'Saved preset')
                            : 'Custom color',
                    };
                });
        },
    },

    methods: {
        ...categoryColorsMethods,
        ...foldersMethods,
        ...backupMethods,
        ...tvPackIgnoreMethods,
        ...indexersMethods,
        ...serversMethods,
        ...updatesMethods,
        ...rawYamlMethods,

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

        toggleSetting(key, section = 'processing') {
            const current = this.getToggleState(key, section);
            this.setToggleState(key, !current, section);
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

        setSettingsTab(tabId) {
            this.activeSettingsTab = tabId;
        },

        settingsTabClass(tabId) {
            const base = 'inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors';
            return this.activeSettingsTab === tabId
                ? `${base} border-notion-accent bg-notion-accent text-white`
                : `${base} border-notion-border bg-notion-bg text-notion-text-secondary hover:bg-notion-bg-hover hover:text-notion-text-primary`;
        },

        sectionVisible(sectionId) {
            const q = this._normQuery();
            if (!q) return true;

            switch (sectionId) {
                case 'category-colors':
                    return (
                        this.matchesSearch('category pill colors icon queue preview snapshot save saved preset palette color wheel') ||
                        this.matchesSearch('active snapshot current color queue pill icon')
                    );
                case 'global':
                    return this._globalSectionVisible();
                case 'tv-pack-ignore':
                    return this.matchesSearch('tv pack ignore seasonal pack sxxexx source resolution sample nfo extras subtitles sidecar proof screens nced ncop non-episode episode format ova oad');
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
            } catch (e) {
                console.error('Failed to load settings:', e);
                this.showStatus('Failed to load settings', true);
            } finally {
                this.loading = false;
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
                            backup_folder: this.settings.folders.backup_folder,
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

        // Direct toggle class helpers (for inline boolean props like fp.monitor)
        toggleBtnClassDirect(isOn) {
            const base = 'toggle-switch relative inline-flex h-3.5 w-6 items-center rounded-full transition-colors focus:outline-none';
            return isOn ? `${base} bg-notion-accent` : `${base} bg-notion-bg-hover`;
        },

        toggleKnobClassDirect(isOn) {
            const base = 'inline-block h-2.5 w-2.5 transform rounded-full bg-white transition-transform';
            return isOn ? `${base} translate-x-3` : `${base} translate-x-0.5`;
        },

        getCategoryIcon(category) {
            // Check dynamic categories from the API first
            const found = this.availableCategories.find(c => c.id === category);
            if (found && found.icon) return found.icon;
            return _categoryIcon(category);
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
    }
});
