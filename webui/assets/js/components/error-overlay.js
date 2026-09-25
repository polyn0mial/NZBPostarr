// Queue page error overlay (queue-ui-03): a classic script loaded before the page bundle, so a
// bootstrap or runtime error still shows on screen and reaches the server log through the
// /queue-error-beacon route. It also uncloaks #app if the page never mounted (loader watchdog).
// pages/queue/index.js calls window.__queueShowOverlay when createVuePage throws.
// The build id comes from the script tag: <script src="..." data-build="[[ cache_bust ]]">.
(function () {
    var script = document.currentScript;
    var BUILD = (script && script.dataset.build) || '';
    function beaconQueueError(title, detail) {
        try {
            var message = String(title || 'Queue error');
            var extra = String(detail || '').slice(0, 1500);
            var url = '/queue-error-beacon?title=' + encodeURIComponent(message) + '&detail=' + encodeURIComponent(extra) + '&rev=' + encodeURIComponent(BUILD);
            var img = new Image();
            img.src = url;
        } catch (_err) {}
    }
    function uncloakApp() {
        var app = document.getElementById('app');
        if (app && app.hasAttribute('v-cloak')) {
            app.removeAttribute('v-cloak');
        }
    }
    function ensureOverlay() {
        var existing = document.getElementById('queue-error-overlay');
        if (existing) return existing;
        var overlay = document.createElement('div');
        overlay.id = 'queue-error-overlay';
        overlay.style.cssText = [
            'display:none',
            'position:fixed',
            'right:16px',
            'bottom:16px',
            'width:min(28rem, calc(100vw - 32px))',
            'max-height:45vh',
            'z-index:99999',
            'background:rgba(15,23,42,0.92)',
            'border:1px solid rgba(248,113,113,0.45)',
            'border-radius:14px',
            'padding:16px',
            'color:#f8fafc',
            'font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace',
            'white-space:pre-wrap',
            'overflow:auto',
            'box-shadow:0 20px 60px rgba(0,0,0,0.45)'
        ].join(';');
        document.addEventListener('DOMContentLoaded', function () {
            document.body.appendChild(overlay);
        }, { once: true });
        return overlay;
    }
    function showOverlay(title, detail) {
        var overlay = ensureOverlay();
        overlay.textContent = title + '\n\n' + detail + '\n\nQueue build: ' + BUILD;
        overlay.style.display = 'block';
        uncloakApp();
        beaconQueueError(title, detail);
    }
    window.__queueShowOverlay = showOverlay;
    window.addEventListener('error', function (event) {
        var source = event && event.filename ? String(event.filename) : 'inline';
        showOverlay('Queue page frontend error', (event.message || 'Unknown error') + '\n' + source + ':' + (event.lineno || 0) + ':' + (event.colno || 0));
    });
    window.addEventListener('unhandledrejection', function (event) {
        var reason = event && event.reason;
        var detail = reason && (reason.stack || reason.message) ? (reason.stack || reason.message) : String(reason || 'Unknown rejection');
        var lowered = String(detail || '').toLowerCase();
        if (lowered.indexOf('aborterror') !== -1 || lowered.indexOf('failed to fetch') !== -1 || lowered.indexOf('load failed') !== -1) {
            beaconQueueError('Queue page transient rejection', detail);
            return;
        }
        showOverlay('Queue page unhandled rejection', detail);
    });
    window.setTimeout(function () {
        var app = document.getElementById('app');
        if (app && app.hasAttribute('v-cloak')) {
            uncloakApp();
            beaconQueueError('Queue page loader timeout', 'The queue app was still cloaked after the loader watchdog expired.');
        }
    }, 20000);
})();
