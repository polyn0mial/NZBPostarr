// Parity between the browser's category/itype mapping and the server's verdict (W15-F13).
// fixtures/category_parity.jsonl is tools/classify_dump.py run over fixtures/category_parity.tree
// (the classify fixture tree plus the queue-ui-12 client cases), each row with the server's
// upload_itype (logic/pending/selection.upload_itype). The JS mapping stays until this table of
// divergences is empty: then the Queue page can show the server category and send upload_itype.
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
const page = {
    flatCategories: ['tv', 'movies', 'anime', 'music', 'audiobooks', 'books', 'apps', 'disc', 'misc'].map((id) => ({ id })),
    normalizePathKey: (value) => String(value),
};
page.itypeToCategory = queueMethods.itypeToCategory.bind(page);

// What the browser infers from the itype alone (normalizePendingNode's fallback, no server category).
function jsCategory(row) {
    const node = { itype: row.itype };
    normalizePendingNode(page, node);
    return node.assigned_category_safe || '';
}

function divergences() {
    const seen = new Map();
    for (const row of rows) {
        const category = jsCategory(row);
        const itype = queueMethods.resolveUploadItype.call(page, row.itype, row.category);
        const fields = [];
        if (category !== row.category) fields.push(`category ${JSON.stringify(category)} vs server ${JSON.stringify(row.category)}`);
        if (itype !== row.upload_itype) fields.push(`upload_itype ${JSON.stringify(itype)} vs server ${JSON.stringify(row.upload_itype)}`);
        if (!fields.length) continue;
        const key = `itype ${JSON.stringify(row.itype)} dir=${row.is_dir}: ${fields.join('; ')}`;
        if (!seen.has(key)) seen.set(key, row.relpath);
    }
    return Object.fromEntries([...seen.entries()].sort(([a], [b]) => a.localeCompare(b)));
}

// Every divergent case (first fixture row showing it). Owner decision pending between AGENTS
// "browser shows the server verdict" and the labels the client sees today.
const KNOWN_DIVERGENCES = {
    'itype "Anime" dir=false: category "anime" vs server "tv"': '[SubGroup] Frieren (Season 1) [1080p]/Extras/[SubGroup] Frieren - NCED [1080p].mkv',
    'itype "App" dir=false: category "" vs server "apps"': 'SomeApp.v2.1.x64-GRP/SomeApp.v2.1.x64-GRP.nfo',
    'itype "App" dir=true: category "" vs server "apps"': 'SomeApp.v2.1.x64-GRP',
    'itype "Disc" dir=false: category "" vs server "disc"': 'Old.Movie.1985.DVD9-GRP/VIDEO_TS/VIDEO_TS.IFO',
    'itype "Disc" dir=true: category "" vs server "disc"': 'Old.Movie.1985.DVD9-GRP/VIDEO_TS',
    'itype "Ebook" dir=false: category "" vs server "books"': 'Mixed Leaves/book.epub',
    'itype "Ebook" dir=true: category "" vs server "books"': 'Mixed Leaves',
    'itype "External" dir=true: category "" vs server "music"': 'Lossless Album [FLAC]/CD2',
    'itype "External" dir=true: category "" vs server "tv"; upload_itype "TV Episode" vs server "TV Show"': 'Other Show/Season 01',
    'itype "Misc" dir=false: category "" vs server "books"': 'Mixed Leaves/clip.mkv',
    'itype "Misc" dir=false: category "" vs server "misc"': 'Pokémon - Rouge Feu (FR)/Pokémon Épisode 01.mkv',
    'itype "Misc" dir=true: category "" vs server "misc"': 'Empty Folder',
    'itype "Music" dir=false: category "music" vs server "books"': 'Mixed Leaves/song.flac',
};

test('the fixture covers the classify tree and the queue-ui-12 client cases', () => {
    const paths = new Set(rows.map((row) => row.relpath));
    for (const relpath of ['Narrator Read - Long Story.m4b', 'Some Books', 'book/Single Book.epub', 'Lossless Album [FLAC]', 'Mixed Leaves']) {
        assert.ok(paths.has(relpath), relpath);
    }
    assert.ok(rows.some((row) => row.relpath.startsWith('Pokémon')));
    assert.ok(rows.every((row) => typeof row.upload_itype === 'string' && row.upload_itype));
});

test('JS category/itype mapping against the server verdict: every divergence is known', () => {
    assert.deepEqual(divergences(), KNOWN_DIVERGENCES);
});

test('the browser still sends the server category first and anime wins the upload itype', () => {
    assert.equal(queueMethods.resolveUploadItype.call(page, 'Movie', 'anime'), 'Anime');
    assert.equal(queueMethods.resolveUploadItype.call(page, 'External', 'music'), 'Music');
    const node = { itype: 'Movie', detected_category: 'books' };
    normalizePendingNode(page, node);
    assert.equal(node.assigned_category_safe, 'books');
});
