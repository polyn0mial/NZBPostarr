// Node tests for the pure pending-tree helpers that the queue page modules share.
// pages/queue.js is a browser entry point: it imports the bundler alias "page-base" and
// mounts the Vue page on import. A resolve hook swaps those imports for inert stubs so the
// module can load under node and its exported helpers can be tested directly.
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';

const stubs = {
    'page-base': [
        'export const createVuePage = () => null;',
        'export const queueCategoryMeta = {};',
        'export const categoryLabel = () => "";',
        'export const itypeToCategory = () => "";',
        'export const categoryToItype = () => "";',
    ].join('\n'),
    sortablejs: 'export default class Sortable {}',
    'lodash.debounce': 'export default (fn) => fn;',
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

const { normalizePendingNode, deepFreezePendingTree } = await import('../assets/js/pages/queue.js');

// The two page methods normalizePendingNode calls on the Vue instance.
const page = {
    normalizePathKey: (value) => String(value).replace(/\\/g, '/').toLowerCase(),
    itypeToCategory: (itype) => ({ 1: 'movies', 2: 'tv' })[itype] || '',
};

test('normalizePendingNode derives a key from the path only when none is set', () => {
    const node = { path: 'D:\\Media\\Show.S01' };
    normalizePendingNode(page, node);
    assert.equal(node.key, 'path:d:/media/show.s01');

    const keyed = { key: 'kept', path: '/media/x' };
    normalizePendingNode(page, keyed);
    assert.equal(keyed.key, 'kept');
});

test('normalizePendingNode prefers detected, then explicit, then inherited, then itype category', () => {
    const detected = { detected_category: 'Series2', category: 'movies', itype: 1 };
    normalizePendingNode(page, detected, 'music');
    assert.equal(detected.assigned_category_safe, 'tv');

    const explicit = { category: 'Films', itype: 2 };
    normalizePendingNode(page, explicit, 'music');
    assert.equal(explicit.assigned_category_safe, 'movies');
    assert.equal(explicit.detected_category, 'movies');

    const inherited = { itype: 1 };
    normalizePendingNode(page, inherited, 'ebook');
    assert.equal(inherited.assigned_category_safe, 'ebooks');

    const inferred = { itype: 2 };
    normalizePendingNode(page, inferred);
    assert.equal(inferred.assigned_category_safe, 'tv');

    const unknown = { category: 'Something Else' };
    normalizePendingNode(page, unknown);
    assert.equal(unknown.assigned_category_safe, 'something else');

    const empty = {};
    normalizePendingNode(page, empty);
    assert.equal(empty.assigned_category_safe, '');
    assert.equal('detected_category' in empty, false);
});

test('normalizePendingNode promotes files to children and passes the category down', () => {
    const node = {
        category: 'anime',
        files: [{ name: 'a.mkv' }, { name: 'b.mkv', children: [{ name: 'c.nfo' }] }],
    };
    normalizePendingNode(page, node);

    assert.equal(node.children, node.files);
    assert.equal(node.is_dir, true);
    assert.equal(node.children[0].assigned_category_safe, 'anime');
    assert.equal(node.children[1].children[0].assigned_category_safe, 'anime');
    assert.equal('__normalizing' in node, false);
    assert.equal('__normalizing' in node.children[1].children[0], false);

    const withChildren = { is_dir: false, children: [{ name: 'x' }], files: [{ name: 'y' }] };
    normalizePendingNode(page, withChildren);
    assert.equal(withChildren.children.length, 1);
    assert.equal(withChildren.children[0].name, 'x');
    assert.equal(withChildren.is_dir, false);
});

test('normalizePendingNode ignores non-objects and a node already being normalized', () => {
    assert.doesNotThrow(() => normalizePendingNode(page, null));
    assert.doesNotThrow(() => normalizePendingNode(page, 'text'));

    const busy = { __normalizing: true, category: 'tv' };
    normalizePendingNode(page, busy);
    assert.equal('assigned_category_safe' in busy, false);
    assert.equal(busy.__normalizing, true);
});

test('deepFreezePendingTree freezes every category list, node and nested map', () => {
    const leaf = { name: 'e01.mkv' };
    const folder = { name: 'Show', children: [leaf], indexers: { a: 1 }, indexer_errors: { b: 'x' } };
    const extItem = { name: 'ext', children: [] };
    const group = { folder_name: 'Downloads', items: [extItem] };
    const tree = { tv: [folder], movies: [], external: [group], summary: { total: 1 } };

    const result = deepFreezePendingTree(tree);

    assert.equal(result, tree);
    for (const value of [tree, tree.tv, tree.movies, folder, folder.children, leaf, folder.indexers,
        folder.indexer_errors, tree.external, group, group.items, extItem, extItem.children]) {
        assert.equal(Object.isFrozen(value), true);
    }
    // Only arrays of nodes are walked; other top-level values are left as they are.
    assert.equal(Object.isFrozen(tree.summary), false);
});

test('deepFreezePendingTree passes non-objects through and skips frozen nodes', () => {
    assert.equal(deepFreezePendingTree(null), null);
    assert.equal(deepFreezePendingTree('x'), 'x');

    const child = { name: 'inner' };
    const frozen = Object.freeze({ name: 'outer', children: [child] });
    deepFreezePendingTree({ misc: [frozen] });
    assert.equal(Object.isFrozen(child), false);
});
