// Table-driven tests for the shared presentation helpers, checked against the outputs of the
// per-page helpers they replaced (copied here verbatim before those were deleted).
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';

// package.json declares commonjs for the build tooling; the shared sources are ES modules.
const hooks = `
export async function load(url, context, nextLoad) {
    if (url.startsWith('file:') && url.includes('/assets/js/') && url.endsWith('.js')) {
        return nextLoad(url, { ...context, format: 'module' });
    }
    return nextLoad(url, context);
}
`;
register(`data:text/javascript,${encodeURIComponent(hooks)}`);

const { statusBadgeClass } = await import('../assets/js/shared/status.js');
const { formatBytesCompact } = await import('../assets/js/shared/format.js');
const { categoryBadgeClass } = await import('../assets/js/shared/categories.js');

// page-base statusConfig as it stood before the move.
const statusConfig = {
    completed: { icon: 'check-circle', color: 'text-notion-success', bg: 'bg-green-500/20' },
    failed: { icon: 'x-circle', color: 'text-notion-error', bg: 'bg-red-500/20' },
    stopped: { icon: 'pause-circle', color: 'text-notion-warning', bg: 'bg-yellow-500/20' },
    cancelled: { icon: 'pause-circle', color: 'text-notion-warning', bg: 'bg-yellow-500/20' },
    running: { icon: 'loader-2', color: 'text-notion-accent', bg: 'bg-blue-500/20' },
    paused: { icon: 'pause-circle', color: 'text-notion-warning', bg: 'bg-yellow-500/20' },
    queued: { icon: 'clock', color: 'text-notion-text-tertiary', bg: 'bg-notion-bg-hover' },
    default: { icon: 'circle', color: 'text-notion-text-tertiary', bg: 'bg-notion-bg-hover' },
};
const getStatusConfig = (s) => statusConfig[s] || { icon: 'circle', color: 'text-notion-text-tertiary', bg: 'bg-gray-500/20' };

const old = {
    pill(status) {
        const config = statusConfig[status] || statusConfig.default;
        return `inline-flex items-center gap-1 px-2 rounded uppercase font-bold tracking-tight py-0.5 text-[10px] ${config.bg} ${config.text || config.color}`;
    },
    finished(status) {
        const map = {
            completed: 'bg-green-500/15 text-notion-success',
            failed: 'bg-red-500/15 text-notion-error',
            stopped: 'bg-yellow-500/15 text-notion-warning',
            cancelled: 'bg-yellow-500/15 text-notion-warning',
        };
        return map[status] || 'bg-notion-bg-hover text-notion-text-tertiary';
    },
    finishedBg: (status) => `${getStatusConfig(status).bg}`,
    tile: (status) => `size-8 rounded-lg flex items-center justify-center ${getStatusConfig(status).bg}`,
};

const STATUSES = ['completed', 'failed', 'stopped', 'cancelled', 'running', 'paused', 'queued', 'unknown', '', undefined];

for (const variant of Object.keys(old)) {
    test(`statusBadgeClass ${variant} matches the old page helper`, () => {
        for (const status of STATUSES) {
            assert.equal(statusBadgeClass(status, variant), old[variant](status), `${variant}/${status}`);
        }
    });
}

test('statusBadgeClass queue matches the old jobQueueStatusBadgeClass branches', () => {
    assert.equal(statusBadgeClass('stopping', 'queue'), 'bg-notion-error/15 text-notion-error');
    assert.equal(statusBadgeClass('paused', 'queue'), 'bg-notion-warning/15 text-notion-warning');
    assert.equal(statusBadgeClass('stopped', 'queue'), 'bg-notion-warning/15 text-notion-warning');
    assert.equal(statusBadgeClass('active', 'queue'), 'bg-notion-accent/10 text-notion-accent');
    assert.equal(statusBadgeClass('queued', 'queue'), 'bg-notion-bg-hover text-notion-text-secondary');
});

test('formatBytesCompact keeps the settings output', () => {
    const cases = [
        [0, '0 B'], [null, '0 B'], [-5, '0 B'], ['x', '0 B'], [Infinity, '0 B'],
        [1, '1.00 B'], [1023, '1023 B'], [1024, '1.00 KB'], [15 * 1024, '15.0 KB'],
        [1536 * 1024, '1.50 MB'], [250 * 1024 ** 3, '250 GB'], [3 * 1024 ** 5, '3072 TB'],
    ];
    for (const [input, expected] of cases) assert.equal(formatBytesCompact(input), expected, String(input));
});

test('categoryBadgeClass keeps the queue palette', () => {
    const cases = {
        tv: 'bg-cyan-500/15 text-cyan-400',
        movies: 'bg-purple-500/15 text-purple-400',
        anime: 'bg-pink-500/15 text-pink-400',
        disc: 'bg-[#E0E0E0] text-[#2A2A2A] border-[#B9B9B9]',
        books: 'bg-emerald-500/15 text-emerald-400',
        ebooks: 'bg-emerald-500/15 text-emerald-400',
        audiobooks: 'bg-orange-500/15 text-orange-400',
        music: 'bg-blue-500/15 text-blue-400',
        apps: 'bg-red-500/15 text-red-400',
        misc: 'bg-orange-500/15 text-orange-400',
        external: 'bg-notion-bg-hover text-notion-text-tertiary',
        '': 'bg-notion-bg-hover text-notion-text-tertiary',
    };
    for (const [id, expected] of Object.entries(cases)) assert.equal(categoryBadgeClass(id), expected, id);
});
