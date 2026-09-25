import { reactive } from 'vue';

// Highest p98 seen per peakKey this page session, so a chart keeps its scale when a burst
// scrolls out of the window. Shared by every sparkline that names the same peakKey.
const sessionPeaks = reactive({});

export function getSparklineStyle(item, data, color, peakKey = null) {
    const val = typeof item === 'object' ? item.v : item;
    if (!data || data.length === 0) return { height: '4%', backgroundColor: color, opacity: 0.85 };

    const values = data.map(d => typeof d === 'object' ? d.v : d).filter(v => typeof v === 'number');
    if (values.length === 0) return { height: '4%', backgroundColor: color, opacity: 0.85 };

    const sorted = [...values].sort((a, b) => a - b);
    const p98 = sorted[Math.floor(sorted.length * 0.98)] || 0.1;

    let scaleMax = p98;
    if (peakKey) {
        if (!sessionPeaks[peakKey] || p98 > sessionPeaks[peakKey]) {
            sessionPeaks[peakKey] = p98;
        }
        scaleMax = sessionPeaks[peakKey];
    }

    const height = Math.min(100, Math.max(4, (val / scaleMax) * 100));
    return { height: `${height}%`, backgroundColor: color, opacity: 0.85 };
}

export function getSparklineTitle(item, index, data, suffix = '') {
    const val = typeof item === 'object' ? item.v : item;
    const secsAgo = (data.length - 1 - index) * 1;
    const timeLabel = secsAgo === 0 ? 'now' : secsAgo < 60 ? `${secsAgo}s ago` : `${Math.round(secsAgo / 60)}m ago`;
    const displayVal = typeof val === 'number' ? val.toFixed(1) : val;
    return `${displayVal}${suffix} (${timeLabel})`;
}

export const Sparkline = {
    props: {
        data: Array,
        colorRgb: String,
        peakKey: String,
        valueSuffix: { type: String, default: '' },
        height: { type: String, default: 'h-10' }
    },
    template: `
        <div v-if="data && data.length" :class="[height, 'flex items-end gap-px']">
            <div v-for="(v, i) in data" :key="v.id" 
                class="flex-1 rounded-sm cursor-pointer transition-[height] duration-300 infotip-trigger relative" 
                :style="barStyle(v)">
                <div class="infotip-content">
                    {{ barTitle(v, i) }}
                    <div class="infotip-arrow"></div>
                </div>
            </div>
        </div>
    `,
    methods: {
        barStyle(v) {
            return getSparklineStyle(v, this.data, this.colorRgb, this.peakKey);
        },
        barTitle(v, i) {
            return getSparklineTitle(v, i, this.data, this.valueSuffix);
        }
    }
};
