// History series helpers for the dashboard and stats sparklines (pure; no Vue).

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
