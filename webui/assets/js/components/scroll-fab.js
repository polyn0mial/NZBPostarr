// Mobile scroll FAB: jumps to the Job Staging Area, and back to the top once that
// section is in view. The button lives inside #app, so this must run after Vue has
// mounted the page: the elements it binds are the ones Vue rendered.

const TITLE_TO_TARGET = 'Jump to Job Staging Area';
const TITLE_TO_TOP = 'Back to top';

export const scrollFab = {
    rootId: 'scroll-fab-btn',

    init(fab) {
        const target = document.getElementById('upload-queue-section');
        const iconDn = document.getElementById('fab-icon-queue');
        const iconUp = document.getElementById('fab-icon-top');
        if (!fab || !target) return;
        let inView = false;
        const obs = new IntersectionObserver(
            (entries) => {
                inView = entries[0].isIntersecting;
                fab.title = inView ? TITLE_TO_TOP : TITLE_TO_TARGET;
                fab.ariaLabel = fab.title;
                iconDn.style.display = inView ? 'none' : '';
                iconUp.style.display = inView ? '' : 'none';
            },
            { threshold: 0.15 },
        );
        obs.observe(target);
        fab.addEventListener('click', () => {
            if (inView) {
                window.scrollTo({ top: 0, behavior: 'smooth' });
            } else {
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    },
};
