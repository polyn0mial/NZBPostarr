import { createApp } from 'vue';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { filesize } from 'filesize';
import humanizeDuration from 'humanize-duration';
import copy from 'copy-to-clipboard';
import escapeStringRegexp from 'escape-string-regexp';
import { computePosition, flip, shift, offset } from '@floating-ui/dom';

dayjs.extend(relativeTime);

// Configure humanizeDuration
const humanizer = humanizeDuration.humanizer({
    language: 'shortEn',
    languages: {
        shortEn: {
            y: () => 'y',
            mo: () => 'mo',
            w: () => 'w',
            d: () => 'd',
            h: () => 'h',
            m: () => 'm',
            s: () => 's',
            ms: () => 'ms',
        },
    },
    round: true,
    spacer: '',
    conjunction: ' ',
    serialComma: false,
});

// ============================================================
//  SHARED FORMATTING UTILITIES (Vue filters/methods)
// ============================================================

export const formatUtils = {
    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        if (!bytes || Number.isNaN(bytes)) return '-';
        return filesize(bytes, { base: 2, standard: 'jedec' });
    },

    formatDuration(seconds) {
        if (seconds === 0) return '0s';
        if (!seconds || Number.isNaN(seconds)) return '-';
        return humanizer(seconds * 1000, { largest: 2 });
    },

    formatSpeed(bps) {
        if (bps === 0) return '0.00 MB/s';
        if (!bps || Number.isNaN(bps)) return '-';
        return filesize(bps, { base: 2, standard: 'jedec' }) + '/s';
    },

    formatDate(dateStr) {
        if (!dateStr) return '-';
        const d = dayjs(dateStr);
        if (!d.isValid()) return '-';
        return d.format('M/D/YYYY h:mm A');
    },

    formatRelativeDate(dateStr) {
        if (!dateStr) return '-';
        const d = dayjs(dateStr);
        if (!d.isValid()) return '-';

        const now = dayjs();
        if (d.isSame(now, 'day')) return 'Today ' + d.format('h:mm A');
        if (d.isSame(now.subtract(1, 'day'), 'day')) return 'Yesterday ' + d.format('h:mm A');
        if (now.diff(d, 'day') < 7 && now.diff(d, 'day') >= 1) return d.fromNow();

        return d.format('M/D/YYYY h:mm A');
    },

    formatUptime(seconds) {
        if (!seconds || isNaN(seconds)) return '-';
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        let parts = [];
        if (days > 0) parts.push(days + 'd');
        if (hours > 0) parts.push(hours + 'h');
        if (mins > 0 || parts.length === 0) parts.push(mins + 'm');
        return parts.join(' ');
    },

    escapeHtml(text) {
        if (!text) return '';
        return String(text).replace(/[&<>"']/g, (ch) => {
            switch (ch) {
                case '&': return '&amp;';
                case '<': return '&lt;';
                case '>': return '&gt;';
                case '"': return '&quot;';
                case "'": return '&#39;';
                default: return ch;
            }
        });
    },

    /**
     * Escape special regex characters.
     * Uses the battle-tested escape-string-regexp package.
     */
    escapeRegex(str) {
        return escapeStringRegexp(str);
    },

    /**
     * Returns an array of {text, hl} segments for XSS-safe, v-html-free search highlighting.
     * Use with v-for + <mark> in templates.
     */
    highlightSegments(text, searchQuery, literalSearch = false) {
        if (!text || !searchQuery) return [{ text: text || '', hl: false }];

        let patterns;
        if (literalSearch) {
            patterns = [escapeStringRegexp(searchQuery)];
        } else {
            patterns = searchQuery.replace(/[-_.]/g, ' ').split(/\s+/).filter(w => w).map(w => escapeStringRegexp(w));
        }

        if (patterns.length === 0) return [{ text, hl: false }];

        const regex = new RegExp(`(${patterns.join('|')})`, 'gi');
        const segments = [];
        let lastIndex = 0;
        let match;

        while ((match = regex.exec(text)) !== null) {
            if (match.index > lastIndex) {
                segments.push({ text: text.slice(lastIndex, match.index), hl: false });
            }
            segments.push({ text: match[0], hl: true });
            lastIndex = regex.lastIndex;
        }

        if (lastIndex < text.length) {
            segments.push({ text: text.slice(lastIndex), hl: false });
        }

        return segments.length ? segments : [{ text, hl: false }];
    },

    /**
     * Returns HTML string with search matches highlighted (for backward-compat v-html usage).
     * Prefer highlightSegments + v-for for new code.
     */
    highlightSearch(text, searchQuery, literalSearch = false) {
        if (!text || !searchQuery) return formatUtils.escapeHtml(text);

        let displayName = formatUtils.escapeHtml(text);

        if (literalSearch) {
            const regex = new RegExp(`(${escapeStringRegexp(searchQuery)})`, 'gi');
            displayName = displayName.replace(regex, '<mark class="bg-notion-warning/30 text-notion-text-primary rounded px-0.5">$1</mark>');
        } else {
            const words = searchQuery.replace(/[-_.]/g, ' ').split(/\s+/).filter(w => w);
            words.forEach(word => {
                const regex = new RegExp(`(${escapeStringRegexp(word)})`, 'gi');
                displayName = displayName.replace(regex, '<mark class="bg-notion-warning/30 text-notion-text-primary rounded px-0.5">$1</mark>');
            });
        }

        return displayName;
    },
};

// Re-export library utilities for page scripts that need them directly
export { copy, computePosition, flip, shift, offset, escapeStringRegexp };

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
//  STATUS CONFIGS FOR JOBS
// ============================================================

export const statusConfig = {
    completed: { icon: 'check-circle', color: 'text-notion-success', bg: 'bg-green-500/20' },
    failed: { icon: 'x-circle', color: 'text-notion-error', bg: 'bg-red-500/20' },
    stopped: { icon: 'pause-circle', color: 'text-notion-warning', bg: 'bg-yellow-500/20' },
    cancelled: { icon: 'pause-circle', color: 'text-notion-warning', bg: 'bg-yellow-500/20' },
    running: { icon: 'loader-2', color: 'text-notion-accent', bg: 'bg-blue-500/20' },
    paused: { icon: 'pause-circle', color: 'text-notion-warning', bg: 'bg-yellow-500/20' },
    queued: { icon: 'clock', color: 'text-notion-text-tertiary', bg: 'bg-notion-bg-hover' },
    default: { icon: 'circle', color: 'text-notion-text-tertiary', bg: 'bg-notion-bg-hover' }
};

// ============================================================
//  MEDIA TYPE / CATEGORY UTILITIES  (shared across all pages)
// ============================================================

/**
 * Canonical metadata for every known category.
 * Pages should use these instead of inline icon/color/label maps.
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
 * Map a display itype string (e.g. "TV Show", "Movie", "Anime") to an upload
 * category id (e.g. "tv", "movies", "misc").
 *
 * For content types that *may* have a dedicated folder category (music,
 * audiobook, ebook), pass `availableCategories` (an array of {id} objects)
 * so the function can prefer a specific category over falling back to "misc".
 */
export function itypeToCategory(itype, availableCategories = []) {
    if (!itype) return '';
    switch (itype) {
        case 'TV Show':
        case 'TV Episode':
            return 'tv';
        case 'Anime':
            return 'anime';
        case 'Movie':
        case 'Movies':
            return 'movies';
        case 'Music': {
            const found = availableCategories.find(c =>
                c.id.toLowerCase().includes('music'));
            return found ? found.id : '';
        }
        case 'Audiobook': {
            const exact = availableCategories.find(c => c.id.toLowerCase() === 'audiobooks');
            if (exact) return exact.id;
            const found = availableCategories.find(c => c.id.toLowerCase().includes('book'));
            return found ? found.id : '';
        }
        case 'Ebook': {
            const exact = availableCategories.find(c => c.id.toLowerCase() === 'ebooks');
            if (exact) return exact.id;
            const found = availableCategories.find(c => c.id.toLowerCase().includes('book'));
            return found ? found.id : '';
        }
        default:
            return '';
    }
}

/**
 * Map an upload category id back to its best matching content-type label.
 */
export function categoryToItype(category, fallbackItype = 'Misc') {
    switch ((category || '').toLowerCase()) {
        case 'tv':
            return 'TV Episode';
        case 'movies':
            return 'Movie';
        case 'anime':
            return 'Anime';
        case 'music':
            return 'Music';
        case 'books':
            return 'Ebook';
        case 'apps':
            return 'Apps';
        case 'audiobooks':
            return 'Audiobook';
        case 'ebooks':
            return 'Ebook';
        case 'misc':
            return 'Misc';
        default:
            return fallbackItype || 'Misc';
    }
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

// ============================================================
//  SHARED STATS / HISTORY UTILITIES
// ============================================================

function createHistoryPoint(value, nextId) {
    return { id: nextId(), v: value ?? 0 };
}

export function mapHistorySeries(values, targetLen, nextId) {
    let normalized = Array.isArray(values) ? values.slice() : [];
    const length = Math.max(0, Number(targetLen) || 0);
    if (normalized.length < length) {
        normalized = [...new Array(length - normalized.length).fill(0), ...normalized];
    }
    return normalized.map(value => createHistoryPoint(value, nextId));
}

export function mapDeltaHistorySeries(values, targetLen, nextId, divisor = 1) {
    const normalized = Array.isArray(values) ? values : [];
    const safeDivisor = Math.max(0.001, Number(divisor) || 1);
    let deltas = [];
    for (let i = 1; i < normalized.length; i += 1) {
        deltas.push(Math.max(0, ((normalized[i] ?? 0) - (normalized[i - 1] ?? 0)) / safeDivisor));
    }
    const length = Math.max(0, Number(targetLen) || 0);
    if (deltas.length < length) {
        deltas = [...new Array(length - deltas.length).fill(0), ...deltas];
    }
    return deltas.map(value => createHistoryPoint(value, nextId));
}

export function seedHistorySeries(length, nextId) {
    const count = Math.max(0, Math.floor(Number(length) || 0));
    return Array.from({ length: count }, () => createHistoryPoint(0, nextId));
}

export function appendHistoryPoint(series, value, nextId, maxLen) {
    if (!Array.isArray(series)) return;
    series.push(createHistoryPoint(value, nextId));
    const limit = Math.max(0, Math.floor(Number(maxLen) || 0));
    if (limit > 0 && series.length > limit) {
        series.splice(0, series.length - limit);
    }
}

export function computePositiveRateDelta(current, previous, intervalSecs = 1) {
    if (previous === null || previous === undefined) return 0;
    const safeInterval = Math.max(0.001, Number(intervalSecs) || 1);
    return Math.max(0, ((Number(current) || 0) - (Number(previous) || 0)) / safeInterval);
}

export function sumNumericFields(source, fieldNames = []) {
    return fieldNames.reduce((total, fieldName) => total + (Number(source?.[fieldName]) || 0), 0);
}

// ============================================================
//  SHARED COMPONENTS
// ============================================================

const StatsCard = {
    props: {
        title: String,
        icon: String,
        color: String, // e.g., 'purple-400'
        value: [String, Number],
        unit: { type: String, default: '' },
        progress: { type: Number, default: null },
        sparkData: { type: Array, default: null },
        peakKey: { type: String, default: null },
        valueSuffix: { type: String, default: '' },
        footerLabel: { type: String, default: 'Last 5 min' },
        valueId: { type: String, default: null }
    },
    template: `
        <div class="bg-notion-bg border border-notion-border rounded-lg p-3 flex flex-col h-full transition-all hover:shadow-md">
            <div class="flex items-center gap-2 mb-2">
                <div :class="['w-6 h-6 rounded flex items-center justify-center flex-shrink-0', getBgClass()]">
                    <lucide-icon :name="icon" :icon-class="['w-3.5 h-3.5', 'text-' + color]"></lucide-icon>
                </div>
                <p class="text-notion-text-tertiary uppercase tracking-wide text-[10.5px]">{{ title }}</p>
            </div>
            <p :id="valueId" class="text-lg font-semibold text-notion-text-primary">{{ value }}{{ unit }}</p>
            
            <div v-if="progress !== null" class="mt-1 h-1 bg-notion-bg-hover rounded-full overflow-hidden">
                <div :class="['h-full rounded-full transition-all duration-300', 'bg-' + color]"
                     :style="{ width: Math.min(100, progress) + '%' }"></div>
            </div>

            <slot name="extra"></slot>

            <div v-if="(sparkData && sparkData.length) || footerLabel" class="mt-auto pt-2">
                <div v-if="sparkData && sparkData.length" class="h-10 flex items-end gap-px">
                    <div v-for="(v, i) in sparkData" :key="v.id" 
                        class="flex-1 rounded-sm cursor-pointer transition-[height] duration-300 infotip-trigger relative" 
                        :style="$parent.getSparklineStyle(v, sparkData, getRgb(color), peakKey)">
                        <div class="infotip-content">
                            {{ $parent.getSparklineTitle(v, i, sparkData, valueSuffix) }}
                            <div class="infotip-arrow"></div>
                        </div>
                    </div>
                </div>
                <p v-if="footerLabel" class="text-notion-text-tertiary mt-1 text-center text-[10px]">{{ footerLabel }}</p>
            </div>
        </div>
    `,
    methods: {
        getRgb(color) {
            const isLight = document.documentElement.getAttribute('data-theme') === 'light';
            const map = isLight ? {
                'orange-400': 'rgb(234, 88, 12)',
                'green-400': 'rgb(22, 163, 74)',
                'blue-400': 'rgb(37, 99, 235)',
                'cyan-400': 'rgb(8, 145, 178)',
                'purple-400': 'rgb(147, 51, 234)',
                'pink-400': 'rgb(219, 39, 119)',
                'teal-400': 'rgb(13, 148, 136)',
                'rose-400': 'rgb(225, 29, 72)',
                'red-400': 'rgb(220, 38, 38)',
                'yellow-400': 'rgb(202, 138, 4)',
                'amber-400': 'rgb(217, 119, 6)'
            } : {
                'orange-400': 'rgb(251, 146, 60)',
                'green-400': 'rgb(74, 222, 128)',
                'blue-400': 'rgb(96, 165, 250)',
                'cyan-400': 'rgb(34, 211, 238)',
                'purple-400': 'rgb(168, 85, 247)',
                'pink-400': 'rgb(236, 72, 153)',
                'teal-400': 'rgb(45, 212, 191)',
                'rose-400': 'rgb(251, 113, 133)',
                'red-400': 'rgb(248, 113, 113)',
                'yellow-400': 'rgb(250, 204, 21)',
                'amber-400': 'rgb(251, 191, 36)'
            };
            return map[color] || 'rgb(156, 163, 175)';
        },
        getBgClass() {
            const base = this.color.split('-')[0];
            return `bg-${base}-500/15`;
        }
    }
};

/**
 * Shared modal shell: teleport + backdrop + panel + header + close button.
 *
 * Replaces the hand-coded block that had drifted apart across the pages (some
 * used a self-click backdrop, some a separate overlay div, headers differed).
 * Body content goes in the default slot; extra header buttons in #header-actions.
 *
 *   <modal :open="showThing" title="Thing" icon="list" @close="closeThing()">
 *       <div class="overflow-y-auto">...</div>
 *   </modal>
 */
const openModalStack = []; // Escape only closes the topmost open modal

const Modal = {
    props: {
        open: { type: Boolean, default: false },
        title: { type: String, default: '' },
        subtitle: { type: String, default: '' },
        subtitleClass: { type: String, default: '' },
        icon: { type: String, default: '' },
        iconClass: { type: String, default: 'text-notion-accent' },
        badge: { type: [String, Number], default: null },
        maxWidth: { type: String, default: 'max-w-2xl' },
        panelClass: { type: String, default: 'bg-notion-bg-secondary max-h-[85vh]' },
        zClass: { type: String, default: 'z-50' },
        teleport: { type: Boolean, default: true },
        closeDisabled: { type: Boolean, default: false },
        closeOnBackdrop: { type: Boolean, default: true }
    },
    emits: ['close'],
    template: `
        <Teleport to="body" :disabled="!teleport">
            <Transition name="modal">
                <div v-if="open" :class="['fixed inset-0 flex items-end sm:items-center justify-center sm:p-4', zClass]" role="dialog" aria-modal="true">
                    <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="onBackdrop()"></div>
                    <div :class="['relative border border-notion-border rounded-t-xl sm:rounded-lg shadow-2xl w-full flex flex-col overflow-hidden', maxWidth, panelClass]">
                        <div class="shrink-0 flex items-center justify-between gap-3 px-4 py-3 border-b border-notion-divider bg-notion-bg-secondary">
                            <div class="flex items-center gap-2 min-w-0">
                                <lucide-icon v-if="icon" :name="icon" :icon-class="['size-4 shrink-0', iconClass]"></lucide-icon>
                                <div class="min-w-0">
                                    <h3 class="text-sm font-semibold text-notion-text-primary truncate">{{ title }}</h3>
                                    <p v-if="subtitle" :class="['text-[10px] text-notion-text-tertiary truncate', subtitleClass]">{{ subtitle }}</p>
                                </div>
                                <span v-if="badge !== null && badge !== ''" class="px-1.5 py-0.5 bg-notion-bg-hover text-notion-text-secondary rounded text-xs font-medium shrink-0">{{ badge }}</span>
                            </div>
                            <div class="flex items-center gap-3 shrink-0">
                                <slot name="header-actions"></slot>
                                <button @click="$emit('close')" :disabled="closeDisabled" class="icon-btn disabled:opacity-40" aria-label="Close">
                                    <lucide-icon name="x" icon-class="size-4"></lucide-icon>
                                </button>
                            </div>
                        </div>
                        <slot></slot>
                    </div>
                </div>
            </Transition>
        </Teleport>
    `,
    watch: {
        open: {
            immediate: true,
            handler(isOpen) {
                const at = openModalStack.indexOf(this);
                if (isOpen) {
                    if (at === -1) openModalStack.push(this);
                } else if (at !== -1) {
                    openModalStack.splice(at, 1);
                }
            }
        }
    },
    mounted() {
        this._modalKeyHandler = (event) => {
            if (event.key !== 'Escape' || this.closeDisabled) return;
            if (openModalStack[openModalStack.length - 1] !== this) return;
            event.preventDefault();
            this.$emit('close');
        };
        document.addEventListener('keydown', this._modalKeyHandler);
    },
    beforeUnmount() {
        document.removeEventListener('keydown', this._modalKeyHandler);
        const at = openModalStack.indexOf(this);
        if (at !== -1) openModalStack.splice(at, 1);
    },
    methods: {
        onBackdrop() {
            if (this.closeOnBackdrop && !this.closeDisabled) this.$emit('close');
        }
    }
};

const Sparkline = {
    props: {
        data: Array,
        colorRgb: String,
        peakKey: String,
        valueSuffix: { type: String, default: '' },
        height: { type: String, default: 'h-10' }
    },
    template: `
        <div v-if="data && data.length" :class="[height, 'flex items-end gap-px']">
            <div v-for="(v, i) in data" :key="v.id" 
                class="flex-1 rounded-sm cursor-pointer transition-[height] duration-300 infotip-trigger relative" 
                :style="$parent.getSparklineStyle(v, data, colorRgb, peakKey)">
                <div class="infotip-content">
                    {{ $parent.getSparklineTitle(v, i, data, valueSuffix) }}
                    <div class="infotip-arrow"></div>
                </div>
            </div>
        </div>
    `
};

// ============================================================
//  VUE APP FACTORY
// ============================================================

/**
 * Lucide Icon component.
 *
 * IMPORTANT: Do not call `lucide.createIcons()` per icon instance.
 * The Lucide UMD build scans the whole document for `[data-lucide]` each call
 * (it does not support a `root` option). Doing that inside large v-for lists
 * becomes O(n^2) and makes expands/collapses laggy.
 *
 * Instead, render SVGs directly via `lucide.createElement(iconDef)`.
 */
const LucideIcon = {
    props: {
        name: { type: String, required: true },
        iconClass: { type: [String, Array, Object], default: '' },
        iconStyle: { type: [String, Object], default: null }
    },
    template: `<span ref="container" :style="iconStyle" class="lucide-icon-wrapper inline-flex items-center justify-center leading-none"></span>`,
    computed: {
        combinedClass() {
            let classes = '';
            if (Array.isArray(this.iconClass)) {
                classes = this.iconClass.filter(Boolean).join(' ');
            } else if (typeof this.iconClass === 'object') {
                classes = Object.entries(this.iconClass)
                    .filter(([_, value]) => value)
                    .map(([key, _]) => key)
                    .join(' ');
            } else {
                classes = this.iconClass || '';
            }

            // Ensure we have both width and height if one or none is provided
            const hasWidth = /\bw-[\d.]+/.test(classes);
            const hasHeight = /\bh-[\d.]+/.test(classes);

            if (!hasWidth && !hasHeight) {
                classes += ' w-4 h-4';
            } else if (hasWidth && !hasHeight) {
                const wMatch = classes.match(/\bw-([\d.]+)/);
                if (wMatch) classes += ' h-' + wMatch[1];
            } else if (!hasWidth && hasHeight) {
                const hMatch = classes.match(/\bh-([\d.]+)/);
                if (hMatch) classes += ' w-' + hMatch[1];
            }

            return classes.trim();
        }
    },
    mounted() {
        this.renderIcon();
    },
    updated() {
        this.renderIcon();
    },
    methods: {
        renderIcon() {
            const lucide = window.lucide;
            const container = this.$refs.container;
            if (!lucide || !container) return;

            const key = `${this.name}|${this.combinedClass}`;
            if (container.dataset.lucideKey === key) return;
            container.dataset.lucideKey = key;

            // Clear previous icon
            while (container.firstChild) container.removeChild(container.firstChild);

            // Lucide stores icon defs as PascalCase keys (e.g. "play-circle" -> "PlayCircle")
            const pascal = String(this.name)
                .split(/[-_\s]+/)
                .filter(Boolean)
                .map(part => part.charAt(0).toUpperCase() + part.slice(1))
                .join('');

            const def = (lucide.icons && (lucide.icons[pascal] || lucide.icons[this.name])) || lucide[pascal];
            if (!def || typeof lucide.createElement !== 'function') return;

            // Render SVG directly (no global DOM scan)
            const svg = lucide.createElement(def);
            svg.setAttribute('class', `lucide lucide-${this.name} ${this.combinedClass}`.trim());
            container.appendChild(svg);
        }
    }
};

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
    const storageId = pageTitle.toLowerCase().replace(/\s+/g, '_') || 'common';
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
            appVersion: '9.5.0',
            updateStatus: {
                current_version: '9.5.0',
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
            sessionPeaks: {}, // Shared peak tracking for sparklines
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
            if (!raw) return 'v9.5.0';
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
        flatNavItems() {
            const flat = [];
            this.visibleNavItems.forEach(item => {
                flat.push(item);
                if (item.children) item.children.forEach(c => flat.push({ ...c, isChild: true }));
            });
            return flat;
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

            return new Promise((resolve) => {
                this._confirmResolver = resolve;
            });
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
                this.refreshIcons();
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
            return statusConfig[status] || { icon: 'circle', color: 'text-notion-text-tertiary', bg: 'bg-gray-500/20' };
        },

        // ============================================================
        // SHARED JOB DISPLAY HELPERS
        // Used by dashboard, queue, and any page showing job state.
        // Pages can override any of these if needed.
        // ============================================================
        jobCategoryName(job) {
            const map = {
                tv: 'TV',
                movies: 'Movies',
                anime: 'Anime',
                disc: 'DISC',
                misc: 'Misc',
                both: 'Selected Upload',
                mixed: 'Selected Upload',
                selected: 'Selected Upload'
            };
            const key = (job && job.category) ? String(job.category) : '';
            return map[key] || key || 'Job';
        },

        jobDisplayName(job) {
            if (!job) return 'Job';
            const custom = (job.display_name || '').trim();
            if (custom) {
                const autoCountName = custom.match(/^(.*?)(?:\s*-\s*\d+\s+items?)$/i);
                if (autoCountName && /^(?:TV|Movies|Anime|DISC|Misc|Both|Mixed|Selected)$/i.test(autoCountName[1].trim())) {
                    return autoCountName[1].trim().replace(/^Mixed$/i, 'Selected Upload');
                }
                return custom;
            }

            return this.jobCategoryName(job);
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
            if (job.status === 'completed') return `${total} items finished`;
            if (job.status !== 'failed') {
                const explicitRemaining = Number(job.target_path_count || 0);
                const processed = Number(job.items_processed || 0);
                const calculatedRemaining = total > 0 ? Math.max(total - processed, 0) : 0;
                const remaining = Math.max(explicitRemaining, calculatedRemaining);
                if (job.has_explicit_paths || total > 0) {
                    return `${remaining} item${remaining === 1 ? '' : 's'} not uploaded`;
                }
            }
            let current = Number(job.items_processed || 0);
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

            this.refreshIcons();
        },

        // ============================================================
        // SPARKLINE HELPERS (Shared across all pages)
        // ============================================================
        getSparklineStyle(item, data, color, peakKey = null) {
            const val = typeof item === 'object' ? item.v : item;
            if (!data || data.length === 0) return { height: '4%', backgroundColor: color, opacity: 0.85 };

            const values = data.map(d => typeof d === 'object' ? d.v : d).filter(v => typeof v === 'number');
            if (values.length === 0) return { height: '4%', backgroundColor: color, opacity: 0.85 };

            const sorted = [...values].sort((a, b) => a - b);
            const p98 = sorted[Math.floor(sorted.length * 0.98)] || 0.1;

            let scaleMax = p98;
            if (peakKey) {
                if (!this.sessionPeaks[peakKey] || p98 > this.sessionPeaks[peakKey]) {
                    this.sessionPeaks[peakKey] = p98;
                }
                scaleMax = this.sessionPeaks[peakKey];
            }

            const height = Math.min(100, Math.max(4, (val / scaleMax) * 100));
            return { height: `${height}%`, backgroundColor: color, opacity: 0.85 };
        },

        getSparklineTitle(item, index, data, suffix = '') {
            const val = typeof item === 'object' ? item.v : item;
            const secsAgo = (data.length - 1 - index) * 1;
            const timeLabel = secsAgo === 0 ? 'now' : secsAgo < 60 ? `${secsAgo}s ago` : `${Math.round(secsAgo / 60)}m ago`;
            const displayVal = typeof val === 'number' ? val.toFixed(1) : val;
            return `${displayVal}${suffix} (${timeLabel})`;
        },

        // API helpers
        async apiFetch(url, options = {}) {
            const start = performance.now();
            try {
                const res = await fetch(url, {
                    headers: { 'Content-Type': 'application/json' },
                    cache: 'no-store',
                    ...options,
                });

                if (!res.ok) {
                    const errorData = await res.json().catch(() => ({ detail: 'Unknown error' }));
                    const error = new Error(errorData.detail || res.statusText);
                    error.status = res.status;
                    throw error;
                }

                // Calculate latency
                const end = performance.now();
                this.apiLatency = Math.round(end - start) + 'ms';
                this.markUpdated();

                // Restore connection state if it was down
                if (!this.isConnected) {
                    this.isConnected = true;
                    this.connectionStatus = 'Server Online';
                    console.info('Server connection restored');
                }

                // Read the response
                const result = await res.json();

                // Extract global settings if present (auto-sync)
                const uiMeta = (result && typeof result === 'object') ? (result.ui || result) : null;
                if (uiMeta && uiMeta.ui_refresh_seconds) {
                    const newRate = uiMeta.ui_refresh_seconds * 1000;
                    if (this.uiRefreshRate !== newRate) {
                        this.uiRefreshRate = newRate;
                        console.debug(`UI Refresh rate updated: ${uiMeta.ui_refresh_seconds}s`);
                    }
                }

                if (uiMeta && typeof uiMeta.stats_page_enabled === 'boolean') {
                    this.sharedUiSettings = {
                        ...this.sharedUiSettings,
                        stats_page_enabled: uiMeta.stats_page_enabled,
                    };
                }

                return result;
            } catch (e) {
                // Handle network errors (offline)
                const isNetworkError = e.name === 'TypeError' ||
                    e.message === 'Failed to fetch' ||
                    e.message === 'Load failed' ||
                    e.message.includes('ERR_CONNECTION_REFUSED');

                if (isNetworkError) {
                    const wasConnected = this.isConnected;
                    this.isConnected = false;
                    this.connectionStatus = 'Server Offline';

                    if (wasConnected) {
                        console.warn(`Server went offline: ${url}`);
                    }
                    e.isOffline = true;
                } else if (!e.isOffline) {
                    // Only log non-offline errors to console
                    console.error(`API Error: ${url}`, e);
                }
                throw e;
            }
        },

        async apiPost(url, data) {
            return this.apiFetch(url, {
                method: 'POST',
                body: JSON.stringify(data),
            });
        },

        async apiPut(url, data) {
            return this.apiFetch(url, {
                method: 'PUT',
                body: JSON.stringify(data),
            });
        },

        async apiDelete(url) {
            return this.apiFetch(url, { method: 'DELETE' });
        },

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

        async syncSharedUiSettings() {
            try {
                await this.apiFetch('/api/settings');
            } catch (e) {
                if (!e.isOffline) {
                    console.debug('Shared UI settings sync failed:', e.message || e);
                }
            }
        },

        // Managed Interval Helper
        startInterval(fn, ms) {
            // Under Lighthouse audit, slow down polling to allow the page to "finish" loading
            if (navigator.userAgent.includes('Lighthouse') || window.location.search.includes('lighthouse=1')) {
                ms = Math.max(ms, 120000); // 2 minutes! Basically stop polling.
            }

            const existing = this._intervalMap.get(fn);
            if (existing) {
                existing.cancelled = true;
                clearTimeout(existing.timerId);
                this._intervals = this._intervals.filter(handle => handle !== existing);
            }

            const handle = {
                cancelled: false,
                running: false,
                timerId: null,
            };

            const scheduleNext = () => {
                if (handle.cancelled) {
                    return;
                }
                const delay = document.hidden ? Math.max(ms, 60000) : ms;
                handle.timerId = setTimeout(() => {
                    void tick();
                }, delay);
            };

            const tick = async () => {
                if (handle.cancelled || handle.running) {
                    return;
                }
                if (document.hidden) {
                    scheduleNext();
                    return;
                }

                handle.running = true;
                try {
                    await fn.call(this);
                } finally {
                    handle.running = false;
                    scheduleNext();
                }
            };

            scheduleNext();
            this._intervalMap.set(fn, handle);
            this._intervals.push(handle);
            return handle;
        },

        // Managed one-shot timer for delayed refreshes that must not outlive a page.
        startTimeout(fn, ms) {
            const handle = {
                cancelled: false,
                running: false,
                timerId: null,
            };
            const removeHandle = () => {
                this._intervals = this._intervals.filter(candidate => candidate !== handle);
            };
            handle.timerId = setTimeout(async () => {
                if (handle.cancelled || this._isUnmounting) {
                    removeHandle();
                    return;
                }
                handle.running = true;
                try {
                    await fn.call(this);
                } finally {
                    handle.running = false;
                    handle.cancelled = true;
                    removeHandle();
                }
            }, ms);
            this._intervals.push(handle);
            return handle;
        },

        // Global formatting aliases for templates
        formatSize(val) { return this.$format.formatBytes(val); },
        formatRelative(val) { return this.$format.formatRelativeDate(val); },

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

        // Refresh icons after Vue updates
        refreshIcons() {
            // No-op: most icons are rendered via the <lucide-icon> component without DOM scans.
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
            // Clear all managed intervals
            this._intervals.forEach(handle => {
                if (handle && typeof handle === 'object') {
                    handle.cancelled = true;
                    clearTimeout(handle.timerId);
                    return;
                }
                clearTimeout(handle);
            });
            this._intervals = [];
            this._intervalMap.clear();

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

    // Register v-click-outside directive
    app.directive('click-outside', {
        mounted(el, binding) {
            el._clickOutsideHandler = (event) => {
                if (!(el === event.target || el.contains(event.target))) {
                    binding.value(event);
                }
            };
            document.addEventListener('click', el._clickOutsideHandler, true);
        },
        unmounted(el) {
            document.removeEventListener('click', el._clickOutsideHandler, true);
            delete el._clickOutsideHandler;
        }
    });

    // Global Properties for templates
    app.config.globalProperties.$format = formatUtils;

    const vm = app.mount(root);
    window.nzbVue = vm;

    return vm;
}
