// Browser storage: the key registry, JSON helpers and the UI revision sync.
//
// KEY lists every localStorage key string the web UI reads or writes. The strings are a
// contract with every browser that already holds saved state (tests/webui/contracts.json);
// never rename one.
export const KEY = Object.freeze({
    theme: 'nzbpostarr_theme',
    expandedSections: 'nzbpostarr_expanded_sections',
    uiRevisionSignature: 'nzbpostarr_ui_revision_signature',
    persistPrefix: 'nzbpostarr_persist_',
    queuePersist: 'nzbpostarr_persist_queue',
    queuePersistPrefix: 'nzbpostarr_persist_queue_',
    queueBootstrapReset: 'nzbpostarr_queue_bootstrap_reset_v20260805_r3',
    pendingCache: 'nzbpostarr_pending_cache',
    pendingExpanded: 'nzb_pending_expanded',
    pendingExternalGroupOrder: 'nzbpostarr_pending_external_group_order',
    pendingExternalGroupOrderLocked: 'nzbpostarr_pending_external_group_order_locked',
    finishedJobs: 'nzb_finished_jobs',
    categoryAppearance: 'nzbpostarr_category_appearance_v2',
    categoryAppearanceLegacy: 'nzbpostarr_category_appearance_v1',
    updateBannerDismissed: 'nzbpostarr_update_banner_dismissed',
});

// Parsed JSON object under `key`, or {} when it is missing or unreadable.
export function readJSON(key, failureMessage) {
    try {
        return JSON.parse(localStorage.getItem(key) || '{}');
    } catch (e) {
        console.warn(failureMessage, e);
        return {};
    }
}

// Set one field of the JSON object stored under `key`.
export function writeJSONField(key, field, value, failureMessage) {
    try {
        const current = JSON.parse(localStorage.getItem(key) || '{}');
        current[field] = value;
        localStorage.setItem(key, JSON.stringify(current));
    } catch (e) {
        console.warn(failureMessage, e);
    }
}

export function readTheme() {
    return localStorage.getItem(KEY.theme) || 'dark';
}

export function saveTheme(theme) {
    try {
        localStorage.setItem(KEY.theme, theme);
    } catch (e) {
        console.error('Failed to save theme to localStorage:', e);
    }
}

// ============================================================
//  UI REVISION SYNC
//  When the server revision changes, drop the saved UI state (section states, the pending
//  cache and every nzbpostarr_persist_* page state) so a new release starts clean.
// ============================================================

const REVISION_CLEAR_KEYS = new Set([
    KEY.expandedSections,
    KEY.pendingCache,
]);

function getStoredRevision() {
    try {
        return localStorage.getItem(KEY.uiRevisionSignature) || '';
    } catch (_err) {
        return '';
    }
}

function setStoredRevision(signature) {
    try {
        localStorage.setItem(KEY.uiRevisionSignature, signature);
    } catch (_storageError) {
        // Storage full or disabled: keep the page usable.
    }
}

function clearPersistedUiState() {
    try {
        const keysToRemove = [];
        for (let i = 0; i < localStorage.length; i += 1) {
            const key = localStorage.key(i);
            if (!key) continue;
            if (key === KEY.uiRevisionSignature || REVISION_CLEAR_KEYS.has(key) || key.startsWith(KEY.persistPrefix)) {
                keysToRemove.push(key);
            }
        }
        keysToRemove.forEach((key) => localStorage.removeItem(key));
    } catch (_storageError) {
        // Storage full or disabled: keep the page usable.
    }
}

async function fetchRevisionSignal() {
    const response = await fetch('/api/system/revision', {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: {
            Accept: 'application/json',
        },
    });
    if (!response.ok) {
        return null;
    }
    return await response.json();
}

export async function syncRevision() {
    try {
        const payload = await fetchRevisionSignal();
        if (!payload || !payload.revision) {
            return;
        }
        const signature = String(payload.revision);
        const previous = getStoredRevision();
        if (!previous) {
            setStoredRevision(signature);
            return;
        }
        if (previous !== signature) {
            clearPersistedUiState();
            setStoredRevision(signature);
        }
    } catch (_networkError) {
        // Transient network problem: the next poll retries.
    }
}

// Once per page: on pageshow, whenever the tab becomes visible, 1.5 s after load and every 30 s.
export function startRevisionSync() {
    if (window.__nzbpostarrRevisionSyncStarted) return;
    window.__nzbpostarrRevisionSyncStarted = true;
    window.addEventListener('pageshow', () => {
        syncRevision();
    });
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            syncRevision();
        }
    });
    window.setTimeout(syncRevision, 1500);
    window.setInterval(syncRevision, 30 * 1000);
}
