// Node tests for the pure modules split out of page-base.js: shared/format, shared/status and
// the core/storage key registry. package.json declares commonjs for the build tooling; the
// sources under assets/js are ES modules, so a load hook marks them as such.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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

const { formatUtils } = await import('../assets/js/shared/format.js');
const { statusConfig, getStatusConfig } = await import('../assets/js/shared/status.js');
const { KEY } = await import('../assets/js/core/storage.js');

test('formatBytes and formatSpeed use binary JEDEC units', () => {
    assert.equal(formatUtils.formatBytes(1024), '1 KB');
    assert.equal(formatUtils.formatSpeed(1048576), '1 MB/s');
});

test('escapeHtml escapes markup characters', () => {
    assert.equal(formatUtils.escapeHtml('<a href="x">&</a>'), '&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
});

test('escapeRegex escapes regex metacharacters', () => {
    assert.equal(formatUtils.escapeRegex('a.b*c'), 'a\\.b\\*c');
});

test('getStatusConfig returns the known status and a fallback for unknown ones', () => {
    assert.equal(getStatusConfig('completed'), statusConfig.completed);
    assert.deepEqual(getStatusConfig('nonsense'), {
        icon: 'circle',
        color: 'text-notion-text-tertiary',
        bg: 'bg-gray-500/20',
    });
});

test('storage KEY registry matches the contracted storage keys', () => {
    const contracts = JSON.parse(readFileSync(new URL('../../tests/webui/contracts.json', import.meta.url), 'utf8'));
    assert.deepEqual(Object.values(KEY).sort(), [...contracts.storage_keys].sort());
});
