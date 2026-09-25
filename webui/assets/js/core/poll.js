// Managed polling for the page root: every timer stops when the page unmounts, a slow tick never
// overlaps the next one, and a hidden tab polls at most once a minute. Spread into the root's
// methods by page-base.js createVuePage; _intervals and _intervalMap are root data.
export const pollMethods = {
    // Managed Interval Helper
    startInterval(fn, ms) {
        const existing = this._intervalMap.get(fn);
        if (existing) {
            existing.cancelled = true;
            clearTimeout(existing.timerId);
            this._intervals = this._intervals.filter(handle => handle !== existing);
        }

        const handle = {
            cancelled: false,
            running: false,
            timerId: null,
        };

        const scheduleNext = () => {
            if (handle.cancelled) {
                return;
            }
            const delay = document.hidden ? Math.max(ms, 60000) : ms;
            handle.timerId = setTimeout(() => {
                void tick();
            }, delay);
        };

        const tick = async () => {
            if (handle.cancelled || handle.running) {
                return;
            }
            if (document.hidden) {
                scheduleNext();
                return;
            }

            handle.running = true;
            try {
                await fn.call(this);
            } finally {
                handle.running = false;
                scheduleNext();
            }
        };

        scheduleNext();
        this._intervalMap.set(fn, handle);
        this._intervals.push(handle);
        return handle;
    },

    // Managed one-shot timer for delayed refreshes that must not outlive a page.
    startTimeout(fn, ms) {
        const handle = {
            cancelled: false,
            running: false,
            timerId: null,
        };
        const removeHandle = () => {
            this._intervals = this._intervals.filter(candidate => candidate !== handle);
        };
        handle.timerId = setTimeout(async () => {
            if (handle.cancelled || this._isUnmounting) {
                removeHandle();
                return;
            }
            handle.running = true;
            try {
                await fn.call(this);
            } finally {
                handle.running = false;
                handle.cancelled = true;
                removeHandle();
            }
        }, ms);
        this._intervals.push(handle);
        return handle;
    },
};

// Cancel every managed timer of `vm` (called from the root's beforeUnmount).
export function stopPolling(vm) {
    vm._intervals.forEach(handle => {
        if (handle && typeof handle === 'object') {
            handle.cancelled = true;
            clearTimeout(handle.timerId);
            return;
        }
        clearTimeout(handle);
    });
    vm._intervals = [];
    vm._intervalMap.clear();
}
