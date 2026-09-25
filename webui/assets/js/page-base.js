import { createApp } from 'vue';
import copy from 'copy-to-clipboard';
import { computePosition, flip, shift, offset } from '@floating-ui/dom';
import { formatUtils } from './shared/format.js';
import { statusConfig, getStatusConfig as statusConfigFor } from './shared/status.js';
import {
    mapHistorySeries,
    mapDeltaHistorySeries,
    seedHistorySeries,
    appendHistoryPoint,
    computePositiveRateDelta,
    sumNumericFields,
    sparkWindow,
} from './shared/series.js';
import { itypeToCategory, categoryToItype } from './shared/category.js';
import * as jobs from './shared/jobs.js';
import { LucideIcon } from './components/lucide-icon.js';
import { Modal } from './components/modal.js';
import { Sparkline } from './components/sparkline.js';
import { StatsCard } from './components/stats-card.js';
import { apiMethods } from './core/api.js';
import { pollMethods, stopPolling } from './core/poll.js';

export { formatUtils, statusConfig };
export { mapHistorySeries, mapDeltaHistorySeries, seedHistorySeries, appendHistoryPoint, computePositiveRateDelta, sumNumericFields, sparkWindow };
export { itypeToCategory, categoryToItype };

// ============================================================
//  COLOR MAP FOR INDEXERS
// ============================================================

export const colorClassMap = {
    'purple': { bg: 'bg-idx-purple/15', text: 'text-idx-purple', dot: 'bg-idx-purple' },
    'orange': { bg: 'bg-idx-orange/15', text: 'text-idx-orange', dot: 'bg-idx-orange' },
    'blue': { bg: 'bg-idx-blue/15', text: 'text-idx-blue', dot: 'bg-idx-blue' },
    'pink': { bg: 'bg-idx-pink/15', text: 'text-idx-pink', dot: 'bg-idx-pink' },
    'green': { bg: 'bg-idx-green/15', text: 'text-idx-green', dot: 'bg-idx-green' },
    'cyan': { bg: 'bg-idx-cyan/15', text: 'text-idx-cyan', dot: 'bg-idx-cyan' },
    'rose': { bg: 'bg-idx-rose/15', text: 'text-idx-rose', dot: 'bg-idx-rose' },
    'red': { bg: 'bg-idx-red/15', text: 'text-idx-red', dot: 'bg-idx-red' },
    'amber': { bg: 'bg-idx-amber/15', text: 'text-idx-amber', dot: 'bg-idx-amber' },
    'emerald': { bg: 'bg-emerald-500/15', text: 'text-emerald-400', dot: 'bg-emerald-400' },
    'slate': { bg: 'bg-slate-500/15', text: 'text-slate-400', dot: 'bg-slate-400' },
    'gray': { bg: 'bg-notion-bg-hover', text: 'text-notion-text-secondary', dot: 'bg-gray-400' },
};

/**
 * Build a badge class string: the shared `.badge` layout primitive (padding/
 * rounded/font-size, see tailwind-input.css) plus the bg/text tint for a
 * colorClassMap key. Falls back to the neutral 'gray' tint for unknown colors.
 * Extra classes (e.g. 'font-bold uppercase') can be appended by the caller.
 */
export function badgeClass(color, extraClasses = '') {
    const tint = colorClassMap[color] || colorClassMap['gray'];
    return `badge ${tint.bg} ${tint.text}${extraClasses ? ' ' + extraClasses : ''}`;
}

// ============================================================
//  MEDIA TYPE / CATEGORY UTILITIES  (shared across all pages)
// ============================================================

/**
 * Canonical metadata for every known category.
 * Pages should use these instead of inline icon/color/label maps.
 * The colours match DEFAULT_CATEGORY_HEX below (Dashboard, History, Settings).
 */
export const categoryMeta = {
    tv: { id: 'tv', label: 'TV', icon: 'tv', color: 'cyan', badgeClass: 'bg-cyan-500/15 text-cyan-400' },
    movies: { id: 'movies', label: 'Movies', icon: 'film', color: 'purple', badgeClass: 'bg-purple-500/15 text-purple-400' },
    misc: { id: 'misc', label: 'Misc', icon: 'box', color: 'amber', badgeClass: 'bg-amber-500/15 text-amber-400' },
    anime: { id: 'anime', label: 'Anime', icon: 'tv', color: 'pink', badgeClass: 'bg-pink-500/15 text-pink-400' },
    disc: { id: 'disc', label: 'DISC', icon: 'disc-3', color: 'slate', badgeClass: 'bg-slate-500/15 text-slate-400' },
    music: { id: 'music', label: 'Music', icon: 'music', color: 'green', badgeClass: 'bg-green-500/15 text-green-400' },
    books: { id: 'books', label: 'Books', icon: 'book-open', color: 'blue', badgeClass: 'bg-blue-500/15 text-blue-400' },
    apps: { id: 'apps', label: 'Apps', icon: 'app-window', color: 'orange', badgeClass: 'bg-orange-500/15 text-orange-400' },
    audiobooks: { id: 'audiobooks', label: 'Audiobooks', icon: 'headphones', color: 'orange', badgeClass: 'bg-orange-500/15 text-orange-400' },
    ebooks: { id: 'ebooks', label: 'Ebooks', icon: 'book-open', color: 'blue', badgeClass: 'bg-blue-500/15 text-blue-400' },
    external: { id: 'external', label: 'External', icon: 'folder-input', color: 'gray', badgeClass: 'bg-notion-bg-hover text-notion-text-secondary' },
    both: { id: 'both', label: 'Both', icon: 'layers', color: 'blue', badgeClass: 'bg-blue-500/15 text-blue-400' },
};

// The Queue page has its own palette (orange Misc, blue Music, emerald Books,
// red Apps, light grey DISC badge). Only the differences are listed here.
const QUEUE_CATEGORY_PALETTE = {
    misc: { color: 'orange', badgeClass: 'bg-orange-500/15 text-orange-400' },
    disc: { badgeClass: 'bg-[#E0E0E0] text-[#2A2A2A] border-[#B9B9B9]' },
    music: { color: 'blue', badgeClass: 'bg-blue-500/15 text-blue-400' },
    books: { color: 'emerald', badgeClass: 'bg-emerald-500/15 text-emerald-400' },
    apps: { color: 'red', badgeClass: 'bg-red-500/15 text-red-400' },
};

export const queueCategoryMeta = Object.fromEntries(
    Object.entries(categoryMeta).map(([id, meta]) => [id, { ...meta, ...(QUEUE_CATEGORY_PALETTE[id] || {}) }])
);

// Revision of the Queue page's saved-state key (nzbpostarr_persist_queue_<rev>).
export const QUEUE_PERSIST_REV = '20260805_queue_hotfix_r5';

export const CATEGORY_APPEARANCE_STORAGE_KEY = 'nzbpostarr_category_appearance_v2';
const LEGACY_CATEGORY_APPEARANCE_STORAGE_KEYS = ['nzbpostarr_category_appearance_v1'];

const DEFAULT_CATEGORY_HEX = {
    tv: '#22d3ee',
    movies: '#a855f7',
    misc: '#f59e0b',
    anime: '#ec4899',
    disc: '#94a3b8',
    music: '#22c55e',
    books: '#3b82f6',
    apps: '#f97316',
    audiobooks: '#fb923c',
    ebooks: '#60a5fa',
    external: '#9ca3af',
    both: '#3b82f6',
};

function makeCategoryAppearanceState() {
    return {
        version: 2,
        categories: {},
    };
}

export function normalizeCategoryHexColor(value, fallback = '#9ca3af') {
    const raw = String(value || '').trim();
    const base = /^#?[0-9a-f]{6}$/i.test(raw)
        ? raw.replace(/^#/, '')
        : /^#?[0-9a-f]{3}$/i.test(raw)
            ? raw.replace(/^#/, '').split('').map((ch) => ch + ch).join('')
            : '';
    return base ? `#${base.toUpperCase()}` : fallback;
}

export function hexToRgba(hex, alpha = 1) {
    const normalized = normalizeCategoryHexColor(hex);
    const base = normalized.replace('#', '');
    const r = parseInt(base.slice(0, 2), 16);
    const g = parseInt(base.slice(2, 4), 16);
    const b = parseInt(base.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function getDefaultCategoryHex(catId) {
    return DEFAULT_CATEGORY_HEX[catId] || '#9ca3af';
}

function normalizeCategoryAppearanceSave(save, fallbackColor, index = 0) {
    if (!save || typeof save !== 'object') return null;
    const id = save.id !== undefined && save.id !== null ? String(save.id) : '';
    if (!id) return null;
    return {
        id,
        name: String(save.name || `Saved ${index + 1}`),
        color: normalizeCategoryHexColor(save.color || fallbackColor, fallbackColor),
        created_at: save.created_at || null,
    };
}

function normalizeCategoryAppearanceState(parsed) {
    const sourceCategories = parsed && typeof parsed === 'object' && parsed.categories && typeof parsed.categories === 'object'
        ? parsed.categories
        : {};
    const categories = {};
    Object.entries(sourceCategories).forEach(([catId, value]) => {
        if (!value || typeof value !== 'object') return;
        const fallbackColor = getDefaultCategoryHex(catId);
        const saves = Array.isArray(value.saves)
            ? value.saves.map((save, index) => normalizeCategoryAppearanceSave(save, fallbackColor, index)).filter(Boolean)
            : [];
        categories[catId] = {
            color: normalizeCategoryHexColor(value.color || fallbackColor, fallbackColor),
            selected_save_id: value.selected_save_id ? String(value.selected_save_id) : '',
            saves,
        };
    });
    return {
        version: 2,
        categories,
    };
}

function readCategoryAppearanceStateFromKeys(keys) {
    for (const key of keys) {
        try {
            const raw = localStorage.getItem(key);
            if (!raw) continue;
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed === 'object') {
                return normalizeCategoryAppearanceState(parsed);
            }
        } catch (_err) {
            continue;
        }
    }
    return null;
}

export function loadCategoryAppearanceState() {
    const current = readCategoryAppearanceStateFromKeys([CATEGORY_APPEARANCE_STORAGE_KEY]);
    if (current) return current;

    const legacy = readCategoryAppearanceStateFromKeys(LEGACY_CATEGORY_APPEARANCE_STORAGE_KEYS);
    if (legacy) {
        saveCategoryAppearanceState(legacy);
        return legacy;
    }
    return makeCategoryAppearanceState();
}

export function saveCategoryAppearanceState(state) {
    const safeState = normalizeCategoryAppearanceState(state && typeof state === 'object' ? state : makeCategoryAppearanceState());
    localStorage.setItem(CATEGORY_APPEARANCE_STORAGE_KEY, JSON.stringify(safeState));
    LEGACY_CATEGORY_APPEARANCE_STORAGE_KEYS.forEach((key) => {
        try {
            localStorage.removeItem(key);
        } catch (_err) {
            // Ignore legacy cleanup failures.
        }
    });
    return safeState;
}

export function getCategoryAppearanceEntry(catId, state = null) {
    const source = state || loadCategoryAppearanceState();
    const entry = source.categories && source.categories[catId] ? source.categories[catId] : {};
    const saves = Array.isArray(entry.saves) ? entry.saves.filter((save) => save && save.id) : [];
    return {
        color: normalizeCategoryHexColor(entry.color || getDefaultCategoryHex(catId), getDefaultCategoryHex(catId)),
        selected_save_id: entry.selected_save_id || '',
        saves: saves.map((save, index) => ({
            id: String(save.id),
            name: String(save.name || `Saved ${index + 1}`),
            color: normalizeCategoryHexColor(save.color || getDefaultCategoryHex(catId), getDefaultCategoryHex(catId)),
            created_at: save.created_at || null,
        })),
    };
}

/**
 * Get the label for a category id. Returns a title-cased fallback for unknowns.
 */
export function categoryLabel(catId) {
    if (!catId) return '';
    const meta = categoryMeta[catId];
    if (meta) return meta.label;
    return catId.charAt(0).toUpperCase() + catId.slice(1);
}

/**
 * Get the Lucide icon name for a category id.
 */
export function categoryIcon(catId) {
    const meta = categoryMeta[catId];
    return meta ? meta.icon : 'folder';
}

/**
 * Check if a media_type / category string represents a movie.
 * Works with both the DB media_type ("movie") and category id ("movies").
 */
export function isMovieType(type) {
    if (!type) return false;
    const t = type.toLowerCase();
    return t === 'movie' || t === 'movies';
}

/**
 * Check if a media_type / category string represents TV.
 */
export function isTvType(type) {
    if (!type) return false;
    const t = type.toLowerCase();
    return t === 'tv' || t === 'episode';
}

/**
 * Main factory function to initialize a Vue 3 page with base NZBPostarr logic and components.
 * @param {Object} pageOptions - Vue component options (data, methods, computed, etc.)
 * @returns {Object} Vue app instance
 */
export function createVuePage(pageOptions = {}) {
    const root = document.getElementById('app');
    if (!root) return null;

    const pageTitle = root.dataset.pageTitle || '';

    // Load persisted section states
    let savedSections = {};
    try {
        savedSections = JSON.parse(localStorage.getItem('nzbpostarr_expanded_sections') || '{}');
    } catch (e) {
        console.warn('Failed to load section states:', e);
    }

    // Load persisted page-specific data
    // The Queue page keeps the revisioned key its saved state already lives
    // under; other pages append a revision only when the template sets one.
    const baseStorageId = pageTitle.toLowerCase().replace(/\s+/g, '_') || 'common';
    const persistRev = pageTitle === 'Queue' ? QUEUE_PERSIST_REV : (root.dataset.persistRev || '');
    const storageId = persistRev ? `${baseStorageId}_${persistRev}` : baseStorageId;
    const persistKey = `nzbpostarr_persist_${storageId}`;
    let savedPageData = {};
    try {
        savedPageData = JSON.parse(localStorage.getItem(persistKey) || '{}');
    } catch (e) {
        console.warn('Failed to load page-specific persistence:', e);
    }

    // Load persisted theme
    const savedTheme = localStorage.getItem('nzbpostarr_theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
    if (savedTheme === 'dark') {
        document.documentElement.classList.add('dark');
    } else {
        document.documentElement.classList.remove('dark');
    }

    // Merge base data with page-specific data
    const mergedData = function () {
        const baseData = {
            pageTitle,
            lastUpdated: null,
            loading: false,
            toasts: [],
            theme: savedTheme,
            uiRefreshRate: 2000, // Default 2s
            // Connection status
            isConnected: true,
            apiLatency: null,
            connectionStatus: 'Checking...',
            connectionTooltip: 'Server connected',
            // The running version comes from the server (/api/system/update/status, which reads
            // version.py) once loadUpdateStatus returns; there is no second copy here.
            appVersion: '',
            updateStatus: {
                current_version: null,
                latest_version: null,
                update_available: false,
                check_error: null,
                last_checked_at: null,
                is_busy: false,
            },
            sharedUiSettings: {
                stats_page_enabled: true,
            },
            // Common section collapse state
            expandedSections: {
                'new-upload': true, // Default expanded for dashboard
            },
            navItems: [
                { label: 'Dashboard', href: '/', icon: 'layout-dashboard', title: 'Dashboard' },
                { label: 'Queue', href: '/queue', icon: 'list-ordered', title: 'Queue' },
                { label: 'History', href: '/history', icon: 'list', title: 'History' },
                { label: 'Stats', href: '/stats', icon: 'activity', title: 'System Stats', visibilityKey: 'stats_page_enabled' },
                { label: 'Settings', href: '/settings', icon: 'settings', title: 'Settings', iconOnly: true }
            ],
            // Shared confirm dialog (see confirmDialog(); markup lives in base.html)
            confirmState: {
                open: false,
                title: 'Are you sure?',
                message: '',
                detail: '',
                icon: 'help-circle',
                danger: false,
                confirmLabel: 'Confirm',
                cancelLabel: 'Cancel',
                prompt: false,
                value: '',
                placeholder: '',
                inputType: 'text',
            },
            _confirmResolver: null,
            _confirmKeyHandler: null,
            historyIdCounter: 1000, // Shared ID counter
            toastIdCounter: 0,
            _intervals: [], // Managed polling handles for automatic cleanup
            _intervalMap: new Map(), // Track managed polling by function to allow updates
            _healthTimer: null,
            _touchInfotipHandler: null,
            _isUnmounting: false,
            mobileMenuOpen: false, // Mobile hamburger menu state
        };
        const pageData = typeof pageOptions.data === 'function' ? pageOptions.data() : (pageOptions.data || {});

        // Merge with persistence: page defaults -> base defaults -> saved state
        const finalData = { ...baseData, ...pageData };

        // Apply page-specific persistent data
        if (pageOptions.persist && Array.isArray(pageOptions.persist)) {
            pageOptions.persist.forEach(key => {
                if (savedPageData[key] !== undefined) {
                    finalData[key] = savedPageData[key];
                }
            });
        }

        if (finalData.expandedSections) {
            finalData.expandedSections = {
                ...baseData.expandedSections,
                ...pageData.expandedSections,
                ...savedSections
            };
        }

        return finalData;
    };

    // Merge base computed with page-specific computed
    const mergedComputed = {
        lastUpdatedLabel() {
            if (!this.lastUpdated || !(this.lastUpdated instanceof Date)) {
                return typeof this.lastUpdated === 'string' ? this.lastUpdated : '';
            }
            return this.lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        },
        appVersionLabel() {
            const raw = String(this.appVersion || '').trim();
            if (!raw) return '';
            return raw.startsWith('v') ? raw : `v${raw}`;
        },
        latestVersionLabel() {
            const latest = this.updateStatus && this.updateStatus.latest_version;
            if (!latest) return this.appVersionLabel;
            const raw = String(latest).trim();
            return raw.startsWith('v') ? raw : `v${raw}`;
        },
        updateStatusText() {
            if (this.updateStatus && this.updateStatus.check_error) return 'Check Failed';
            return this.updateStatus && this.updateStatus.update_available ? 'Available' : 'Up to Date';
        },
        updateStatusClass() {
            if (this.updateStatus && this.updateStatus.check_error) return 'text-notion-warning';
            return this.updateStatus && this.updateStatus.update_available ? 'text-notion-warning' : 'text-notion-success';
        },
        // Connection dot class - Prettier-safe (used in all pages)
        connectionDotClass() {
            return this.isConnected ? 'bg-notion-success' : 'bg-notion-warning';
        },
        visibleNavItems() {
            return this.navItems.filter(item => this.isNavItemVisible(item));
        },
        ...(pageOptions.computed || {}),
    };

    // Merge base methods with page-specific methods
    const mergedMethods = {
        // ============================================================
        // PRETTIER-SAFE CLASS HELPERS (shared across all pages)
        // ============================================================

        // Nav link class helper
        navLinkClass(item) {
            const base = 'flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm font-medium transition-colors cursor-pointer';
            const active = 'bg-notion-bg-hover text-notion-text-primary';
            const inactive = 'text-notion-text-secondary hover:bg-notion-bg-hover hover:text-notion-text-primary';
            return this.isNavActive(item) ? `${base} ${active}` : `${base} ${inactive}`;
        },

        // Check if a nav item (or any of its children) matches the current page
        isNavActive(item) {
            if (this.pageTitle === item.title) return true;
            if (item.children) return item.children.some(c => this.pageTitle === c.title);
            return false;
        },

        isNavItemVisible(item) {
            if (!item || !item.visibilityKey) return true;
            return this.sharedUiSettings[item.visibilityKey] !== false;
        },

        // Toast icon class helper
        toastIconClass(toast) {
            const map = {
                success: 'text-notion-success',
                error: 'text-notion-error',
                warning: 'text-notion-warning',
                info: 'text-notion-accent',
            };
            return map[toast.type] || 'text-notion-text-secondary';
        },

        // Toast progress bar class helper
        toastProgressClass(toast) {
            const map = {
                success: 'bg-notion-success',
                error: 'bg-notion-error',
                warning: 'bg-notion-warning',
                info: 'bg-notion-accent',
            };
            return map[toast.type] || 'bg-notion-text-secondary';
        },

        // Toast icon name helper
        toastIcon(toast) {
            const map = {
                success: 'check-circle',
                error: 'x-circle',
                warning: 'alert-triangle',
                info: 'info',
            };
            return map[toast.type] || 'info';
        },

        // ============================================================
        // BASE METHODS
        // ============================================================

        // Timestamp update
        markUpdated() {
            this.lastUpdated = new Date();
        },

        // Section toggle (replaces manual DOM manipulation)
        // Universal helper: finds .section-content by matching any data-* attribute value.
        // Works with data-section, data-card, or any future wrapper attribute.
        _findSectionContent(sectionId) {
            for (const el of document.querySelectorAll('.section-content')) {
                const p = el.parentElement;
                if (p && Object.values(p.dataset).includes(sectionId)) return el;
            }
            return null;
        },

        toggleSection(sectionId) {
            const expanding = !this.expandedSections[sectionId];
            this.expandedSections[sectionId] = expanding;

            // Manage overflow for tooltip visibility:
            // - When expanding: set overflow:visible AFTER the animation completes
            //   so tooltips inside sections can escape the container
            // - When collapsing: restore overflow immediately so the animation clips properly
            this.$nextTick(() => {
                const el = this._findSectionContent(sectionId);
                if (el) {
                    const inner = el.querySelector(':scope > div');
                    if (expanding) {
                        const handler = () => {
                            if (this.expandedSections[sectionId]) {
                                el.style.overflow = 'visible';
                                if (inner) inner.style.overflow = 'visible';
                            }
                        };
                        el.addEventListener('transitionend', handler, { once: true });
                    } else {
                        el.style.overflow = '';
                        if (inner) inner.style.overflow = '';
                    }
                }
            });

            // Persist state to localStorage
            try {
                const saved = JSON.parse(localStorage.getItem('nzbpostarr_expanded_sections') || '{}');
                saved[sectionId] = this.expandedSections[sectionId];
                localStorage.setItem('nzbpostarr_expanded_sections', JSON.stringify(saved));
            } catch (e) {
                console.warn('Failed to save section state:', e);
            }
        },

        // Set overflow:visible on all initially-expanded sections (for tooltip escape)
        _initSectionOverflow() {
            this.$nextTick(() => {
                Object.keys(this.expandedSections).forEach(id => {
                    if (this.expandedSections[id]) {
                        const el = this._findSectionContent(id);
                        if (el) {
                            el.style.overflow = 'visible';
                            const inner = el.querySelector(':scope > div');
                            if (inner) inner.style.overflow = 'visible';
                        }
                    }
                });
            });
        },

        isSectionExpanded(sectionId) {
            return !!this.expandedSections[sectionId];
        },

        // Section content class (for CSS-based collapse animation)
        sectionContentClass(sectionId) {
            const base = 'section-content';
            return this.expandedSections[sectionId] ? base : `${base} section-collapsed`;
        },

        // Toast notifications
        showToast(type, title, message, duration = 5000, actions = []) {
            const id = ++this.toastIdCounter;
            this.toasts.push({ id, type, title, message, actions });

            if (duration > 0) {
                setTimeout(() => {
                    this.removeToast(id);
                }, duration);
            }
        },

        removeToast(id) {
            const index = this.toasts.findIndex(t => t.id === id);
            if (index !== -1) {
                this.toasts.splice(index, 1);
            }
        },

        // ============================================================
        // SHARED CONFIRM / PROMPT DIALOG
        // Themed, promise-returning replacements for the native confirm()
        // and prompt(), sharing one dialog shell:
        //     if (!(await this.confirmDialog('Delete 3 jobs?', { danger: true }))) return;
        //     const name = await this.promptDialog('New name?', { value: current });
        // confirmDialog resolves true/false; promptDialog resolves the string, or
        // null when cancelled. Both cancel on Escape or a backdrop click.
        // The markup lives in base.html so every page inherits it.
        // ============================================================
        confirmDialog(message, options = {}) {
            return this._openDialog(message, options, false);
        },

        promptDialog(message, options = {}) {
            return this._openDialog(message, options, true);
        },

        _openDialog(message, options, isPrompt) {
            // Opening a second dialog cancels the first instead of orphaning its promise.
            if (this._confirmResolver) this._settleConfirmDialog(false);

            const opts = options || {};
            this.confirmState = {
                open: true,
                title: opts.title || (isPrompt ? 'Enter a value' : 'Are you sure?'),
                message: message === null || message === undefined ? '' : String(message),
                detail: opts.detail ? String(opts.detail) : '',
                icon: opts.icon || (opts.danger ? 'alert-triangle' : (isPrompt ? 'pencil' : 'help-circle')),
                danger: !!opts.danger,
                confirmLabel: opts.confirmLabel || (isPrompt ? 'Save' : 'Confirm'),
                cancelLabel: opts.cancelLabel || 'Cancel',
                prompt: isPrompt,
                value: opts.value === null || opts.value === undefined ? '' : String(opts.value),
                placeholder: opts.placeholder ? String(opts.placeholder) : '',
                inputType: opts.inputType || 'text',
            };

            // Capture-phase so the dialog wins over page-level key handlers.
            this._confirmKeyHandler = (event) => {
                if (event.key !== 'Escape' && event.key !== 'Enter') return;
                event.preventDefault();
                event.stopPropagation();
                this._settleConfirmDialog(event.key === 'Enter');
            };
            document.addEventListener('keydown', this._confirmKeyHandler, true);

            this.$nextTick(() => {
                const target = isPrompt ? this.$refs.confirmDialogInput : this.$refs.confirmDialogAccept;
                if (!target || typeof target.focus !== 'function') return;
                target.focus();
                if (isPrompt && typeof target.select === 'function') target.select();
            });

            const { promise, resolve } = Promise.withResolvers();
            this._confirmResolver = resolve;
            return promise;
        },

        _settleConfirmDialog(accepted) {
            const resolver = this._confirmResolver;
            const wasPrompt = this.confirmState.prompt;
            const value = this.confirmState.value;
            this._confirmResolver = null;
            if (this._confirmKeyHandler) {
                document.removeEventListener('keydown', this._confirmKeyHandler, true);
                this._confirmKeyHandler = null;
            }
            this.confirmState = { ...this.confirmState, open: false };
            if (!resolver) return;
            if (wasPrompt) {
                resolver(accepted ? value : null);
            } else {
                resolver(accepted);
            }
        },

        acceptConfirmDialog() {
            this._settleConfirmDialog(true);
        },

        cancelConfirmDialog() {
            this._settleConfirmDialog(false);
        },

        // Format utilities available on the instance
        formatBytes: formatUtils.formatBytes,
        formatDuration: formatUtils.formatDuration,
        formatSpeed: formatUtils.formatSpeed,
        formatDate: formatUtils.formatDate,
        formatRelativeDate: formatUtils.formatRelativeDate,
        formatUptime: formatUtils.formatUptime,
        escapeHtml: formatUtils.escapeHtml,

        // Favicon error handler
        handleFaviconError(item) {
            if (item) {
                // Use Vue.set-like approach for reactivity in case it wasn't pre-defined
                item.faviconError = true;
            }
        },

        // Color helper
        getColorClasses(color) {
            if (color && typeof color === 'string' && color.startsWith('#')) {
                return {
                    bg: '',
                    text: '',
                    dot: '',
                    style: {
                        bg: { backgroundColor: `${color}26` }, // ~15% opacity
                        text: { color: color },
                        dot: { backgroundColor: color }
                    }
                };
            }
            // Fallback to map or default gray
            const cfg = colorClassMap[color] || colorClassMap['gray'];
            return {
                ...cfg,
                style: {} // Ensure style exists for safety
            };
        },

        // Status helper
        getStatusConfig(status) {
            return statusConfigFor(status);
        },

        // ============================================================
        // SHARED JOB DISPLAY HELPERS
        // Used by dashboard, queue, and any page showing job state.
        // Pages can override any of these if needed.
        // ============================================================
        jobCategoryName(job) {
            return jobs.jobCategoryName(job);
        },

        jobTargetPathCount(job) {
            return jobs.jobTargetPathCount(job);
        },

        jobDisplayName(job) {
            return jobs.jobDisplayName(job);
        },

        jobTitle(job) {
            if (!job) return 'Job';
            if (job.current_item) {
                return job.current_item;
            }
            const displayName = this.jobDisplayName(job);
            if (displayName) return displayName;
            const catName = categoryLabel(job.category);
            if (job.status === 'queued') return `${catName} - Queued`;
            if (job.status === 'paused') return `${catName} - Paused`;
            return `${catName} Processing`;
        },

        jobDisplaySpeed(job) {
            if (job.status === 'paused') return 'Paused';
            if (job.status !== 'running') return '';
            if (job.current_stage === 'PREPARING') return 'Preparing...';
            if (job.current_stage === 'INITIALIZING') return 'Starting...';
            return job.speed || 'Uploading...';
        },

        jobItemPercent(job) {
            if (job.status === 'completed') return '100%';
            if (job.status === 'failed') return (job.progress_percent || 0) + '%';
            if (typeof job.item_percent === 'number' && job.item_percent > 0) {
                return job.item_percent + '%';
            }
            if (job.progress && job.progress.includes('%')) {
                const match = job.progress.match(/(\d+(?:\.\d+)?)\s*%/);
                if (match) return Math.round(parseFloat(match[1])) + '%';
            }
            if (job.progress_percent > 0) return job.progress_percent + '%';
            // Show 0% if actively in an upload stage (not just initializing)
            if (typeof job.item_percent === 'number' && job.current_stage && job.current_stage !== 'INITIALIZING') {
                return job.item_percent + '%';
            }
            return '0%';
        },

        jobItemInfo(job) {
            if (job.status === 'completed' || job.status === 'failed') {
                return job.progress || (job.status === 'completed' ? 'Finished' : 'Error');
            }
            return job.progress || 'Analyzing items...';
        },

        jobItemCount(job) {
            const total = Number(job.items_total || 0);
            const hasCurrentItem = typeof job.current_item === 'string' && job.current_item.trim() !== '';
            if (job.status === 'completed') return `${total} items finished`;
            if (job.has_explicit_paths && job.status !== 'failed') {
                const remaining = this.jobTargetPathCount(job) + (hasCurrentItem ? 1 : 0);
                return `${remaining} not uploaded`;
            }
            let current = Number(job.items_processed || 0);
            if (total > 0 && hasCurrentItem && job.status !== 'failed') {
                current = Math.min(total, current + 1);
            }
            return `${current} / ${total} items`;
        },

        jobItemSize(job) {
            if (job.status === 'completed') return this.formatBytes(job.total_bytes || 0);
            return job.item_size_str || '0 MB';
        },

        categoryBadgeClass(category) {
            const meta = categoryMeta[category];
            return meta ? meta.badgeClass : 'bg-notion-bg-hover text-notion-text-secondary';
        },

        shortPath(fullPath) {
            if (!fullPath) return '';
            const parts = fullPath.replace(/\\/g, '/').split('/').filter(Boolean);
            if (parts.length <= 2) return fullPath;
            return '.../' + parts.slice(-2).join('/');
        },

        // Theme management
        toggleTheme() {
            this.theme = this.theme === 'dark' ? 'light' : 'dark';

            // Apply to DOM
            document.documentElement.setAttribute('data-theme', this.theme);

            if (this.theme === 'dark') {
                document.documentElement.classList.add('dark');
            } else {
                document.documentElement.classList.remove('dark');
            }

            // Persist
            try {
                localStorage.setItem('nzbpostarr_theme', this.theme);
            } catch (e) {
                console.error('Failed to save theme to localStorage:', e);
            }
        },

        ...apiMethods,

        // API health check
        async checkHealth() {
            if (this._isUnmounting) return;
            const start = performance.now();
            try {
                // Avoid using apiFetch here to prevent recursive connection logic
                const res = await fetch('/api/dashboard/health', {
                    
                    headers: { 'Cache-Control': 'no-cache' }
                });

                const end = performance.now();
                const wasConnected = this.isConnected;
                this.isConnected = res.ok;
                this.connectionStatus = res.ok ? 'Server Online' : 'Server Offline';

                if (res.ok) {
                    this.apiLatency = Math.round(end - start) + 'ms';
                    if (!wasConnected) {
                        this.markUpdated();
                    }
                }

                if (res.ok && !wasConnected) {
                    console.info('Server connection restored via health check');
                }
            } catch (e) {
                this.isConnected = false;
                this.connectionStatus = 'Server Offline';
                this.apiLatency = null;
            } finally {
                if (this._isUnmounting) return;
                // Keep background health checks light so they do not compete with page rendering.
                const nextCheck = document.visibilityState === 'hidden'
                    ? 60000
                    : (this.isConnected ? 30000 : 10000);
                if (this._healthTimer) clearTimeout(this._healthTimer);
                this._healthTimer = setTimeout(() => this.checkHealth(), nextCheck);
            }
        },

        async loadUpdateStatus(force = false) {
            try {
                const suffix = force ? '?force=true' : '';
                const data = await this.apiFetch(`/api/system/update/status${suffix}`);
                if (!data || typeof data !== 'object') return;

                this.updateStatus = {
                    ...this.updateStatus,
                    ...data,
                };
                if (data.current_version) {
                    this.appVersion = data.current_version;
                }
            } catch (e) {
                if (!e.isOffline) {
                    console.debug('Update status check failed:', e.message || e);
                }
            }
        },

        ...pollMethods,

        // Global formatting aliases for templates
        formatSize(val) { return this.$format.formatBytes(val); },

        // Shared search/escape utilities (consolidated from per-page duplicates)
        escapeRegex(str) { return formatUtils.escapeRegex(str); },
        highlightSearch(text) { return formatUtils.highlightSearch(text, this.searchQuery, this.literalSearch); },
        highlightSegments(text) { return formatUtils.highlightSegments(text, this.searchQuery, this.literalSearch); },

        // Clipboard copy using copy-to-clipboard package
        copyToClipboard(text, successMsg = 'Copied to clipboard') {
            const success = copy(text);
            if (success) {
                this.showToast('success', 'Copied', successMsg);
            } else {
                this.showToast('error', 'Error', 'Failed to copy to clipboard');
            }
            return success;
        },

        // Floating UI positioning helper for flyouts/popovers
        async positionFloatingEl(referenceEl, floatingEl, options = {}) {
            const { x, y } = await computePosition(referenceEl, floatingEl, {
                placement: options.placement || 'bottom-start',
                middleware: [
                    offset(options.offset || 4),
                    flip(),
                    shift({ padding: options.padding || 8 }),
                ],
            });
            return { x, y };
        },

        ...(pageOptions.methods || {}),
    };

    // Create Vue app
    const app = createApp({
        data: mergedData,
        computed: mergedComputed,
        methods: mergedMethods,
        watch: pageOptions.watch || {},
        created() {
            if (pageOptions.created) {
                pageOptions.created.call(this);
            }
        },
        mounted() {
            // Check health on start (it will self-perpetuate via setTimeout)
            this.checkHealth();
            this.loadUpdateStatus();
            this.syncSharedUiSettings();
            this.startInterval(this.loadUpdateStatus, 15 * 60 * 1000);

            // Set up page-specific persistence watchers
            if (pageOptions.persist && Array.isArray(pageOptions.persist)) {
                pageOptions.persist.forEach(key => {
                    this.$watch(key, (newVal) => {
                        try {
                            const current = JSON.parse(localStorage.getItem(persistKey) || '{}');
                            current[key] = newVal;
                            localStorage.setItem(persistKey, JSON.stringify(current));
                        } catch (e) {
                            console.warn(`Failed to persist key ${key}:`, e);
                        }
                    }, { deep: true });
                });
            }

            // Touch support for infotip tooltips (hover doesn't work on touch devices)
            if ('ontouchstart' in window || navigator.maxTouchPoints > 0) {
                this._touchInfotipHandler = (e) => {
                    const trigger = e.target.closest('.infotip-trigger');
                    // Remove active class from all other triggers
                    document.querySelectorAll('.infotip-trigger.infotip-active').forEach(el => {
                        if (el !== trigger) el.classList.remove('infotip-active');
                    });
                    // Toggle the clicked trigger
                    if (trigger) {
                        trigger.classList.toggle('infotip-active');
                        e.stopPropagation();
                    }
                };
                document.addEventListener('click', this._touchInfotipHandler, { passive: false });
            }

            // Call page-specific mounted if exists
            if (pageOptions.mounted) {
                pageOptions.mounted.call(this);
            }

            // After page mounts, set overflow:visible on expanded sections
            // so that tooltips inside collapsible sections can escape
            this._initSectionOverflow();
        },
        beforeUnmount() {
            this._isUnmounting = true;
            if (this._healthTimer) {
                clearTimeout(this._healthTimer);
                this._healthTimer = null;
            }
            if (this._touchInfotipHandler) {
                document.removeEventListener('click', this._touchInfotipHandler);
                this._touchInfotipHandler = null;
            }
            // Release any awaiter blocked on an open confirm dialog.
            if (this._confirmResolver) this._settleConfirmDialog(false);
            stopPolling(this);

            // Call page-specific beforeUnmount if exists
            if (pageOptions.beforeUnmount) {
                pageOptions.beforeUnmount.call(this);
            }
        },
    });

    // Register global components
    app.component('lucide-icon', LucideIcon);
    app.component('stats-card', StatsCard);
    app.component('sparkline', Sparkline);
    app.component('modal', Modal);
    Object.entries(pageOptions.components || {}).forEach(([name, component]) => {
        app.component(name, component);
    });

    // Global Properties for templates
    app.config.globalProperties.$format = formatUtils;

    const vm = app.mount(root);
    window.nzbVue = vm;

    return vm;
}
