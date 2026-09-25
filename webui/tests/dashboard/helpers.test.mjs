// Node tests for the pure helpers the dashboard page modules share. package.json declares
// commonjs for the build tooling; a load hook reads the page sources as ES modules.
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';

const hooks = `
export async function load(url, context, nextLoad) {
    if (url.startsWith('file:') && url.includes('/assets/js/') && url.endsWith('.js')) {
        return nextLoad(url, { ...context, format: 'module' });
    }
    return nextLoad(url, context);
}
`;
register(`data:text/javascript,${encodeURIComponent(hooks)}`);

const { DASHBOARD_CARD_DEFAULTS, DASHBOARD_CARD_IDS, normalizeEnabledMap, normalizeOrderedIds } = await import('../../assets/js/pages/dashboard/cards.js');
const { normalizeStringArray } = await import('../../assets/js/pages/dashboard/upload-form.js');

test('normalizeOrderedIds keeps the saved order, drops unknown and repeated ids, appends missing ones', () => {
    assert.deepEqual(
        normalizeOrderedIds(['console', 'bogus', 'overview', 'console'], DASHBOARD_CARD_IDS),
        ['console', 'overview', 'new-upload', 'usenet-stream', 'active-jobs', 'server-stats'],
    );
    assert.deepEqual(normalizeOrderedIds(null, ['a', 'b']), ['a', 'b']);
});

test('normalizeEnabledMap enables every card unless it is saved as false', () => {
    assert.deepEqual(normalizeEnabledMap(undefined), DASHBOARD_CARD_DEFAULTS);
    const map = normalizeEnabledMap({ console: false, overview: 0, bogus: false });
    assert.equal(map.console, false);
    assert.equal(map.overview, true);
    assert.equal('bogus' in map, false);
});

test('normalizeStringArray trims, drops empties and keeps the first of each value', () => {
    assert.deepEqual(normalizeStringArray([' tv ', '', null, 'tv', 'movies', 3]), ['tv', 'movies', '3']);
    assert.deepEqual(normalizeStringArray('tv'), []);
});
