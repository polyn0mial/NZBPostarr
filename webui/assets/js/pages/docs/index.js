// docs.html and indexer-guides.html: the page's tabs come from its #doc-browser-tabs JSON.
import { createVuePage } from 'page-base';
import { docBrowser } from '../../components/doc-browser.js';

const tabs = JSON.parse(document.getElementById('doc-browser-tabs').textContent);

createVuePage(docBrowser(tabs));
