// Contract between the Queue page and the server's category verdict (W15-F13).
// fixtures/category_parity.jsonl is tools/classify_dump.py run over fixtures/category_parity.tree
// (the classify fixture tree plus the queue-ui-12 client cases), each row with the server's
// upload_itype (logic/pending/selection.upload_itype). /api/pending stamps both on every row
// (selection.stamp_server_verdict); the page displays that category and sends that upload_itype.
// Its own itype mapping is left only for payloads without them and for a manual category.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { test } from 'node:test';

const categoryModule = new URL('../assets/js/shared/category.js', import.meta.url).href;
const stubs = {
    'page-base': [
        `export { itypeToCategory, categoryToItype } from ${JSON.stringify(categoryModule)};`,
        'export const createVuePage = () => null;',
        'export const queueCategoryMeta = {};',
        'export const categoryLabel = () => "";',
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
export async function load(url, context, nextLoad) {
    if (url.startsWith('file:') && url.includes('/assets/js/') && url.endsWith('.js')) {
        return nextLoad(url, { ...context, format: 'module' });
    }
    return nextLoad(url, context);
}
`;
register(`data:text/javascript,${encodeURIComponent(hooks)}`);

const { default: { methods: queueMethods } } = await import('../assets/js/pages/queue/pending-categories.js');
const { normalizePendingNode } = await import('../assets/js/pages/queue/pending-tree.logic.js');

const rows = readFileSync(new URL('./fixtures/category_parity.jsonl', import.meta.url), 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line));

// The server's category ids (logic/pending/selection._CATEGORY_UPLOAD_ITYPES plus tv, disc, misc).
function queuePage(manualExternalCategories = {}) {
    const page = {
        flatCategories: ['tv', 'movies', 'anime', 'music', 'audiobooks', 'books', 'apps', 'disc', 'misc'].map((id) => ({ id })),
        manualExternalCategories,
        externalCategories: {},
        normalizePathKey: (value) => String(value),
        isExternalDescendantNode: () => false,
    };
    for (const name of ['itypeToCategory', 'serverCategoryForItem', 'getCategoryForItem', '_resolveExtCategory', 'resolveUploadItype']) {
        page[name] = queueMethods[name].bind(page);
    }
    return page;
}

// One fixture row as /api/pending sends it.
function serverNode(row) {
    return {
        key: `row:${row.relpath}`,
        path: row.relpath,
        is_dir: row.is_dir,
        itype: row.itype,
        detected_category: row.category,
        upload_itype: row.upload_itype,
    };
}

function pageVerdict(page, node) {
    normalizePendingNode(page, node);
    const category = page.getCategoryForItem(node);
    return { safe: node.assigned_category_safe, category, upload_itype: page.resolveUploadItype(node, category) };
}

test('the fixture covers the classify tree and the queue-ui-12 client cases', () => {
    const paths = new Set(rows.map((row) => row.relpath));
    for (const relpath of ['Narrator Read - Long Story.m4b', 'Some Books', 'book/Single Book.epub', 'Lossless Album [FLAC]', 'Mixed Leaves']) {
        assert.ok(paths.has(relpath), relpath);
    }
    assert.ok(rows.some((row) => row.relpath.startsWith('Pokémon')));
    assert.ok(rows.every((row) => typeof row.category === 'string' && row.category));
    assert.ok(rows.every((row) => typeof row.upload_itype === 'string' && row.upload_itype));
});

test('the Queue page shows the server category and sends the server upload_itype for every row', () => {
    const page = queuePage();
    const divergences = {};
    for (const row of rows) {
        const got = pageVerdict(page, serverNode(row));
        const want = { safe: row.category, category: row.category, upload_itype: row.upload_itype };
        if (JSON.stringify(got) !== JSON.stringify(want)) divergences[row.relpath] = got;
    }
    assert.deepEqual(divergences, {});
});

test('a manual category still wins over the server verdict', () => {
    const row = rows.find((candidate) => candidate.relpath === 'Other Show/Season 01');
    assert.ok(row);
    const node = serverNode(row);
    assert.deepEqual(pageVerdict(queuePage({ [node.key]: 'movies' }), node), { safe: 'tv', category: 'movies', upload_itype: 'Movie' });
    assert.equal(pageVerdict(queuePage({ [node.key]: 'anime' }), serverNode(row)).upload_itype, 'Anime');
});

test('a payload without the server fields falls back to the itype mapping', () => {
    const page = queuePage();
    assert.deepEqual(pageVerdict(page, { itype: 'Movie' }), { safe: 'movies', category: 'movies', upload_itype: 'Movie' });
    assert.equal(page.resolveUploadItype({ itype: 'Movie' }, 'anime'), 'Anime');
    assert.equal(page.resolveUploadItype({ itype: 'External' }, 'music'), 'Music');
    assert.equal(page.resolveUploadItype({ itype: 'External', is_dir: true }, 'tv'), 'TV Episode');
    const node = { itype: 'Movie', detected_category: 'books' };
    normalizePendingNode(page, node);
    assert.equal(node.assigned_category_safe, 'books');
});
