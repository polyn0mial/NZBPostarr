// Page components: plain DOM widgets that live outside a page's Vue app logic.
// Each registered component names the id of its root element; startPageComponents()
// initialises every component whose root is on the page. Call it after the page's
// Vue app has mounted, since the roots sit inside #app and Vue replaces that DOM.
import { scrollFab } from '../components/scroll-fab.js';

const COMPONENTS = [scrollFab];

function initPresent() {
    for (const component of COMPONENTS) {
        const root = document.getElementById(component.rootId);
        if (root) component.init(root);
    }
}

export function startPageComponents() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPresent);
    } else {
        initPresent();
    }
}
