import { createVuePage, formatUtils, colorClassMap, isMovieType, statusConfig, categoryMeta } from 'page-base';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import debounce from 'lodash.debounce';

dayjs.extend(utc);

// ============================================================
//  UPLOADS PAGE - Full Vue Reactive Implementation
// ============================================================

// Per-season completeness stats for one TV season bucket built by buildUploadGroupSeasons:
// total size, the missing-episode list (when there are at least 2 known episode numbers to
// bound a range), and whether the whole season looks entirely absent.
function computeUploadSeasonStats(season) {
    season.totalSize = season.items.reduce((sum, it) => sum + (Number(it.filesize) || 0), 0);
    season.missingCount = 0;
    season.missingList = [];
    season.expectedEps = 0;
    season.foundEps = 0;
    season.firstEp = 0;
    season.lastEp = 0;
    season.allEpMissing = false;

    if (season.epNumbers.length === 0 && season.items.length > 0) {
        // Season 0 (specials) are inherently incomplete - never flag them as "All EP Missing".
        if (season.num !== 0) {
            season.allEpMissing = true;
        }
    } else if (season.epNumbers.length >= 2) {
        season.epNumbers.sort((a, b) => a - b);
        const first = season.epNumbers[0];
        const last = season.epNumbers[season.epNumbers.length - 1];
        const epSet = new Set(season.epNumbers);
        const missing = [];
        for (let e = first; e <= last; e++) {
            if (!epSet.has(e)) missing.push(e);
        }
        season.firstEp = first;
        season.lastEp = last;
        season.expectedEps = last - first + 1;
        season.foundEps = epSet.size;
        season.missingCount = missing.length;
        season.missingList = missing.slice(0, 20);
    }
}

// Bucket a TV group's items into season sub-groups (or its stray movie-item bucket), expand
// multi-episode ranges (e.g. E05-E06 -> [5, 6]), then compute per-season stats. Fills in
// `group.movieItems`, `.seasonList`, `.missingCount`, and `.allEpMissingSeasonsCount` in place.
function buildUploadGroupSeasons(group) {
    const seasonsMap = new Map();
    for (const item of group.items) {
        // Items individually classified as movies shouldn't be jammed into Season 0 - keep
        // them in a separate bucket.
        if (isMovieType(item.media_type) && !item.season_number && !item.episode_number) {
            group.movieItems.push(item);
            continue;
        }

        const seasonNum = item.season_number || 0;
        const epNum = item.episode_number || null;
        const epEnd = item.episode_end_number || null;

        if (!seasonsMap.has(seasonNum)) {
            seasonsMap.set(seasonNum, {
                num: seasonNum,
                label: 'S' + String(seasonNum).padStart(2, '0'),
                items: [],
                epNumbers: [],
            });
        }
        const season = seasonsMap.get(seasonNum);
        season.items.push(item);
        if (epNum !== null) {
            if (epEnd !== null && epEnd > epNum) {
                for (let e = epNum; e <= epEnd; e++) {
                    season.epNumbers.push(e);
                }
            } else {
                season.epNumbers.push(epNum);
            }
        }
    }

    group.seasonList = Array.from(seasonsMap.values()).sort((a, b) => a.num - b.num);

    group.missingCount = 0;
    group.allEpMissingSeasonsCount = 0;
    for (const season of group.seasonList) {
        computeUploadSeasonStats(season);
        if (season.allEpMissing) group.allEpMissingSeasonsCount++;
        group.missingCount += season.missingCount;
    }
}

// One server-side group -> one row for the grouped-uploads view. A summary-only group (details
// not loaded yet) and a movie group both short-circuit before season analysis; only a loaded TV
// group needs buildUploadGroupSeasons.
function buildUploadGroupFromServerGroup(sg) {
    const rawItems = Array.isArray(sg.items) ? sg.items : [];
    const detailsLoaded = Array.isArray(sg.items) && !sg.summary_only;
    const itemCount = Number(sg.item_count ?? rawItems.length) || 0;
    const titleKey = sg.title_key || (sg.show_name || '').toLowerCase();

    if (!detailsLoaded) {
        return {
            key: titleKey,
            titleKey,
            name: sg.show_name,
            items: rawItems,
            itemCount,
            mediaType: (sg.media_type || 'other').toLowerCase(),
            isMovie: isMovieType(sg.media_type || ''),
            movieItems: [],
            seasonList: [],
            missingCount: 0,
            allEpMissingSeasonsCount: 0,
            totalSize: Number(sg.total_size || 0),
            latestDate: sg.latest_date || null,
            detailsLoaded,
        };
    }

    // Determine media type: use server-provided value, or vote across items for the most
    // common type.
    const rawType = (sg.media_type || '').toLowerCase();
    const isMovie = isMovieType(rawType);

    const group = {
        key: titleKey,
        titleKey,
        name: sg.show_name,
        items: rawItems,
        itemCount,
        mediaType: rawType || 'other',
        isMovie: isMovie,
        movieItems: [],   // movie-typed items inside a TV group
        detailsLoaded,
    };

    group.totalSize = group.items.reduce((sum, it) => sum + (Number(it.filesize) || 0), 0);
    group.latestDate = group.items.reduce((latest, it) => {
        return it.updated_at > latest ? it.updated_at : latest;
    }, group.items[0].updated_at);

    // Movies: skip season/episode analysis entirely
    if (isMovie) {
        group.seasonList = [];
        group.missingCount = 0;
        group.allEpMissingSeasonsCount = 0;
        return group;
    }

    buildUploadGroupSeasons(group);
    return group;
}

// The history routes report a database failure (e.g. a locked DB) either as an error status
// with a `detail` message or as an empty payload carrying an `error` message. Treat the latter
// as a failure too, so an unreadable history is never shown as an empty one.
function throwIfHistoryError(result) {
    if (result && typeof result === 'object' && !Array.isArray(result) && typeof result.error === 'string' && result.error) {
        throw new Error(result.error);
    }
    return result;
}

// Toast text for a failed history request: the generic failure plus the server's message.
function historyErrorMessage(error, fallback) {
    const message = typeof error?.message === 'string' ? error.message.trim() : '';
    if (!message || message === '[object Object]') return fallback;
    return `${fallback}: ${message}`;
}

// Grouped-view fetch branch of loadUploads: pull one page of server-side upload groups.
// Returns false when a newer request has already superseded this one (`self._uploadsRequestSeq`
// moved on), matching the original inline `return;` that used to skip the post-fetch cleanup too.
async function fetchGroupedUploadsPage(self, requestSeq) {
    const params = new URLSearchParams({
        page: self.currentPage,
        per_page: self.pageSize,
        sort_by: self.sortColumn,
        order: self.sortOrder,
        summary_only: 'true',
    });
    if (self.searchQuery) params.append('search', self.searchQuery);
    if (self.filterDestination !== 'all') params.append('destination', self.filterDestination);
    if (self.literalSearch) params.append('literal', 'true');

    const result = await self.apiFetch(`/api/uploads/grouped?${params}`);
    if (requestSeq !== self._uploadsRequestSeq) return false;
    throwIfHistoryError(result);
    self.serverGroups = result.groups || [];
    self.totalGroups = result.total_groups || 0;
    self.totalCount = self.totalGroups;

    self.uploads = [];
    return true;
}

// Flat-view fetch branch of loadUploads: pull one page of the plain upload list. Same
// stale-request contract as fetchGroupedUploadsPage - see its comment.
async function fetchFlatUploadsPage(self, requestSeq) {
    const offset = (self.currentPage - 1) * self.pageSize;
    const params = new URLSearchParams({
        limit: self.pageSize,
        offset: offset,
        sort_by: self.sortColumn,
        order: self.sortOrder,
    });

    if (self.searchQuery) params.append('search', self.searchQuery);
    if (self.filterDestination !== 'all') params.append('destination', self.filterDestination);
    if (self.literalSearch) params.append('literal', 'true');

    const result = await self.apiFetch(`/api/uploads/recent?${params}`);
    if (requestSeq !== self._uploadsRequestSeq) return false;
    throwIfHistoryError(result);

    if (Array.isArray(result)) {
        self.uploads = result;
        self.totalCount = result.length;
    } else {
        self.uploads = result.items || [];
        self.totalCount = result.total !== undefined ? result.total : self.uploads.length;
    }
    return true;
}

const vm = createVuePage({
    persist: ['literalSearch', 'filterDestination', 'sortColumn', 'sortOrder', 'pageSize', 'viewMode', 'knownIssuesShowMuted'],
    data() {
        return {
            // Loaded indexers for dynamic rendering
            indexers: [],

            // Uploads data
            uploads: [],
            loading: true,
            // Server message from the last failed list load (e.g. a database error); '' when it loaded
            uploadsError: '',

            // Stats
            stats: {
                tvLastHour: 0,
                moviesLastHour: 0,
                totalToday: 0,
                avgTime: null,
                latestTs: null,
            },

            // Filters & Search
            searchQuery: '',
            literalSearch: false,
            filterDestination: 'all',

            // Sorting
            sortColumn: 'when',
            sortOrder: 'desc',

            // Pagination
            currentPage: 1,
            pageSize: 50,
            totalCount: 0,

            // Selection for bulk operations
            selectedItems: new Set(),
            selectAll: false,

            // View mode
            viewMode: 'grouped',  // 'flat', 'grouped', or 'jobs'
            expandedGroups: {},
            expandedSeasons: {},
            loadingGroups: {},

            // Server-side grouped data
            serverGroups: [],
            totalGroups: 0,

            // Jobs view data
            jobsList: [],
            selectedJobIds: [],

            // Job uploads modal
            jobModalOpen: false,
            jobModalTitle: '',
            jobModalUploads: [],
            jobModalLoading: false,

            // Upload item details modal
            uploadDetailModalOpen: false,
            uploadDetailItem: null,

            // Known Issues panel: grouped failed-submission signatures (core.database.get_grouped_upload_errors)
            knownIssuesOpen: false,
            knownIssuesLoading: false,
            knownIssuesShowMuted: true,
            knownIssues: [],

            // Prevent stale async responses from clobbering newer results
            _uploadsRequestSeq: 0,
        };
    },

    created() {
        this.debouncedLoadUploads = debounce(() => this.loadUploads(true), 400);
    },

    computed: {
        totalPages() {
            return Math.max(1, Math.ceil(this.totalCount / this.pageSize));
        },

        showingStart() {
            if (this.totalCount === 0) return 0;
            return (this.currentPage - 1) * this.pageSize + 1;
        },

        showingEnd() {
            return Math.min(this.currentPage * this.pageSize, this.totalCount);
        },

        selectedCount() {
            return this.selectedItems.size;
        },

        isFirstPage() {
            return this.currentPage <= 1;
        },

        isLastPage() {
            return this.currentPage >= this.totalPages;
        },

        // Jobs view: all selected check
        isAllJobsSelected() {
            return this.jobsList.length > 0 && this.jobsList.every(j => this.selectedJobIds.includes(j.job_id));
        },

        // Table data length - unified for loading/empty state
        tableDataLength() {
            if (this.viewMode === 'jobs') return this.jobsList.length;
            if (this.viewMode === 'grouped') return this.serverGroups.length;
            return this.uploads.length;
        },

        // Destination filter options (dynamic from indexers)
        destinationOptions() {
            const options = [
                { value: 'all', label: 'All Destinations' },
                { value: 'incomplete', label: 'Incomplete Uploads' },
            ];
            this.indexers
                .filter(idx => idx.enabled)
                .forEach(idx => {
                    options.push({ value: idx.id, label: idx.name });
                });
            return options;
        },

        // Empty state message for the table
        emptyStateMessage() {
            if (this.uploadsError) return historyErrorMessage({ message: this.uploadsError }, 'Could not load upload history');
            return this.searchQuery
                ? 'No matches found for "' + this.searchQuery + '"'
                : 'No uploads found';
        },

        // Grouped uploads computed - processes server-side groups into season tree
        groupedUploads() {
            if (!this.serverGroups || this.serverGroups.length === 0) return [];
            return this.serverGroups.map(buildUploadGroupFromServerGroup);
        },

        // Flattened row list for grouped view - handles expand/collapse state
        groupedRows() {
            const rows = [];
            for (const group of this.groupedUploads) {
                // Always show group header
                rows.push({ type: 'group', key: 'g-' + group.key, group });

                if (!this.expandedGroups[group.key]) continue;

                if (!group.detailsLoaded) {
                    rows.push({ type: 'loading', key: 'loading-' + group.key, group });
                    continue;
                }

                if (group.isMovie) {
                    // Movies: show items directly under the group (no season sub-headers)
                    for (const item of group.items) {
                        rows.push({ type: 'item', key: 'i-' + item.item_name, item, group, indent: 1, displayName: this.getEpisodeDisplayName(item.item_name, group.name) });
                    }
                } else {
                    // TV group: show any movie-typed items first (flat, no season header)
                    for (const item of group.movieItems) {
                        rows.push({ type: 'item', key: 'i-' + item.item_name, item, group, indent: 1, isMovieInTvGroup: true, displayName: this.getEpisodeDisplayName(item.item_name, group.name) });
                    }
                    // Then season sub-headers
                    for (const season of group.seasonList) {
                        const sKey = group.key + '-S' + season.num;
                        rows.push({ type: 'season', key: 's-' + sKey, group, season, seasonKey: sKey });
                        if (this.expandedSeasons[sKey]) {
                            for (const item of season.items) {
                                rows.push({ type: 'item', key: 'i-' + item.item_name, item, group, indent: 2, displayName: this.getEpisodeDisplayName(item.item_name, group.name) });
                            }
                        }
                    }
                }
            }
            return rows;
        },
    },

    methods: {
        // ============================================================
        //  Category Icon Helpers
        // ============================================================
        _resolveCategoryKey(typeOrCategory) {
            if (!typeOrCategory) return null;
            const t = typeOrCategory.toLowerCase();
            // Direct match
            if (categoryMeta[t]) return t;
            // Map media_type values to categoryMeta keys
            if (t === 'movie') return 'movies';
            if (t === 'tv' || t === 'episode') return 'tv';
            if (t === 'other') return 'misc';
            return null;
        },

        getCategoryIconName(typeOrCategory) {
            const key = this._resolveCategoryKey(typeOrCategory);
            return key && categoryMeta[key] ? categoryMeta[key].icon : 'file-video';
        },

        getCategoryIconColor(typeOrCategory) {
            const key = this._resolveCategoryKey(typeOrCategory);
            if (!key || !categoryMeta[key]) return 'text-notion-text-tertiary';
            const color = categoryMeta[key].color;
            return `text-${color}-400`;
        },

        getCategoryIconBg(typeOrCategory) {
            const key = this._resolveCategoryKey(typeOrCategory);
            if (!key || !categoryMeta[key]) return 'bg-notion-bg-hover';
            const color = categoryMeta[key].color;
            return `bg-${color}-500/15`;
        },

        getCategoryLabel(typeOrCategory) {
            const key = this._resolveCategoryKey(typeOrCategory);
            return key && categoryMeta[key] ? categoryMeta[key].label : (typeOrCategory || 'Unknown');
        },

        // ============================================================
        //  Date Formatting (compact)
        // ============================================================
        parseHistoryDate(dateValue) {
            if (!dateValue) return null;
            if (dayjs.isDayjs(dateValue)) return dateValue;
            if (dateValue instanceof Date) return dayjs(dateValue);

            if (typeof dateValue === 'string') {
                const value = dateValue.trim();
                if (!value) return null;

                const hasExplicitTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
                const looksIsoLike = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/i.test(value);

                if (!hasExplicitTimezone && looksIsoLike) {
                    return dayjs.utc(value).local();
                }

                return dayjs(value);
            }

            return dayjs(dateValue);
        },

        formatDateCompact(dateStr) {
            if (!dateStr) return '-';
            const d = this.parseHistoryDate(dateStr);
            if (!d || !d.isValid()) return '-';
            return d.format('M/D h:mm A');
        },

        formatDateFull(dateStr) {
            if (!dateStr) return '';
            const d = this.parseHistoryDate(dateStr);
            if (!d || !d.isValid()) return '';
            return d.format('M/D/YYYY h:mm:ss A');
        },

        formatWhenAge(dateStr) {
            if (!dateStr) return '-';
            const d = this.parseHistoryDate(dateStr);
            if (!d || !d.isValid()) return '-';

            const now = dayjs();
            const minutes = Math.max(0, now.diff(d, 'minute'));
            const hours = Math.max(0, now.diff(d, 'hour'));
            const days = Math.max(0, now.diff(d, 'day'));

            if (minutes < 1) return 'Just now';
            if (hours < 1) return `${minutes}m ago`;
            if (hours < 48) return `${hours}h ago`;
            if (days < 7) return d.format('ddd');
            if (now.year() === d.year()) return d.format('M/D');
            return d.format('M/D/YY');
        },

        formatWhenTime(dateStr) {
            if (!dateStr) return '-';
            const d = this.parseHistoryDate(dateStr);
            if (!d || !d.isValid()) return '-';
            return d.format('h:mm A');
        },

        // ============================================================
        //  Jobs View Helpers
        // ============================================================
        getJobStatusConfig(status) {
            return statusConfig[status] || statusConfig.default || { icon: 'circle', color: 'text-notion-text-tertiary', bg: 'bg-notion-bg-hover', text: 'text-notion-text-tertiary' };
        },

        jobStatusBadgeClass(status) {
            const config = this.getJobStatusConfig(status);
            return `inline-flex items-center gap-1 px-2 rounded uppercase font-bold tracking-tight py-0.5 text-[10px] ${config.bg} ${config.text || config.color}`;
        },

        toggleSelectAllJobs() {
            if (this.isAllJobsSelected) {
                this.selectedJobIds = [];
            } else {
                this.selectedJobIds = this.jobsList.map(j => j.job_id);
            }
        },

        async loadJobs() {
            if (!this.isConnected) return;
            this.loading = true;
            this.selectedJobIds = [];
            try {
                this.jobsList = throwIfHistoryError(await this.apiFetch('/api/uploads/history?limit=100'));
            } catch (e) {
                if (!e.isOffline) {
                    this.showToast('error', 'Error', historyErrorMessage(e, 'Failed to load job history'));
                }
            } finally {
                this.loading = false;
            }
        },

        async showJobUploads(job) {
            this.jobModalTitle = `${job.category || ''} - ${job.job_id}`;
            this.jobModalUploads = [];
            this.jobModalLoading = true;
            this.jobModalOpen = true;
            try {
                this.jobModalUploads = throwIfHistoryError(await this.apiFetch(`/api/uploads/history/${job.job_id}/uploads`));
            } catch (e) {
                if (!e.isOffline) {
                    this.showToast('error', 'Error', historyErrorMessage(e, 'Failed to load upload details'));
                }
            } finally {
                this.jobModalLoading = false;
            }
        },

        openUploadDetails(item) {
            if (!item) return;
            this.uploadDetailItem = item;
            this.uploadDetailModalOpen = true;
        },

        closeUploadDetails() {
            this.uploadDetailModalOpen = false;
            this.uploadDetailItem = null;
        },

        indexerOrderIndex(indexerId) {
            const id = String(indexerId || '').toLowerCase();
            const idx = (this.indexers || []).findIndex(indexer => String(indexer.id || '').toLowerCase() === id);
            return idx >= 0 ? idx : Number.MAX_SAFE_INTEGER;
        },

        orderDestinations(destinations) {
            if (!Array.isArray(destinations)) return [];
            return destinations.slice().sort((a, b) => {
                const ai = this.indexerOrderIndex(a?.id);
                const bi = this.indexerOrderIndex(b?.id);
                if (ai !== bi) return ai - bi;
                return String(a?.name || a?.id || '').localeCompare(String(b?.name || b?.id || ''), undefined, { numeric: true, sensitivity: 'base' });
            });
        },

        getUploadDetailDestinations(item) {
            if (!item || !Array.isArray(item.destinations)) return [];
            return this.orderDestinations(item.destinations).map(dest => {
                const indexer = this.indexers.find(idx => idx.id.toLowerCase() === (dest.id || '').toLowerCase());
                return {
                    id: dest.id,
                    name: dest.name || (indexer ? indexer.name : null) || dest.id || 'Unknown',
                    status: dest.status || 'success',
                    error: dest.error || null,
                    duration: dest.duration || null,
                    speed: this.getDisplaySpeed(item, dest),
                    server: dest.server_name || null,
                    favicon: (indexer ? indexer.favicon_url : null) || null,
                    icon: (indexer ? indexer.icon : null) || 'database',
                };
            });
        },

        prettyJson(value) {
            try {
                return JSON.stringify(value || {}, null, 2);
            } catch (_e) {
                return '{}';
            }
        },

        async deleteJobHistory(jobId) {
            const ok = await this.confirmDialog(`Delete job "${jobId}" from history?`, {
                title: 'Delete Job',
                detail: 'This only removes the history record. Uploaded files are not touched.',
                danger: true,
                confirmLabel: 'Delete',
            });
            if (!ok) return;
            try {
                throwIfHistoryError(await this.apiFetch('/api/uploads/history', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ job_ids: [jobId] }),
                }));
                this.showToast('success', 'Deleted', 'Job removed from history');
                await this.loadJobs();
            } catch (e) {
                if (!e.isOffline) {
                    this.showToast('error', 'Error', historyErrorMessage(e, 'Failed to delete job'));
                }
            }
        },

        async bulkDeleteJobs() {
            if (this.selectedJobIds.length === 0) return;
            const count = this.selectedJobIds.length;
            const ok = await this.confirmDialog(`Delete ${count} job${count !== 1 ? 's' : ''} from history?`, {
                title: 'Delete Jobs',
                detail: 'This only removes the history records. Uploaded files are not touched.',
                danger: true,
                confirmLabel: `Delete ${count} Job${count !== 1 ? 's' : ''}`,
            });
            if (!ok) return;
            try {
                throwIfHistoryError(await this.apiFetch('/api/uploads/history', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ job_ids: [...this.selectedJobIds] }),
                }));
                this.showToast('success', 'Deleted', `${count} job${count !== 1 ? 's' : ''} removed`);
                this.selectedJobIds = [];
                await this.loadJobs();
            } catch (e) {
                if (!e.isOffline) {
                    this.showToast('error', 'Error', historyErrorMessage(e, 'Failed to delete jobs'));
                }
            }
        },

        // ============================================================
        //  Known Issues Panel
        // ============================================================
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

        // ============================================================
        //  Data Loading
        // ============================================================
        async loadIndexers() {
            try {
                const data = await this.apiFetch('/api/indexers');
                this.indexers = (data || []).map(idx => ({ ...idx, faviconError: false }));
            } catch (e) {
                // apiFetch handles logging if needed
            }
        },

        async loadStats() {
            if (!this.isConnected) return;
            try {
                const hourly = await this.apiFetch('/api/uploads/hourly-stats');
                this.stats.tvLastHour = hourly.tv_last_hour || 0;
                this.stats.moviesLastHour = hourly.movies_last_hour || 0;
                this.stats.totalToday = hourly.total_today || 0;
                this.stats.avgTime = hourly.avg_time_per_upload ?? 0;
                this.stats.latestTs = hourly.latest_ts || null;
            } catch (e) {
                // apiFetch handles logging
            }
        },

        async loadUploads(resetPage = true, isSilent = false) {
            if (!this.isConnected) return;

            // Jobs mode uses a separate loader
            if (this.viewMode === 'jobs') {
                if (!isSilent) await this.loadJobs();
                return;
            }

            const requestSeq = ++this._uploadsRequestSeq;

            if (resetPage) this.currentPage = 1;

            if (!isSilent) {
                this.loading = true;
            }

            try {
                const fetched = this.viewMode === 'grouped'
                    ? await fetchGroupedUploadsPage(this, requestSeq)
                    : await fetchFlatUploadsPage(this, requestSeq);
                if (!fetched) return;
                this.uploadsError = '';

                // Clamp current page if needed
                if (this.currentPage > this.totalPages) {
                    this.currentPage = this.totalPages;
                }

                // Reset selection
                this.selectedItems.clear();
                this.selectAll = false;

            } catch (e) {
                if (requestSeq === this._uploadsRequestSeq && !e.isOffline) {
                    this.uploadsError = e.message || 'Unknown error';
                    if (!isSilent) {
                        this.showToast('error', 'Error', historyErrorMessage(e, 'Failed to load uploads'));
                    }
                }
            } finally {
                if (requestSeq === this._uploadsRequestSeq) {
                    this.loading = false;
                }
            }
        },

        // ============================================================
        //  Sorting
        // ============================================================
        handleSort(column) {
            if (this.sortColumn === column) {
                this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
            } else {
                this.sortColumn = column;
                this.sortOrder = 'desc';
            }
            this.loadUploads(true);
        },

        getSortIcon(column) {
            if (this.sortColumn !== column) return 'chevrons-up-down';
            return this.sortOrder === 'asc' ? 'chevron-up' : 'chevron-down';
        },

        // Prettier-safe sort icon class helper
        sortIconClass(column) {
            const base = 'size-3';
            return this.sortColumn === column ? base : `${base} opacity-40`;
        },

        // ============================================================
        //  Pagination
        // ============================================================
        goToPage(page) {
            if (page < 1 || page > this.totalPages) return;
            this.currentPage = page;
            this.loadUploads(false);
        },

        prevPage() {
            if (!this.isFirstPage) this.goToPage(this.currentPage - 1);
        },

        nextPage() {
            if (!this.isLastPage) this.goToPage(this.currentPage + 1);
        },

        // ============================================================
        //  Selection
        // ============================================================
        toggleSelectAll(checked) {
            if (checked) {
                this.uploads.forEach(item => this.selectedItems.add(item.item_name));
            } else {
                this.selectedItems.clear();
            }
            this.selectAll = checked;
        },

        toggleItemSelection(itemName, checked) {
            if (checked) {
                this.selectedItems.add(itemName);
            } else {
                this.selectedItems.delete(itemName);
            }
            // Update selectAll state
            this.selectAll = this.selectedItems.size === this.uploads.length && this.uploads.length > 0;
        },

        isItemSelected(itemName) {
            return this.selectedItems.has(itemName);
        },

        // ============================================================
        //  Delete Operations
        // ============================================================
        async deleteUpload(itemName) {
            const ok = await this.confirmDialog(`Delete "${itemName}" from upload history?`, {
                title: 'Delete Upload Record',
                detail: 'This only removes the database record. The file itself is not deleted.',
                danger: true,
                confirmLabel: 'Delete',
            });
            if (!ok) return;
            try {
                throwIfHistoryError(await this.apiDelete(`/api/uploads/item/${encodeURIComponent(itemName)}`));
                this.loadUploads(false);
                this.loadStats();
                this.showToast('success', 'Deleted', 'Item removed from history');
            } catch (e) {
                console.error('Delete failed:', e);
                this.showToast('error', 'Error', historyErrorMessage(e, 'Failed to delete item'));
            }
        },

        async bulkDelete() {
            const selected = Array.from(this.selectedItems);
            if (selected.length === 0) return;

            const ok = await this.confirmDialog(`Delete ${selected.length} items from upload history?`, {
                title: 'Delete Upload Records',
                detail: 'This only removes the database records. The files themselves are not deleted.',
                danger: true,
                confirmLabel: `Delete ${selected.length} Items`,
            });
            if (!ok) return;

            try {
                throwIfHistoryError(await this.apiPost('/api/uploads/item/bulk-delete', { item_names: selected }));
                this.showToast('success', 'Deleted', `${selected.length} items removed from history`);
                this.loadUploads(false);
                this.loadStats();
            } catch (e) {
                console.error('Bulk delete failed:', e);
                this.showToast('error', 'Error', historyErrorMessage(e, 'Failed to delete items'));
            }
        },

        // Display Helpers
        // ============================================================

        // ── Grouped view helpers ──
        async toggleGroup(key) {
            const next = !this.expandedGroups[key];
            this.expandedGroups[key] = next;
            if (!next) return;
            const group = this.groupedUploads.find(g => g.key === key);
            if (group && !group.detailsLoaded) {
                await this.loadGroupDetails(group);
            }
        },

        toggleSeason(key) {
            this.expandedSeasons[key] = !this.expandedSeasons[key];
        },

        isGroupSelected(group) {
            if (!group || !Array.isArray(group.items) || !group.items.length) return false;
            const eps = group.items.filter(it => it.episode_number != null);
            if (eps.length === 0) {
                // No individual episodes - check if ALL items are selected
                return group.items.length > 0 && group.items.every(it => this.selectedItems.has(it.item_name));
            }
            // If eps exist, check if all EPISODES are selected (ignore packs)
            return eps.every(it => this.selectedItems.has(it.item_name));
        },

        toggleGroupSelection(group, checked) {
            if (!group || !Array.isArray(group.items) || !group.items.length) return;
            const eps = group.items.filter(it => it.episode_number != null);
            const targets = eps.length > 0 ? eps : group.items;

            targets.forEach(it => {
                if (checked) {
                    this.selectedItems.add(it.item_name);
                } else {
                    this.selectedItems.delete(it.item_name);
                }
            });

            // If we are unchecking, and it's a season/group header, also uncheck any packs in that group/season
            // because otherwise checking the season again would do nothing (behaviorally, usually uncheck = full clear)
            if (!checked) {
                group.items.forEach(it => this.selectedItems.delete(it.item_name));
            }

            this.selectAll = this.selectedItems.size === this.uploads.length && this.uploads.length > 0;
        },

        async loadGroupDetails(group) {
            if (!group || !group.titleKey || this.loadingGroups[group.key]) return;
            this.loadingGroups = { ...this.loadingGroups, [group.key]: true };
            try {
                const params = new URLSearchParams({ title_key: group.titleKey });
                if (this.filterDestination !== 'all') params.append('destination', this.filterDestination);
                const data = throwIfHistoryError(await this.apiFetch(`/api/uploads/grouped/items?${params}`));
                const items = Array.isArray(data.items) ? data.items : [];
                this.serverGroups = this.serverGroups.map(sg => {
                    const sgKey = sg.title_key || (sg.show_name || '').toLowerCase();
                    if (sgKey !== group.key) return sg;
                    return {
                        ...sg,
                        items,
                        item_count: items.length || sg.item_count || 0,
                        summary_only: false,
                    };
                });
                this.uploads = this.serverGroups.flatMap(sg => Array.isArray(sg.items) ? sg.items : []);
            } catch (e) {
                if (!e.isOffline) {
                    this.showToast('error', 'Error', historyErrorMessage(e, 'Failed to load group details'));
                }
                this.expandedGroups[group.key] = false;
            } finally {
                const next = { ...this.loadingGroups };
                delete next[group.key];
                this.loadingGroups = next;
            }
        },

        getItemDisplayName(itemName) {
            if (!itemName) return '';
            // Strip folder prefix: show just the filename part
            const slashIdx = itemName.lastIndexOf('/');
            return slashIdx >= 0 ? itemName.substring(slashIdx + 1) : itemName;
        },

        getEpisodeDisplayName(itemName, groupName) {
            if (!itemName) return '';
            // Strip folder prefix first so entries are distinguishable
            let name = this.getItemDisplayName(itemName);
            if (!groupName) return name;
            const lower = name.toLowerCase();
            const prefix = groupName.toLowerCase();
            if (lower.startsWith(prefix)) {
                // Strip the shared show-name prefix and any leading separators
                return name.substring(groupName.length).replace(/^[.\s-]+/, '') || name;
            }
            return name;
        },

        getGroupDests(group) {
            if (!group || !Array.isArray(group.items) || group.items.length === 0) return [];
            // Collect all unique destination IDs across the group
            const destMap = new Map();
            for (const item of group.items) {
                const dots = this.getDestinationDots(item);
                for (const d of dots) {
                    if (!destMap.has(d.id)) {
                        destMap.set(d.id, { ...d, count: 0, allDone: true });
                    }
                    if (d.status === 'success') {
                        destMap.get(d.id).count++;
                    }
                }
            }
            // Check if all episodes uploaded to each dest
            for (const [, dest] of destMap) {
                dest.allDone = dest.count >= group.items.length;
            }
            return Array.from(destMap.values());
        },

        getDestinationDots(item) {
            if (!item.destinations) return [];
            return this.orderDestinations(item.destinations)
                .filter(dest => {
                    const idx = this.indexers.find(i => i.id.toLowerCase() === String(dest.id || '').toLowerCase());
                    return idx && idx.enabled;
                })
                .map(dest => {
                    const indexer = this.indexers.find(idx => idx.id.toLowerCase() === dest.id.toLowerCase());
                    return {
                        id: dest.id,
                        name: dest.name,
                        color: indexer ? indexer.color : (dest.color || 'gray'),
                        favicon: indexer ? indexer.favicon_url : null,
                        icon: indexer ? indexer.icon : 'database',
                        status: dest.status || 'success',
                        error: dest.error || null,
                        faviconError: false
                    };
                });
        },

        // Prettier-safe class generator for destination dots
        destDotClass(dest) {
            const classes = this.getColorClasses(dest.color);
            return `size-1.5 rounded-full ${classes.dot}`;
        },

        // Prettier-safe class for speed column
        speedCellClass(item) {
            const stats = this.getBestStats(item);
            const hasSpeed = stats && stats.speed;
            return hasSpeed ? 'px-2 py-1.5 text-right hidden lg:table-cell whitespace-nowrap text-green-400'
                : 'px-2 py-1.5 text-right hidden lg:table-cell whitespace-nowrap text-notion-text-tertiary';
        },

        getDisplaySpeed(item, dest) {
            const duration = Number(dest?.duration) || 0;
            const rawSpeed = Number(dest?.speed) || 0;
            const filesize = Number(item?.filesize) || 0;

            if (duration > 0 && filesize > 0) {
                return filesize / duration;
            }

            return rawSpeed > 0 ? rawSpeed : null;
        },

        getBestStats(item) {
            let bestDuration = null;
            let bestSpeed = null;
            let serverUsed = null;

            if (item.destinations && item.destinations.length > 0) {
                item.destinations.forEach(dest => {
                    const duration = Number(dest.duration) || 0;
                    const speed = this.getDisplaySpeed(item, dest);

                    if (duration > 0 && (bestDuration === null || duration > bestDuration)) {
                        bestDuration = duration;
                        bestSpeed = speed;
                        serverUsed = dest.server_name || serverUsed;
                    } else if (bestDuration === null && speed && (bestSpeed === null || speed > bestSpeed)) {
                        bestSpeed = speed;
                    }
                    if (dest.server_name && !serverUsed) serverUsed = dest.server_name;
                });
            }

            return {
                duration: bestDuration,
                speed: bestSpeed,
                server: serverUsed || '-'
            };
        },

        // highlightSearch, escapeRegex, escapeHtml are inherited from page-base shared methods
    },

    watch: {
        searchQuery() {
            // Debounced search
            this.debouncedLoadUploads();
        },
        literalSearch() {
            this.loadUploads(true);
        },
        filterDestination() {
            this.loadUploads(true);
        },
        pageSize() {
            this.loadUploads(true);
        },
        viewMode() {
            // Reload data from correct endpoint when switching modes
            this.expandedGroups = {};
            this.expandedSeasons = {};
            this.loadUploads(true);
        },
    },

    async mounted() {
        this.loadIndexers();
        this.loadStats();
        this.loadUploads();
        this.loadKnownIssues();

        // Stamp the initial load so the 60s fallback measures from now
        this._lastHistoryFullRefresh = Date.now();

        // Change-detection poll: check stats every 5s, only reload list when total changed
        this.startInterval(async () => {
            if (this.searchQuery) return;

            if (this.viewMode === 'jobs') {
                // Jobs complete infrequently - cap at 60s
                const now = Date.now();
                if (now - this._lastHistoryFullRefresh > 60_000) {
                    this._lastHistoryFullRefresh = now;
                    await this.loadJobs();
                }
                return;
            }

            // Lightweight change probe: check both count and latest timestamp.
            // totalToday catches new completions; latestTs catches failures and
            // any other status changes that don't add new rows.
            const prevTotal = this.stats.totalToday;
            const prevTs = this.stats.latestTs;
            await this.loadStats();

            const now = Date.now();
            const uploadsChanged = this.stats.totalToday !== prevTotal || this.stats.latestTs !== prevTs;
            const fallbackDue = now - this._lastHistoryFullRefresh > 60_000;

            if (uploadsChanged || fallbackDue) {
                this._lastHistoryFullRefresh = now;
                this.loadUploads(false, true); // silent - no spinner
            }
        }, 5_000); // probe every 5s; DOM only refreshes on change or 60s fallback
    }
});
