import { mkdir, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { access } from 'node:fs/promises';

import { build } from 'esbuild';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));

// Walk up from the script's location to find the repo root by looking for package.json
async function findRepoRoot(startDir) {
    let currentDir = startDir;
    while (true) {
        const packageJsonPath = path.join(currentDir, 'package.json');
        try {
            await access(packageJsonPath);
            return currentDir;
        } catch {
            const parentDir = path.dirname(currentDir);
            if (parentDir === currentDir) {
                throw new Error(`Could not find package.json walking up from ${startDir}`);
            }
            currentDir = parentDir;
        }
    }
}

const projectRoot = await findRepoRoot(scriptDir);
const packageJson = JSON.parse(
    await readFile(path.join(projectRoot, 'package.json'), 'utf8'),
);

if (packageJson.name !== 'nzbpostarr-webui') {
    throw new Error(`Refusing to build from unexpected package: ${packageJson.name}`);
}

const outdir = path.join(projectRoot, 'assets', 'js', 'dist');
const expectedOutdir = path.resolve(projectRoot, 'assets', 'js', 'dist');
if (path.resolve(outdir) !== expectedOutdir) {
    throw new Error(`Refusing to clean unexpected output directory: ${outdir}`);
}

const entryPoints = [
    'assets/js/page-base.js',
    'assets/js/pages/dashboard.js',
    'assets/js/pages/history.js',
    'assets/js/pages/queue.js',
    'assets/js/pages/settings.js',
    'assets/js/pages/stats.js',
].map((entry) => path.join(projectRoot, entry));

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
    outbase: path.join(projectRoot, 'assets', 'js'),
    entryNames: '[dir]/[name]',
    chunkNames: 'chunks/[name]-[hash]',
    legalComments: 'none',
    alias: {
        'page-base': path.join(projectRoot, 'assets', 'js', 'page-base.js'),
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
