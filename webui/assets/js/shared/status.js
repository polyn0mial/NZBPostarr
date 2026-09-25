// One owner for job-status badge classes. Every variant returns the exact class string its
// old per-page helper built, so the pages render unchanged. Literal class strings only: the
// Tailwind build scans this file.

const STATUS_TINT = {
    completed: { bg: 'bg-green-500/20', color: 'text-notion-success' },
    failed: { bg: 'bg-red-500/20', color: 'text-notion-error' },
    stopped: { bg: 'bg-yellow-500/20', color: 'text-notion-warning' },
    cancelled: { bg: 'bg-yellow-500/20', color: 'text-notion-warning' },
    running: { bg: 'bg-blue-500/20', color: 'text-notion-accent' },
    paused: { bg: 'bg-yellow-500/20', color: 'text-notion-warning' },
    queued: { bg: 'bg-notion-bg-hover', color: 'text-notion-text-tertiary' },
};
const DEFAULT_TINT = { bg: 'bg-notion-bg-hover', color: 'text-notion-text-tertiary' };

const FINISHED_BADGE = {
    completed: 'bg-green-500/15 text-notion-success',
    failed: 'bg-red-500/15 text-notion-error',
    stopped: 'bg-yellow-500/15 text-notion-warning',
    cancelled: 'bg-yellow-500/15 text-notion-warning',
};

// Queue job-list states (derived by the queue page: stopping, paused, stopped, active, queued).
const QUEUE_BADGE = {
    stopping: 'bg-notion-error/15 text-notion-error',
    paused: 'bg-notion-warning/15 text-notion-warning',
    stopped: 'bg-notion-warning/15 text-notion-warning',
    active: 'bg-notion-accent/10 text-notion-accent',
};

/**
 * Badge / icon-tile classes for a job status.
 * variant 'pill'       history jobs table badge
 *         'finished'   queue finished-job badge
 *         'finishedBg' queue finished-job icon tile tint
 *         'tile'       dashboard running-job icon tile
 *         'queue'      queue job-list badge (status is the page's derived queue state)
 */
export function statusBadgeClass(status, variant = 'pill') {
    const tint = STATUS_TINT[status];
    switch (variant) {
        case 'finished':
            return FINISHED_BADGE[status] || 'bg-notion-bg-hover text-notion-text-tertiary';
        case 'finishedBg':
            return tint ? tint.bg : 'bg-gray-500/20';
        case 'tile':
            return `size-8 rounded-lg flex items-center justify-center ${tint ? tint.bg : 'bg-gray-500/20'}`;
        case 'queue':
            return QUEUE_BADGE[status] || 'bg-notion-bg-hover text-notion-text-secondary';
        default: {
            const t = tint || DEFAULT_TINT;
            return `inline-flex items-center gap-1 px-2 rounded uppercase font-bold tracking-tight py-0.5 text-[10px] ${t.bg} ${t.color}`;
        }
    }
}
