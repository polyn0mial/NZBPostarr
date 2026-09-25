// Formatting and search-highlight helpers shared by every page (pure; no Vue).
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime.js';
import utc from 'dayjs/plugin/utc.js';
import { filesize } from 'filesize';
import humanizeDuration from 'humanize-duration';
import escapeStringRegexp from 'escape-string-regexp';

dayjs.extend(relativeTime);
dayjs.extend(utc);

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

/** Bytes as "1.50 MB": 1024 steps, 2/1/0 decimals below 10/100/above. */
export function formatBytesCompact(bytes) {
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
}

// ============================================================
//  HISTORY DATES (compact)
// ============================================================

/** A history timestamp as a dayjs; a naive ISO string is the server's UTC. Null when empty. */
export function parseHistoryDate(dateValue) {
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
}

export function formatDateCompact(dateStr) {
    if (!dateStr) return '-';
    const d = parseHistoryDate(dateStr);
    if (!d || !d.isValid()) return '-';
    return d.format('M/D h:mm A');
}

export function formatDateFull(dateStr) {
    if (!dateStr) return '';
    const d = parseHistoryDate(dateStr);
    if (!d || !d.isValid()) return '';
    return d.format('M/D/YYYY h:mm:ss A');
}

export function formatWhenAge(dateStr) {
    if (!dateStr) return '-';
    const d = parseHistoryDate(dateStr);
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
}

export function formatWhenTime(dateStr) {
    if (!dateStr) return '-';
    const d = parseHistoryDate(dateStr);
    if (!d || !d.isValid()) return '-';
    return d.format('h:mm A');
}
