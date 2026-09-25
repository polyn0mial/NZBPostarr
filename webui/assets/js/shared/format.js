// Shared display formatters (pure functions, no page state).

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
