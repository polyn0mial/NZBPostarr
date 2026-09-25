// Lint gate for the web UI sources. no-undef is the rule that matters: esbuild leaves a
// name it cannot resolve as a free global, so a helper one module uses without importing
// only fails at runtime with a ReferenceError. The dist bundles and vendored builds are
// generated or third-party code and are not linted.
import globals from 'globals';

const rules = {
    'no-undef': 'error',
    // A leading underscore marks a deliberate unused binding, such as a computed that
    // reads reactive values only to register them as dependencies.
    'no-unused-vars': ['error', { args: 'none', caughtErrors: 'none', varsIgnorePattern: '^_' }],
};

export default [
    {
        ignores: ['assets/js/dist/**', 'assets/js/vendor/**', 'node_modules/**'],
    },
    {
        files: ['assets/js/**/*.js'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            globals: {
                ...globals.browser,
                // Loaded as a classic script by base.html, never imported. Vue is not a
                // global here: every page imports it through the bundle, so it is not declared.
                lucide: 'readonly',
            },
        },
        rules,
    },
    {
        // Unused bindings still present in pages owned by later overhaul batches. They are
        // reported as warnings until those batches clean them up; no-undef stays an error.
        files: [
            'assets/js/pages/dashboard.js',
            'assets/js/pages/history.js',
            'assets/js/pages/settings.js',
            'assets/js/pages/stats.js',
        ],
        rules: {
            'no-unused-vars': ['warn', { args: 'none', caughtErrors: 'none', varsIgnorePattern: '^_' }],
        },
    },
    {
        files: ['scripts/**/*.mjs', 'tests/**/*.mjs', 'eslint.config.mjs'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            globals: {
                ...globals.node,
            },
        },
        rules,
    },
];
