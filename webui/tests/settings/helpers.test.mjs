// Node tests for the settings page's pure helpers. The settings modules are plain ES modules of
// Vue methods; package.json declares commonjs for the build tooling, so a load hook marks the
// page sources as ES modules before importing them.
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

const { foldersMethods } = await import('../../assets/js/pages/settings/folders.js');
const { tvPackIgnoreMethods } = await import('../../assets/js/pages/settings/tv-pack-ignore.js');
const { updatesMethods } = await import('../../assets/js/pages/settings/updates.js');

test('normalizeFolderPathEntry fills blanks and keeps Manual selection only as allow_bulk_selection false', () => {
    assert.deepEqual(foldersMethods.normalizeFolderPathEntry({}), {
        path: '',
        category: 'auto',
        monitor: false,
        allow_bulk_selection: true,
    });
    const manual = foldersMethods.normalizeFolderPathEntry({ path: '/media/tv', category: 'tv', monitor: 1, allow_bulk_selection: false });
    assert.deepEqual(manual, { path: '/media/tv', category: 'tv', monitor: true, allow_bulk_selection: false });
});

test('defaultTvPackIgnore uses the server rule keys', () => {
    assert.deepEqual(tvPackIgnoreMethods.defaultTvPackIgnore(), {
        enabled: true,
        ignore_non_episode: true,
        require_episode: true,
        require_resolution: false,
        require_source: true,
    });
});

test('toggleTvPackIgnore creates the default rules before flipping one', () => {
    const page = { settings: { processing: {} }, ...tvPackIgnoreMethods };
    page.toggleTvPackIgnore('require_resolution');
    assert.equal(page.tvPackIgnoreOn('require_resolution'), true);
    assert.equal(page.tvPackIgnoreOn('enabled'), true);
});

test('formatVersionLabel prefixes a bare version and names a missing one', () => {
    assert.equal(updatesMethods.formatVersionLabel('1.2.3'), 'v1.2.3');
    assert.equal(updatesMethods.formatVersionLabel('v1.2.3'), 'v1.2.3');
    assert.equal(updatesMethods.formatVersionLabel(''), 'unknown');
});

test('formatBytesCompact scales to the largest whole unit', () => {
    assert.equal(updatesMethods.formatBytesCompact(0), '0 B');
    assert.equal(updatesMethods.formatBytesCompact(512), '512 B');
    assert.equal(updatesMethods.formatBytesCompact(1536), '1.50 KB');
    assert.equal(updatesMethods.formatBytesCompact(150 * 1024 * 1024), '150 MB');
});
