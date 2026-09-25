import {
    categoryMeta,
    hexToRgba,
    getDefaultCategoryHex,
    loadCategoryAppearanceState,
    saveCategoryAppearanceState,
    getCategoryAppearanceEntry,
} from 'page-base';

// Settings page methods: category colors. Spread into the page's methods by index.js.
export const categoryColorsMethods = {
    loadCategoryAppearanceLibrary(initialProfiles = null) {
        const state = loadCategoryAppearanceState();
        const profiles = initialProfiles && typeof initialProfiles === 'object'
            ? initialProfiles
            : (this.settings.ui.category_appearance_profiles || {});
        Object.entries(profiles || {}).forEach(([catId, value]) => {
            if (!state.categories[catId]) state.categories[catId] = {};
            if (value && typeof value === 'object') {
                if (value.color) state.categories[catId].color = value.color;
                if (Array.isArray(value.saves)) state.categories[catId].saves = value.saves;
                if (value.selected_save_id) state.categories[catId].selected_save_id = value.selected_save_id;
            }
        });
        this.categoryAppearanceState = saveCategoryAppearanceState(state);
        this.settings.ui.category_appearance_profiles = this.categoryAppearanceState.categories;
    },

    persistCategoryAppearanceLibrary() {
        this.categoryAppearanceState = saveCategoryAppearanceState(this.categoryAppearanceState);
        this.settings.ui.category_appearance_profiles = this.categoryAppearanceState.categories;
    },

    getCategoryAppearance(catId) {
        return getCategoryAppearanceEntry(catId, this.categoryAppearanceState);
    },

    categoryAppearancePreviewStyle(catId) {
        const color = this.getCategoryAppearance(catId).color;
        return {
            backgroundColor: hexToRgba(color, 0.16),
            color,
            borderColor: hexToRgba(color, 0.35),
        };
    },

    categoryAppearanceSwatchStyle(catId) {
        const color = this.getCategoryAppearance(catId).color;
        return {
            backgroundColor: hexToRgba(color, 0.16),
        };
    },

    categoryAppearanceIconStyle(catId) {
        return { color: this.getCategoryAppearance(catId).color };
    },

    setCategoryAppearanceColor(catId, color) {
        if (!this.categoryAppearanceState.categories[catId]) {
            this.categoryAppearanceState.categories[catId] = {};
        }
        this.categoryAppearanceState.categories[catId].color = color || getDefaultCategoryHex(catId);
        this.categoryAppearanceState.categories[catId].selected_save_id = '';
        this.persistCategoryAppearanceLibrary();
    },

    saveCategoryAppearancePreset(catId) {
        const entry = this.getCategoryAppearance(catId);
        const bucket = this.categoryAppearanceState.categories[catId] || (this.categoryAppearanceState.categories[catId] = {});
        const saves = Array.isArray(bucket.saves) ? bucket.saves.slice() : [];
        const nextNumber = saves.length + 1;
        const preset = {
            id: `${catId}-${Date.now()}`,
            name: `Saved ${nextNumber}`,
            color: entry.color,
            created_at: new Date().toISOString(),
        };
        saves.push(preset);
        bucket.saves = saves;
        bucket.selected_save_id = preset.id;
        this.persistCategoryAppearanceLibrary();
        this.showToast('success', 'Color Saved', `${categoryMeta[catId]?.label || catId} color saved as ${preset.name}.`);
    },

    applyCategoryAppearancePreset(catId, presetId) {
        if (!presetId) {
            const bucket = this.categoryAppearanceState.categories[catId] || (this.categoryAppearanceState.categories[catId] = {});
            bucket.selected_save_id = '';
            this.persistCategoryAppearanceLibrary();
            return;
        }
        const entry = this.getCategoryAppearance(catId);
        const preset = entry.saves.find((save) => save.id === presetId);
        if (!preset) return;
        const bucket = this.categoryAppearanceState.categories[catId] || (this.categoryAppearanceState.categories[catId] = {});
        bucket.color = preset.color;
        bucket.selected_save_id = preset.id;
        this.persistCategoryAppearanceLibrary();
    },

    deleteCategoryAppearancePreset(catId, presetId) {
        const bucket = this.categoryAppearanceState.categories[catId];
        if (!bucket || !Array.isArray(bucket.saves)) return;
        bucket.saves = bucket.saves.filter((save) => save.id !== presetId);
        if (bucket.selected_save_id === presetId) {
            bucket.selected_save_id = '';
        }
        this.persistCategoryAppearanceLibrary();
    },

    deleteSelectedCategoryAppearancePreset(catId) {
        const entry = this.getCategoryAppearance(catId);
        if (!entry.selected_save_id) return;
        this.deleteCategoryAppearancePreset(catId, entry.selected_save_id);
    },

    resetCategoryAppearance(catId) {
        const bucket = this.categoryAppearanceState.categories[catId] || (this.categoryAppearanceState.categories[catId] = {});
        bucket.color = getDefaultCategoryHex(catId);
        bucket.selected_save_id = '';
        this.persistCategoryAppearanceLibrary();
    },
};
