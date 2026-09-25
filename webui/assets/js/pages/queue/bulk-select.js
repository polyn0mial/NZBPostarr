// Queue page: The Bulk Select menu: pick categories, select every matching visible row.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

export default {
  computed: {
    bulkSelectLabel() {
      const selected = this.normalizedBulkSelectCategories;
      if (!selected.length) return "Bulk Select";
      if (selected.includes("all")) return "All Uploadable";
      if (selected.length === 1) {
        const hit = this.bulkSelectCategories.find((cat) => cat.value === selected[0]);
        return hit ? hit.label : selected[0];
      }
      return `${selected.length} Categories`;
    },

    normalizedBulkSelectCategories() {
      const raw = Array.isArray(this.bulkSelectCategoriesSelected) ? this.bulkSelectCategoriesSelected : [];
      const unique = Array.from(new Set(raw)).filter(Boolean);
      if (unique.includes("all")) return ["all"];
      const valid = new Set(this.bulkSelectCategories.map((cat) => cat.value));
      return unique.filter((v) => valid.has(v));
    },

    bulkSelectSelectedSet() {
      return new Set(this.normalizedBulkSelectCategories);
    },

    bulkSelectCategories() {
      const selected = new Set(Array.isArray(this.bulkSelectCategoriesSelected) ? this.bulkSelectCategoriesSelected.filter(Boolean) : []);
      return this.availableCategories.filter((cat) => cat.value !== "all" && Number(cat.count || 0) > 0).map((cat) => ({
        ...cat,
        checked: selected.has(cat.value)
      }));
    },
  },
  methods: {
    openBulkSelect(event) {
      const rect = (event.target.closest("button") || event.target).getBoundingClientRect();
      this.bulkSelectPos = { x: rect.left, y: rect.bottom + 4 };
      this.bulkSelectOpen = !this.bulkSelectOpen;
      this.categoryFilterOpen = false;
      this.filterModeOpen = false;
    },

    _normalizeBulkSelectCategories() {
      const values = Array.isArray(this.bulkSelectCategoriesSelected) ? Array.from(new Set(this.bulkSelectCategoriesSelected.filter(Boolean))) : [];
      if (values.includes("all")) {
        this.bulkSelectCategoriesSelected = ["all"];
        return;
      }
      const valid = new Set(this.bulkSelectCategories.map((cat) => cat.value));
      this.bulkSelectCategoriesSelected = values.filter((v) => valid.has(v));
    },

    _bulkSelectMatchesCategory(item, value, sectionCategory = null) {
      if (!item) return false;
      if (value === "all") return true;
      if (typeof value === "string" && value.startsWith("itype:")) {
        return (item.itype || "") === value.slice(6);
      }
      const resolvedCategory = this.getCategoryForItem(item);
      if (resolvedCategory && resolvedCategory === value) return true;
      if (!resolvedCategory && sectionCategory && sectionCategory === value) return true;
      const detectedCategory = (item.detected_category || "").toString().toLowerCase();
      return detectedCategory === value;
    },

    collectBulkSelectableEntries(valueOrValues) {
      const entries = [];
      const seen = new Set();
      const values = Array.isArray(valueOrValues) ? valueOrValues : [valueOrValues];
      const normalizedValues = Array.from(new Set(values.filter(Boolean)));
      if (normalizedValues.length === 0) return entries;
      const pushItem = (item, sectionCategory = null) => {
        if (!item || !item.key || seen.has(item.key)) return;
        if (!this.isAutoSelectable(item) || this.isItemCompleted(item)) return;
        if (!normalizedValues.some((value) => this._bulkSelectMatchesCategory(item, value, sectionCategory))) return;
        seen.add(item.key);
        entries.push({ item, categoryOverride: sectionCategory && sectionCategory !== "external" ? sectionCategory : null });
      };
      for (const cat of this.flatCategories) {
        const list = this.items[cat.id] || [];
        for (const item of list) {
          pushItem(item, cat.id);
        }
      }
      for (const group of this.items.external || []) {
        for (const item of group.items || []) {
          const walk = (node) => {
            if (!node) return;
            pushItem(node, null);
            (node.children || []).forEach(walk);
          };
          walk(item);
        }
      }
      return entries;
    },

    selectAllBulkSelectCategories() {
      const values = this.bulkSelectCategories.map((cat) => cat.value);
      this.bulkSelectCategoriesSelected = values.includes("all") ? ["all"] : values;
      this._normalizeBulkSelectCategories();
    },

    clearBulkSelectSelection() {
      this.bulkSelectCategoriesSelected = [];
    },

    isBulkSelectCategoryChecked(value) {
      return this.bulkSelectSelectedSet.has(value);
    },

    toggleBulkSelectCategory(value, checked) {
      const next = new Set((this.bulkSelectCategoriesSelected || []).filter((v) => v !== "all"));
      if (value === "all") {
        this.bulkSelectCategoriesSelected = checked ? ["all"] : [];
        this._normalizeBulkSelectCategories();
        return;
      }
      if (checked) next.add(value);
      else next.delete(value);
      this.bulkSelectCategoriesSelected = Array.from(next);
      this._normalizeBulkSelectCategories();
    },

    applyBulkSelectSelection(action = "select") {
      const values = this.normalizedBulkSelectCategories;
      if (values.length === 0) {
        this.showToast("warning", "No Categories", "Select one or more categories first");
        this.bulkSelectOpen = false;
        return;
      }
      const entries = this.collectBulkSelectableEntries(values);
      if (entries.length === 0) {
        this.showToast("warning", "No Items", "No uploadable items were found for the selected categories");
        this.bulkSelectOpen = false;
        return;
      }
      const nextSet = /* @__PURE__ */ new Set();
      const nextMeta = /* @__PURE__ */ new Map();
      for (const entry of entries) {
        const key = entry.item.key;
        nextSet.add(key);
        nextMeta.set(key, this._buildSelectionMeta(entry.item, entry.categoryOverride));
      }
      this.selectedItems = nextSet;
      this.selectedMeta = nextMeta;
      this.bulkSelectOpen = false;
      const label = values.includes("all") ? "All Uploadable" : values.length === 1 ? this.bulkSelectCategories.find((cat) => cat.value === values[0])?.label || values[0] : `${values.length} categories`;
      if (action === "stage") {
        this.showToast("success", "Selected", `${entries.length} uploadable item(s) selected from ${label}`);
        void this.$nextTick(() => this.addToQueue());
        return;
      }
      if (action === "force") {
        this.showToast("success", "Selected", `${entries.length} uploadable item(s) selected from ${label}`);
        void this.$nextTick(() => this.openForceUploadMenu("bulk", { target: this.$refs.bulkSelectButton || null }));
        return;
      }
      this.showToast("success", "Selected", `${entries.length} uploadable item(s) selected from ${label}`);
    },
  },
};
