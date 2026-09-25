// Node tests for the pure pending-visibility helpers (pages/queue/pending-visibility.logic.js).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { FILTER_MODE_OPTIONS, searchMatches } from '../../assets/js/pages/queue/pending-visibility.logic.js';

test('an empty search matches everything', () => {
    assert.equal(searchMatches('Anything', '', false), true);
    assert.equal(searchMatches('Anything', null, true), true);
});

test('word search ignores case and release separators and needs every word', () => {
    assert.equal(searchMatches('The.Show.S01E02.1080p-GRP', 'show s01e02', false), true);
    assert.equal(searchMatches('The_Show_[2020]', 'show 2020', false), true);
    assert.equal(searchMatches('The.Show.S01E02', 'show s02', false), false);
});

test('literal search is a plain case-insensitive substring match', () => {
    assert.equal(searchMatches('The.Show.S01', 'show.s01', true), true);
    assert.equal(searchMatches('The.Show.S01', 'show s01', true), false);
});

test('the filter modes keep their values and labels', () => {
    assert.deepEqual(FILTER_MODE_OPTIONS.map((option) => option.value),
        ['hideQueued', 'onlyQueued', 'hideCompleted', 'showCompleted', 'hideIgnored']);
    assert.equal(FILTER_MODE_OPTIONS[0].label, 'Hide Staged');
});
