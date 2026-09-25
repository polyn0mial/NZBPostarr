import { Sparkline } from './sparkline.js';

// Dashboard and stats metric card: icon, value, optional progress bar and sparkline.
export const StatsCard = {
    components: { Sparkline },
    props: {
        title: String,
        icon: String,
        color: String, // e.g., 'purple-400'
        value: [String, Number],
        unit: { type: String, default: '' },
        progress: { type: Number, default: null },
        sparkData: { type: Array, default: null },
        peakKey: { type: String, default: null },
        valueSuffix: { type: String, default: '' },
        footerLabel: { type: String, default: 'Last 5 min' },
        valueId: { type: String, default: null }
    },
    template: `
        <div class="bg-notion-bg border border-notion-border rounded-lg p-3 flex flex-col h-full transition-all hover:shadow-md">
            <div class="flex items-center gap-2 mb-2">
                <div :class="['w-6 h-6 rounded flex items-center justify-center flex-shrink-0', getBgClass()]">
                    <lucide-icon :name="icon" :icon-class="['w-3.5 h-3.5', 'text-' + color]"></lucide-icon>
                </div>
                <p class="text-notion-text-tertiary uppercase tracking-wide text-[10.5px]">{{ title }}</p>
            </div>
            <p :id="valueId" class="text-lg font-semibold text-notion-text-primary">{{ value }}{{ unit }}</p>
            
            <div v-if="progress !== null" class="mt-1 h-1 bg-notion-bg-hover rounded-full overflow-hidden">
                <div :class="['h-full rounded-full transition-all duration-300', 'bg-' + color]"
                     :style="{ width: Math.min(100, progress) + '%' }"></div>
            </div>

            <slot name="extra"></slot>

            <div v-if="(sparkData && sparkData.length) || footerLabel" class="mt-auto pt-2">
                <sparkline :data="sparkData" :color-rgb="getRgb(color)" :peak-key="peakKey" :value-suffix="valueSuffix"></sparkline>
                <p v-if="footerLabel" class="text-notion-text-tertiary mt-1 text-center text-[10px]">{{ footerLabel }}</p>
            </div>
        </div>
    `,
    methods: {
        getRgb(color) {
            const isLight = document.documentElement.getAttribute('data-theme') === 'light';
            const map = isLight ? {
                'orange-400': 'rgb(234, 88, 12)',
                'green-400': 'rgb(22, 163, 74)',
                'blue-400': 'rgb(37, 99, 235)',
                'cyan-400': 'rgb(8, 145, 178)',
                'purple-400': 'rgb(147, 51, 234)',
                'pink-400': 'rgb(219, 39, 119)',
                'teal-400': 'rgb(13, 148, 136)',
                'rose-400': 'rgb(225, 29, 72)',
                'red-400': 'rgb(220, 38, 38)',
                'yellow-400': 'rgb(202, 138, 4)',
                'amber-400': 'rgb(217, 119, 6)'
            } : {
                'orange-400': 'rgb(251, 146, 60)',
                'green-400': 'rgb(74, 222, 128)',
                'blue-400': 'rgb(96, 165, 250)',
                'cyan-400': 'rgb(34, 211, 238)',
                'purple-400': 'rgb(168, 85, 247)',
                'pink-400': 'rgb(236, 72, 153)',
                'teal-400': 'rgb(45, 212, 191)',
                'rose-400': 'rgb(251, 113, 133)',
                'red-400': 'rgb(248, 113, 113)',
                'yellow-400': 'rgb(250, 204, 21)',
                'amber-400': 'rgb(251, 191, 36)'
            };
            return map[color] || 'rgb(156, 163, 175)';
        },
        getBgClass() {
            const base = this.color.split('-')[0];
            return `bg-${base}-500/15`;
        }
    }
};
