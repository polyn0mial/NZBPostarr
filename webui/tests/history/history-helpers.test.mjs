// Node tests for the History page's pure group and selection helpers. groups.js imports the
// bundler alias "page-base"; a resolve hook swaps it for a stub so the modules load under node.
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';

const stubs = {
    'page-base': "export const isMovieType = (t) => ['movie', 'movies'].includes(String(t || '').toLowerCase());",
};

const hooks = `
const stubs = ${JSON.stringify(stubs)};
export async function resolve(specifier, context, nextResolve) {
    if (Object.hasOwn(stubs, specifier)) {
        return { url: 'data:text/javascript,' + encodeURIComponent(stubs[specifier]), shortCircuit: true };
    }
    return nextResolve(specifier, context);
}
// package.json declares commonjs for the build tooling; the page sources are ES modules.
export async function load(url, context, nextLoad) {
    if (url.startsWith('file:') && url.includes('/assets/js/') && url.endsWith('.js')) {
        return nextLoad(url, { ...context, format: 'module' });
    }
    return nextLoad(url, context);
}
`;
register(`data:text/javascript,${encodeURIComponent(hooks)}`);

const { buildUploadGroupFromServerGroup } = await import('../../assets/js/pages/history/groups.js');
const { groupSelectionTargets, isGroupFullySelected, selectionMethods } = await import('../../assets/js/pages/history/selection.js');

const ep = (name, season, episode, extra = {}) => ({
    item_name: name, season_number: season, episode_number: episode, filesize: 10, updated_at: `2026-01-0${episode || 1}`, media_type: 'tv', ...extra,
});

test('summary-only group keeps the server item count and skips season analysis', () => {
    const group = buildUploadGroupFromServerGroup({ show_name: 'Show', title_key: 'show', item_count: 7, summary_only: true, total_size: 42, media_type: 'TV' });
    assert.equal(group.detailsLoaded, false);
    assert.equal(group.itemCount, 7);
    assert.equal(group.totalSize, 42);
    assert.deepEqual(group.seasonList, []);
});

test('loaded TV group buckets seasons and reports missing episodes', () => {
    const group = buildUploadGroupFromServerGroup({
        show_name: 'Show',
        media_type: 'tv',
        items: [ep('a', 1, 1), ep('b', 1, 2), ep('c', 1, 5)],
    });
    assert.equal(group.key, 'show');
    assert.equal(group.detailsLoaded, true);
    assert.equal(group.totalSize, 30);
    assert.equal(group.seasonList.length, 1);
    const [season] = group.seasonList;
    assert.equal(season.num, 1);
    assert.deepEqual(season.missingList, [3, 4]);
    assert.equal(group.missingCount, 2);
});

test('movie group skips season analysis', () => {
    const group = buildUploadGroupFromServerGroup({ show_name: 'Film', media_type: 'movie', items: [{ item_name: 'f', filesize: 5, updated_at: 'x' }] });
    assert.equal(group.isMovie, true);
    assert.deepEqual(group.seasonList, []);
    assert.equal(group.missingCount, 0);
});

test('group selection targets episodes when present, else every item', () => {
    const pack = { item_name: 'pack', episode_number: null };
    const withEps = { items: [pack, ep('e1', 1, 1), ep('e2', 1, 2)] };
    assert.deepEqual(groupSelectionTargets(withEps).map(it => it.item_name), ['e1', 'e2']);
    assert.deepEqual(groupSelectionTargets({ items: [pack] }).map(it => it.item_name), ['pack']);
    assert.deepEqual(groupSelectionTargets({ items: [] }), []);
    assert.deepEqual(groupSelectionTargets(null), []);

    assert.equal(isGroupFullySelected(withEps, new Set(['e1', 'e2'])), true);
    assert.equal(isGroupFullySelected(withEps, new Set(['e1', 'pack'])), false);
    assert.equal(isGroupFullySelected({ items: [] }, new Set()), false);
});

test('unchecking a group header clears its packs too', () => {
    const group = { items: [{ item_name: 'pack', episode_number: null }, ep('e1', 1, 1)] };
    const page = { selectedItems: new Set(), uploads: group.items, selectAll: false, ...selectionMethods };
    page.toggleGroupSelection(group, true);
    assert.deepEqual([...page.selectedItems], ['e1']);
    assert.equal(page.isGroupSelected(group), true);
    page.selectedItems.add('pack');
    page.toggleGroupSelection(group, false);
    assert.equal(page.selectedItems.size, 0);
});
