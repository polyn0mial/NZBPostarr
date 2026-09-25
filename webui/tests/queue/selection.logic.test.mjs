// Node tests for the pure selection helpers (pages/queue/selection.logic.js).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    dedupeSelectionEntries,
    isMultiSeasonRange,
    selectionPathIsDirectory,
} from '../../assets/js/pages/queue/selection.logic.js';

test('selectionPathIsDirectory trusts the recorded is_dir first', () => {
    assert.equal(selectionPathIsDirectory('/media/a.mkv', { is_dir: true }), true);
    assert.equal(selectionPathIsDirectory('/media/Show', { is_dir: false }), false);
});

test('selectionPathIsDirectory treats a media or archive extension as a file', () => {
    assert.equal(selectionPathIsDirectory('/media/Show.S01E01.mkv', undefined), false);
    assert.equal(selectionPathIsDirectory('/media/Book.EPUB', null), false);
    assert.equal(selectionPathIsDirectory('/media/Show.S01', undefined), true);
    assert.equal(selectionPathIsDirectory('ext:downloads:D:\\x\\movie.iso', undefined), false);
    assert.equal(selectionPathIsDirectory('ext:downloads:D:\\x\\Folder', undefined), true);
});

test('selectionPathIsDirectory prefers the fallback path, then the meta path, over the key', () => {
    assert.equal(selectionPathIsDirectory('key-without-ext', undefined, '/media/a.mp4'), false);
    assert.equal(selectionPathIsDirectory('key-without-ext', { path: '/media/b.flac' }), false);
    assert.equal(selectionPathIsDirectory('/media/c.mkv', { path: '/media/Folder' }), true);
});

test('isMultiSeasonRange matches an S01-S03 span in the name or the path', () => {
    assert.equal(isMultiSeasonRange({ name: 'Show.S01-S03.1080p' }), true);
    assert.equal(isMultiSeasonRange({ path: '/tv/Show S01 – S02' }), true);
    assert.equal(isMultiSeasonRange({ source_path: '/tv/Show.S01-S02' }), true);
    assert.equal(isMultiSeasonRange({ name: 'Show.S01.1080p' }), false);
    assert.equal(isMultiSeasonRange(null), false);
});

test('dedupeSelectionEntries keeps the first entry per key and drops keyless ones', () => {
    const a1 = { item: { key: 'a' }, n: 1 };
    const a2 = { item: { key: 'a' }, n: 2 };
    const b = { item: { key: 'b' } };
    assert.deepEqual(dedupeSelectionEntries([a1, { item: {} }, null, b, a2]), [a1, b]);
});
