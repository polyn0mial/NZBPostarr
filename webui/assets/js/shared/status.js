// Job status presentation: icon and colour classes per job status (pure; no Vue).

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

const UNKNOWN_STATUS = { icon: 'circle', color: 'text-notion-text-tertiary', bg: 'bg-gray-500/20' };

export function getStatusConfig(status) {
    return statusConfig[status] || UNKNOWN_STATUS;
}

// Badge / icon-tile classes. Every variant returns the exact class string its old per-page
// helper built. Literal class strings only: the Tailwind build scans this file.
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
 * variant 'pill'       history jobs table badge
 *         'finished'   queue finished-job badge
 *         'finishedBg' queue finished-job icon tile tint
 *         'tile'       dashboard running-job icon tile
 *         'queue'      queue job-list badge (status is the page's derived queue state)
 */
export function statusBadgeClass(status, variant = 'pill') {
    switch (variant) {
        case 'finished':
            return FINISHED_BADGE[status] || 'bg-notion-bg-hover text-notion-text-tertiary';
        case 'finishedBg':
            return getStatusConfig(status).bg;
        case 'tile':
            return `size-8 rounded-lg flex items-center justify-center ${getStatusConfig(status).bg}`;
        case 'queue':
            return QUEUE_BADGE[status] || 'bg-notion-bg-hover text-notion-text-secondary';
        default: {
            const config = statusConfig[status] || statusConfig.default;
            return `inline-flex items-center gap-1 px-2 rounded uppercase font-bold tracking-tight py-0.5 text-[10px] ${config.bg} ${config.color}`;
        }
    }
}
