// The doc browser behind docs.html and indexer-guides.html: tabs of sections, a grouped
// sidebar, prev/next, search across every tab, #section deep links and a Markdown export
// of the current tab. Each page supplies its own tabs array; the markup stays in the page.

function groupSections(sections) {
    const groups = [];
    const seen = {};
    for (const s of sections) {
        if (!seen[s.group]) {
            seen[s.group] = { label: s.group, items: [] };
            groups.push(seen[s.group]);
        }
        seen[s.group].items.push(s);
    }
    return groups;
}

function tableToMarkdown(table) {
    const rows = [...table.querySelectorAll('tr')];
    if (!rows.length) return null;
    const header = [...rows[0].querySelectorAll('th,td')].map((c) => c.textContent.trim());
    let tbl = '| ' + header.join(' | ') + ' |\n';
    tbl += '| ' + header.map(() => '---').join(' | ') + ' |\n';
    for (const row of rows.slice(1)) {
        const cells = [...row.querySelectorAll('td,th')].map((c) => c.textContent.trim());
        tbl += '| ' + cells.join(' | ') + ' |\n';
    }
    return tbl;
}

// The rendered .doc-section as Markdown.
function sectionToMarkdown(sec) {
    const clone = sec.cloneNode(true);
    clone.querySelectorAll('lucide-icon').forEach((i) => i.remove());
    clone.querySelectorAll('.idx-badge, .doc-badge').forEach((b) => {
        b.textContent = b.textContent.trim();
    });

    // Convert tables
    clone.querySelectorAll('table').forEach((t) => {
        const tbl = tableToMarkdown(t);
        if (tbl === null) return;
        const pre = document.createElement('pre');
        pre.textContent = tbl;
        t.replaceWith(pre);
    });

    // Convert code blocks
    clone.querySelectorAll('.doc-pre, pre').forEach((p) => {
        if (p.tagName === 'PRE' && !p.querySelector('code') && !p.classList.contains('doc-pre')) return;
        p.textContent = '```\n' + p.textContent.trim() + '\n```';
    });

    // Convert inline code
    clone.querySelectorAll('.doc-code, code').forEach((c) => {
        if (c.closest('.doc-pre, pre')) return;
        c.textContent = '`' + c.textContent.trim() + '`';
    });

    // Walk & build markdown
    let text = '';
    for (const el of clone.children) {
        const tag = el.tagName?.toLowerCase();
        const cls = el.className || '';
        if (cls.includes('doc-h2')) text += '\n## ' + el.textContent.trim() + '\n\n';
        else if (cls.includes('doc-h3')) text += '\n### ' + el.textContent.trim() + '\n\n';
        else if (cls.includes('doc-pre') || tag === 'pre') text += '\n```\n' + el.textContent.trim() + '\n```\n\n';
        else if (cls.includes('doc-callout')) text += '\n> ' + el.textContent.trim().replace(/\n/g, '\n> ') + '\n\n';
        else text += el.textContent.trim() + '\n\n';
    }
    return text;
}

// Vue page options for createVuePage. tabs: [{ id, label, icon?, sections: [{ id, title,
// group, keywords, ... }] }]; the first section of the first tab is the landing section.
export function docBrowser(tabs) {
    return {
        data() {
            return {
                activeTab: tabs[0].id,
                activeSection: tabs[0].sections[0].id,
                searchQuery: '',
                searchFocused: false,
                tabs,
            };
        },
        watch: {
            activeSection(val) {
                if (window.location.hash !== '#' + val) {
                    window.history.pushState(null, '', '#' + val);
                }
            },
        },
        computed: {
            currentTab() {
                return this.tabs.find((t) => t.id === this.activeTab);
            },
            currentTabSections() {
                return this.currentTab ? this.currentTab.sections : [];
            },
            currentTabGroups() {
                return groupSections(this.currentTabSections);
            },
            currentIndex() {
                return this.currentTabSections.findIndex((s) => s.id === this.activeSection);
            },
            prevSection() {
                return this.currentIndex > 0 ? this.currentTabSections[this.currentIndex - 1] : null;
            },
            nextSection() {
                return this.currentIndex < this.currentTabSections.length - 1 ? this.currentTabSections[this.currentIndex + 1] : null;
            },
            searchResults() {
                if (!this.searchQuery || this.searchQuery.length < 2) return [];
                const q = this.searchQuery.toLowerCase();
                const results = [];
                for (const tab of this.tabs) {
                    for (const s of tab.sections) {
                        if (s.title.toLowerCase().includes(q) || s.keywords.toLowerCase().includes(q)) {
                            results.push({ id: s.id, title: s.title, group: s.group, tab: tab.id, tabLabel: tab.label });
                        }
                    }
                }
                return results.slice(0, 10);
            },
        },
        methods: {
            goToSection(tabId, sectionId) {
                this.activeTab = tabId;
                this.activeSection = sectionId;
                this.searchQuery = '';
                this.searchFocused = false;
                window.scrollTo({ top: 0, behavior: 'smooth' });
            },
            async downloadMarkdown() {
                const origSection = this.activeSection;
                let md = `# NZBPostarr - ${this.currentTab.label}\n\n`;

                for (const s of this.currentTab.sections) {
                    this.activeSection = s.id;
                    await this.$nextTick();

                    const sec = document.querySelector('.doc-section');
                    if (!sec) continue;
                    md += sectionToMarkdown(sec);
                }

                // Restore original section
                this.activeSection = origSection;
                await this.$nextTick();

                const blob = new Blob([md], { type: 'text/markdown' });
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = `nzbpostarr-${this.activeTab}.md`;
                a.click();
                URL.revokeObjectURL(a.href);
            },
        },
        mounted() {
            // Priority 1: Check URL search params (legacy support)
            const params = new URLSearchParams(window.location.search);
            const tabParam = params.get('tab');
            if (tabParam) {
                const found = this.tabs.find((t) => t.id === tabParam);
                if (found) {
                    this.activeTab = tabParam;
                    this.activeSection = found.sections[0].id;
                }
                // Clean search params but keep hash if present
                window.history.replaceState({}, '', window.location.pathname + window.location.hash);
            }

            // Priority 2: Check URL hash for direct section linking
            const checkHash = () => {
                const hash = window.location.hash.substring(1);
                if (hash) {
                    for (const t of this.tabs) {
                        const sec = t.sections.find((s) => s.id === hash);
                        if (sec) {
                            this.activeTab = t.id;
                            this.activeSection = hash;
                            // Ensure page scrolls to top when switching sections via direct link
                            window.scrollTo({ top: 0, behavior: 'smooth' });
                            return true;
                        }
                    }
                }
                return false;
            };

            // Run on load and whenever hash changes
            checkHash();
            window.addEventListener('hashchange', checkHash);
        },
    };
}
