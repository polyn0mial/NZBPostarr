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
