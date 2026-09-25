// Settings page methods: tv pack ignore. Spread into the page's methods by index.js.
export const tvPackIgnoreMethods = {
    defaultTvPackIgnore() {
        return {
            enabled: true,
            ignore_non_episode: true,
            require_episode: true,
            require_resolution: false,
            require_source: true,
        };
    },

    ensureTvPackIgnore() {
        if (!this.settings.processing.tv_pack_ignore) {
            this.settings.processing.tv_pack_ignore = this.defaultTvPackIgnore();
        }
        return this.settings.processing.tv_pack_ignore;
    },

    tvPackIgnoreOn(key = 'enabled') {
        const rules = this.ensureTvPackIgnore();
        return !!rules[key];
    },

    toggleTvPackIgnore(key = 'enabled') {
        const rules = this.ensureTvPackIgnore();
        rules[key] = !rules[key];
    },

    isTvPackRuleExpanded(ruleKey) {
        return !!this.expandedTvPackRuleExamples[ruleKey];
    },

    toggleTvPackRuleExpanded(ruleKey) {
        this.expandedTvPackRuleExamples[ruleKey] = !this.expandedTvPackRuleExamples[ruleKey];
    },
};
