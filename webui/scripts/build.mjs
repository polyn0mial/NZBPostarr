import { mkdir, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const webuiDir = fileURLToPath(new URL('..', import.meta.url));
const jsDir = path.join(webuiDir, 'assets', 'js');
const pagesDir = path.join(jsDir, 'pages');
const outdir = path.join(jsDir, 'dist');

// Every page is an entry: pages/<name>.js or pages/<name>/index.js. The other modules in a
// page folder are imported by its index.js, not pages of their own.
async function pageEntries() {
    const entries = [];
    for (const dirent of await readdir(pagesDir, { withFileTypes: true })) {
        if (dirent.isDirectory()) {
            const pageDir = path.join(pagesDir, dirent.name);
            if ((await readdir(pageDir)).includes('index.js')) entries.push(path.join(pageDir, 'index.js'));
        } else if (dirent.name.endsWith('.js')) {
            entries.push(path.join(pagesDir, dirent.name));
        }
    }
    return entries.sort();
}

const entryPoints = [path.join(jsDir, 'page-base.js'), ...(await pageEntries())];

await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });

const result = await build({
    entryPoints,
    bundle: true,
    format: 'esm',
    splitting: true,
    minify: true,
    target: ['es2020'],
    outdir,
    outbase: jsDir,
    entryNames: '[dir]/[name]',
    chunkNames: 'chunks/[name]-[hash]',
    legalComments: 'none',
    alias: {
        'page-base': path.join(jsDir, 'page-base.js'),
        'vue': 'vue/dist/vue.esm-bundler.js',
    },
    define: {
        '__VUE_OPTIONS_API__': 'true',
        '__VUE_PROD_DEVTOOLS__': 'false',
        '__VUE_PROD_HYDRATION_MISMATCH_DETAILS__': 'false',
    },
    logLevel: 'info',
    metafile: true,
});

const outputBytes = Object.values(result.metafile.outputs)
    .reduce((total, output) => total + output.bytes, 0);
console.log(`Built ${Object.keys(result.metafile.outputs).length} JS assets (${outputBytes} bytes total).`);
