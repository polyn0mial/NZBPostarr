// Loads webui/assets/js/**/*.js as ES modules under `node --test`: package.json stays
// "type": "commonjs" for the build scripts, so node would otherwise parse them as CommonJS.
import { register } from 'node:module';

const hooks = `
export async function load(url, context, nextLoad) {
    if (url.startsWith('file:') && url.includes('/assets/js/') && url.endsWith('.js')) {
        return nextLoad(url, { ...context, format: 'module' });
    }
    return nextLoad(url, context);
}
`;
register(`data:text/javascript,${encodeURIComponent(hooks)}`);
