import { createElement as createLucideElement, icons as lucideIcons } from 'lucide';

/**
 * Lucide Icon component.
 *
 * IMPORTANT: Do not call lucide's `createIcons()` per icon instance.
 * It scans the whole document for `[data-lucide]` each call (it does not
 * support a `root` option). Doing that inside large v-for lists becomes
 * O(n^2) and makes expands/collapses laggy.
 *
 * Instead, render SVGs directly via lucide's `createElement(iconDef)`.
 */
export const LucideIcon = {
    props: {
        name: { type: String, required: true },
        iconClass: { type: [String, Array, Object], default: '' },
        iconStyle: { type: [String, Object], default: null }
    },
    template: `<span ref="container" :style="iconStyle" class="lucide-icon-wrapper inline-flex items-center justify-center leading-none"></span>`,
    computed: {
        combinedClass() {
            let classes = '';
            if (Array.isArray(this.iconClass)) {
                classes = this.iconClass.filter(Boolean).join(' ');
            } else if (typeof this.iconClass === 'object') {
                classes = Object.entries(this.iconClass)
                    .filter(([_, value]) => value)
                    .map(([key, _]) => key)
                    .join(' ');
            } else {
                classes = this.iconClass || '';
            }

            // Ensure we have both width and height if one or none is provided
            const hasWidth = /\bw-[\d.]+/.test(classes);
            const hasHeight = /\bh-[\d.]+/.test(classes);

            if (!hasWidth && !hasHeight) {
                classes += ' w-4 h-4';
            } else if (hasWidth && !hasHeight) {
                const wMatch = classes.match(/\bw-([\d.]+)/);
                if (wMatch) classes += ' h-' + wMatch[1];
            } else if (!hasWidth && hasHeight) {
                const hMatch = classes.match(/\bh-([\d.]+)/);
                if (hMatch) classes += ' w-' + hMatch[1];
            }

            return classes.trim();
        }
    },
    mounted() {
        this.renderIcon();
    },
    updated() {
        this.renderIcon();
    },
    methods: {
        renderIcon() {
            const container = this.$refs.container;
            if (!container) return;

            const key = `${this.name}|${this.combinedClass}`;
            if (container.dataset.lucideKey === key) return;
            container.dataset.lucideKey = key;

            // Clear previous icon
            while (container.firstChild) container.removeChild(container.firstChild);

            // Lucide stores icon defs as PascalCase keys (e.g. "play-circle" -> "PlayCircle")
            const pascal = String(this.name)
                .split(/[-_\s]+/)
                .filter(Boolean)
                .map(part => part.charAt(0).toUpperCase() + part.slice(1))
                .join('');

            // `icons` carries every icon and alias export, so it covers the old top-level lookup too.
            const def = lucideIcons[pascal] || lucideIcons[this.name];
            if (!def) return;

            // Render SVG directly (no global DOM scan)
            const svg = createLucideElement(def);
            svg.setAttribute('class', `lucide lucide-${this.name} ${this.combinedClass}`.trim());
            container.appendChild(svg);
        }
    }
};
