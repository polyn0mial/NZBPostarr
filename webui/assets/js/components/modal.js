/**
 * Shared modal shell: teleport + backdrop + panel + header + close button.
 *
 * Replaces the hand-coded block that had drifted apart across the pages (some
 * used a self-click backdrop, some a separate overlay div, headers differed).
 * Body content goes in the default slot; extra header buttons in #header-actions.
 *
 *   <modal :open="showThing" title="Thing" icon="list" @close="closeThing()">
 *       <div class="overflow-y-auto">...</div>
 *   </modal>
 */
const openModalStack = []; // Escape only closes the topmost open modal

export const Modal = {
    props: {
        open: { type: Boolean, default: false },
        title: { type: String, default: '' },
        subtitle: { type: String, default: '' },
        subtitleClass: { type: String, default: '' },
        icon: { type: String, default: '' },
        iconClass: { type: String, default: 'text-notion-accent' },
        badge: { type: [String, Number], default: null },
        maxWidth: { type: String, default: 'max-w-2xl' },
        panelClass: { type: String, default: 'bg-notion-bg-secondary max-h-[85vh]' },
        zClass: { type: String, default: 'z-50' },
        teleport: { type: Boolean, default: true },
        closeDisabled: { type: Boolean, default: false },
        closeOnBackdrop: { type: Boolean, default: true }
    },
    emits: ['close'],
    template: `
        <Teleport to="body" :disabled="!teleport">
            <Transition name="modal">
                <div v-if="open" :class="['fixed inset-0 flex items-end sm:items-center justify-center sm:p-4', zClass]" role="dialog" aria-modal="true">
                    <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="onBackdrop()"></div>
                    <div :class="['relative border border-notion-border rounded-t-xl sm:rounded-lg shadow-2xl w-full flex flex-col overflow-hidden', maxWidth, panelClass]">
                        <div class="shrink-0 flex items-center justify-between gap-3 px-4 py-3 border-b border-notion-divider bg-notion-bg-secondary">
                            <div class="flex items-center gap-2 min-w-0">
                                <lucide-icon v-if="icon" :name="icon" :icon-class="['size-4 shrink-0', iconClass]"></lucide-icon>
                                <div class="min-w-0">
                                    <h3 class="text-sm font-semibold text-notion-text-primary truncate">{{ title }}</h3>
                                    <p v-if="subtitle" :class="['text-[10px] text-notion-text-tertiary truncate', subtitleClass]">{{ subtitle }}</p>
                                </div>
                                <span v-if="badge !== null && badge !== ''" class="px-1.5 py-0.5 bg-notion-bg-hover text-notion-text-secondary rounded text-xs font-medium shrink-0">{{ badge }}</span>
                            </div>
                            <div class="flex items-center gap-3 shrink-0">
                                <slot name="header-actions"></slot>
                                <button @click="$emit('close')" :disabled="closeDisabled" class="icon-btn disabled:opacity-40" aria-label="Close">
                                    <lucide-icon name="x" icon-class="size-4"></lucide-icon>
                                </button>
                            </div>
                        </div>
                        <slot></slot>
                    </div>
                </div>
            </Transition>
        </Teleport>
    `,
    watch: {
        open: {
            immediate: true,
            handler(isOpen) {
                const at = openModalStack.indexOf(this);
                if (isOpen) {
                    if (at === -1) openModalStack.push(this);
                } else if (at !== -1) {
                    openModalStack.splice(at, 1);
                }
            }
        }
    },
    mounted() {
        this._modalKeyHandler = (event) => {
            if (event.key !== 'Escape' || this.closeDisabled) return;
            if (openModalStack[openModalStack.length - 1] !== this) return;
            event.preventDefault();
            this.$emit('close');
        };
        document.addEventListener('keydown', this._modalKeyHandler);
    },
    beforeUnmount() {
        document.removeEventListener('keydown', this._modalKeyHandler);
        const at = openModalStack.indexOf(this);
        if (at !== -1) openModalStack.splice(at, 1);
    },
    methods: {
        onBackdrop() {
            if (this.closeOnBackdrop && !this.closeDisabled) this.$emit('close');
        }
    }
};
