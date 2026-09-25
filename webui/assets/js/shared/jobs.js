// Job naming for the Dashboard and Queue job rows (pure; no Vue). The single browser owner of
// the active-job wording: "TV - 3 items", "DISC", "Both".

const JOB_CATEGORY_NAME = {
    tv: 'TV',
    movies: 'Movies',
    anime: 'Anime',
    disc: 'DISC',
    misc: 'Misc',
    both: 'Both',
    mixed: 'Mixed',
    selected: 'Selected',
};

export function jobCategoryName(job) {
    const key = (job && job.category) ? String(job.category) : '';
    return JOB_CATEGORY_NAME[key] || key || 'Job';
}

// Full job payloads carry the target_paths array; compact polling payloads carry only
// target_path_count.
export function jobTargetPathCount(job) {
    if (Array.isArray(job.target_paths)) return job.target_paths.length;
    return Number(job.target_path_count || 0);
}

export function jobDisplayName(job) {
    if (!job) return 'Job';
    const custom = (job.display_name || '').trim();
    if (custom) return custom;

    const base = jobCategoryName(job);
    const targetCount = jobTargetPathCount(job);
    if (targetCount > 0) {
        return `${base} - ${targetCount} item${targetCount === 1 ? '' : 's'}`;
    }

    const total = Number(job.items_total || 0);
    if (total > 0) {
        return `${base} - ${total} item${total === 1 ? '' : 's'}`;
    }
    return base;
}
