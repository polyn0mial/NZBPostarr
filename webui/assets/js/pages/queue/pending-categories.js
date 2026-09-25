// Queue page: Categories: the category filter, per-row category resolution, pills and colours.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.
import { queueCategoryMeta as categoryMeta, categoryLabel, itypeToCategory as sharedItypeToCategory, categoryToItype } from "page-base";

export default {
  computed: {
    normalizedSelectedCategories() {
      const raw = Array.isArray(this.selectedCategories) ? this.selectedCategories : [];
      const unique = Array.from(new Set(raw)).filter(Boolean);
      if (unique.length === 0 || unique.includes("all")) return ["all"];
      if (!this.categorySelectionReady) return unique;
      const valid = new Set(this.availableCategories.map((c2) => c2.value));
      const filtered = unique.filter((v2) => valid.has(v2));
      if (filtered.length === 0) return valid.has("all") ? ["all"] : [];
      return filtered;
    },

    selectedCategorySet() {
      return new Set(this.normalizedSelectedCategories);
    },

    categoryFilterLabel() {
      const selected = this.normalizedSelectedCategories;
      if (selected.length === 0) return "No Categories";
      if (selected.includes("all")) return "All Categories";
      if (selected.length === 1) {
        const hit = this.availableCategories.find((c2) => c2.value === selected[0]);
        return hit ? hit.label : selected[0];
      }
      return `${selected.length} Categories`;
    },

    isMoviesEnabled() {
      return true;
    },

    availableCategories() {
      const cats = [{ value: "all", label: "All Categories" }];
      const presentCategories = this._collectPresentCategories(this.items);
      const categoryOptions = /* @__PURE__ */ new Map();
      const folderCatIds = /* @__PURE__ */ new Set();
      if (presentCategories.has("tv")) {
        categoryOptions.set(categoryMeta.tv.id, { value: categoryMeta.tv.id, label: categoryMeta.tv.label });
        folderCatIds.add(categoryMeta.tv.id);
      }
      this.flatCategories.forEach((c2) => {
        if (c2.id === "movies" && !this.isMoviesEnabled) return;
        categoryOptions.set(c2.id, { value: c2.id, label: c2.label });
        folderCatIds.add(c2.id);
      });
      const fallbackCategoryLabels = {
        movies: categoryMeta.movies?.label || "Movies",
        tv: categoryMeta.tv?.label || "TV",
        anime: categoryMeta.anime?.label || "Anime",
        disc: categoryMeta.disc?.label || "DISC",
        music: categoryMeta.music?.label || "Music",
        books: categoryMeta.books?.label || "Books",
        ebooks: "Ebooks",
        audiobooks: "Audiobooks",
        apps: categoryMeta.apps?.label || "Apps",
        misc: categoryMeta.misc?.label || "Misc"
      };
      for (const category of ["movies", "tv", "anime", "disc", "books", "ebooks", "music", "apps", "misc"]) {
        if (category === "movies" && !this.isMoviesEnabled) continue;
        if (!categoryOptions.has(category)) {
          categoryOptions.set(category, {
            value: category,
            label: fallbackCategoryLabels[category] || categoryLabel(category)
          });
        }
      }
      for (const category of presentCategories) {
        if (!category || category === "external" || categoryOptions.has(category)) continue;
        categoryOptions.set(category, {
          value: category,
          label: fallbackCategoryLabels[category] || categoryLabel(category)
        });
      }
      const ITYPE_CATS = [
        { itype: "Anime", label: "Anime", catId: "anime", value: "itype:Anime" },
        { itype: "Audiobook", label: "Audiobooks", catId: "audiobooks", value: "itype:Audiobook" },
        { itype: "Ebook", label: "Ebooks", catId: "ebooks", value: "itype:Ebook" },
        { itype: "Music", label: "Music", catId: "music", value: "itype:Music" }
      ];
      const presentItypes = this._collectPresentItypes(this.items);
      ITYPE_CATS.forEach((ic2) => {
        if (presentItypes.has(ic2.itype) && !folderCatIds.has(ic2.catId)) {
          categoryOptions.set(ic2.value, { value: ic2.value, label: ic2.label });
        }
      });
      const withCounts = [...cats, ...categoryOptions.values()].map((cat) => ({
        ...cat,
        count: this.getAvailableCategoryCount(cat.value)
      }));
      const all = withCounts.filter((c2) => c2.value === "all");
      const rest = withCounts.filter((c2) => c2.value !== "all" && Number(c2.count || 0) > 0).sort((a2, b2) => a2.label.localeCompare(b2.label));
      return [...all, ...rest];
    },

    flatCategories() {
      if (this.categories.length > 0) {
        const byId = new Map(this.categories.filter((c2) => c2.id !== "tv").map((c2) => [c2.id, c2]));
        for (const [category, value] of Object.entries(this.items || {})) {
          if (!Array.isArray(value) || value.length === 0) continue;
          if (!category || category === "tv" || category === "external" || byId.has(category)) continue;
          const meta = categoryMeta[category];
          byId.set(category, {
            id: category,
            label: meta?.label || categoryLabel(category),
            icon: meta?.icon || "folder",
            color: meta?.color || "gray"
          });
        }
        return Array.from(byId.values());
      }
      return [
        { id: categoryMeta.movies.id, label: categoryMeta.movies.label, icon: categoryMeta.movies.icon, color: categoryMeta.movies.color },
        { id: categoryMeta.misc.id, label: categoryMeta.misc.label, icon: categoryMeta.misc.icon, color: categoryMeta.misc.color }
      ];
    },

    allCategoryMeta() {
      const internal = new Set(["external", "both"]);
      return Object.values(categoryMeta).filter((c2) => !internal.has(c2.id));
    },
  },
  methods: {
    selectAllCategories() {
      this.selectedCategories = ["all"];
    },

    clearCategorySelection() {
      this.selectedCategories = ["all"];
    },

    isCategoryChecked(value) {
      if (value === "all") return this.selectedCategorySet.has("all");
      return this.selectedCategorySet.has(value);
    },

    toggleCategorySelection(value, checked) {
      if (value === "all") {
        this.selectedCategories = ["all"];
        return;
      }
      const next = new Set(this.selectedCategories || []);
      next.delete("all");
      if (checked) next.add(value);
      else next.delete(value);
      this.selectedCategories = next.size > 0 ? Array.from(next) : ["all"];
    },

    _normalizeSelectedCategories() {
      const normalized = this.normalizedSelectedCategories;
      const current = Array.isArray(this.selectedCategories) ? Array.from(new Set(this.selectedCategories.filter(Boolean))) : [];
      if (normalized.join("|") !== current.join("|")) {
        this.selectedCategories = normalized;
      }
    },

    itemMatchesCategorySelection(item, sectionCategory) {
      if (this.selectedCategorySet.has("all")) return true;
      const resolvedCategory = this.getCategoryForItem(item);
      if (resolvedCategory && this.selectedCategorySet.has(resolvedCategory)) return true;
      if (!resolvedCategory && sectionCategory && this.selectedCategorySet.has(sectionCategory)) return true;
      if (item && item.itype && this.selectedCategorySet.has(`itype:${item.itype}`)) return true;
      return false;
    },

    getAvailableCategoryCount(value) {
      if (value === "all") {
        return Number(this.summary?.total || 0);
      }
      const items = this.items || {};
      let count = 0;
      for (const [sectionCategory, sectionItems] of Object.entries(items)) {
        if (sectionCategory === "external" || !Array.isArray(sectionItems)) continue;
        count += sectionItems.filter((item) => this._itemMatchesCategoryValue(item, sectionCategory, value)).length;
      }
      for (const group of items.external || []) {
        count += (group.items || []).filter((item) => this._externalItemMatchesCategoryValue(item, value)).length;
      }
      return count;
    },

    _itemMatchesCategoryValue(item, sectionCategory, value) {
      if (!item) return false;
      const resolvedCategory = this.getCategoryForItem(item);
      if (value === "all") return true;
      if (resolvedCategory && value === resolvedCategory) return true;
      if (!resolvedCategory && sectionCategory && value === sectionCategory) return true;
      if (typeof value === "string" && value.startsWith("itype:")) {
        return item.itype === value.slice(6);
      }
      return false;
    },

    _externalItemMatchesCategoryValue(item, value, visited = /* @__PURE__ */ new WeakSet()) {
      if (!item || typeof item !== "object") return false;
      if (visited.has(item)) return false;
      visited.add(item);
      if (this._itemMatchesCategoryValue(item, "external", value)) return true;
      const children = Array.isArray(item.children) ? item.children : [];
      return children.some((child) => this._externalItemMatchesCategoryValue(child, value, visited));
    },

    _applyDetectedCategories() {
      const groups = this.items.external || [];
      const manual = { ...this.manualExternalCategories || {} };
      const nextCategories = {};
      const nextManual = {};
      const visitItem = (item) => {
        if (!item || !item.key) return;
        const manualCategory = manual[item.key];
        if (manualCategory) {
          nextManual[item.key] = manualCategory;
        }
        nextCategories[item.key] = manualCategory || this.serverCategoryForItem(item);
        for (const child of item.children || []) {
          visitItem(child);
        }
      };
      for (const group of groups) {
        for (const item of group.items || []) {
          visitItem(item);
        }
      }
      this.manualExternalCategories = nextManual;
      this.externalCategories = nextCategories;
    },

    seriesSignature(value) {
      const normalized = String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
      const seriesOnly = normalized
        .replace(/[_.\s]*S\d{1,2}[._\s-]*E\d{1,3}.*/gi, "")
        .replace(/[_.\s]*\b(?:19|20)\d{2}\b.*/g, "");
      return seriesOnly.replace(/[^a-z0-9]+/gi, " ").trim().toLowerCase();
    },

    /**
     * Collect all unique `itype` strings present anywhere in the items tree.
     * Used to build the synthetic itype-based filter entries in availableCategories.
     */
    _collectPresentItypes(items) {
      const itypes = /* @__PURE__ */ new Set();
      if (!items) return itypes;
      const visited = /* @__PURE__ */ new WeakSet();
      const visitExternalItem = (item) => {
        if (!item || typeof item !== "object") return;
        if (visited.has(item)) return;
        visited.add(item);
        if (item.itype) itypes.add(item.itype);
        for (const child of item.children || []) {
          visitExternalItem(child);
        }
      };
      for (const group of items.external || []) {
        for (const it2 of group.items || []) {
          visitExternalItem(it2);
        }
      }
      for (const [key, val] of Object.entries(items)) {
        if (key === "external" || !Array.isArray(val)) continue;
        for (const it2 of val) {
          if (it2.itype) itypes.add(it2.itype);
        }
      }
      return itypes;
    },

    _collectPresentCategories(items) {
      const categories = /* @__PURE__ */ new Set();
      if (!items) return categories;
      const visited = /* @__PURE__ */ new WeakSet();
      for (const [key, val] of Object.entries(items)) {
        if (key === "external" || !Array.isArray(val) || val.length === 0) continue;
        categories.add(key);
      }
      const visitExternalItem = (item) => {
        if (!item || typeof item !== "object") return;
        if (visited.has(item)) return;
        visited.add(item);
        const category = this.getCategoryForItem(item);
        if (category && category !== "external") categories.add(category);
        for (const child of item.children || []) {
          visitExternalItem(child);
        }
      };
      for (const group of items.external || []) {
        for (const item of group.items || []) {
          visitExternalItem(item);
        }
      }
      return categories;
    },

    /**
     * Map an itype string to the best matching upload category id.
     * Delegates to the shared itypeToCategory utility from page-base.
     */
    itypeToCategory(itype) {
      return sharedItypeToCategory(itype, this.flatCategories);
    },

    resolveUploadItype(itype, category) {
      if ((category || "").toString().toLowerCase() === "anime") return "Anime";
      return !itype || itype === "External" ? categoryToItype(category, "Misc") : itype;
    },

    serverCategoryForItem(item) {
      if (!item) return "";
      const normalizeCategory = (value) => {
        const raw = (value || "").toString().trim().toLowerCase();
        if (!raw || raw === "external") return "";
        if (/^tv\d*$/.test(raw) || /^series\d*$/.test(raw) || /^shows?\d*$/.test(raw)) return "tv";
        if (/^movies?\d*$/.test(raw) || /^films?\d*$/.test(raw) || raw === "movie") return "movies";
        if (/^anime\d*$/.test(raw)) return "anime";
        if (/^disc\d*$/.test(raw)) return "disc";
        if (/^ebooks?\d*$/.test(raw) || raw === "ebook") return "ebooks";
        if (/^books?\d*$/.test(raw) || raw === "book") return "books";
        if (/^audiobooks?\d*$/.test(raw) || raw === "audiobook") return "audiobooks";
        if (/^music\d*$/.test(raw)) return "music";
        if (/^apps?\d*$/.test(raw) || /^games?\d*$/.test(raw) || raw === "app" || raw === "game") return "apps";
        if (/^misc\d*$/.test(raw) || raw === "other") return "misc";
        return raw;
      };
      const directCategory = normalizeCategory(item.detected_category || item.category || item.assigned_category_safe || "");
      if (directCategory) return directCategory;
      return normalizeCategory(item.itype ? this.itypeToCategory(item.itype) : "");
    },

    setExternalCategory(key, value) {
      const nextManual = { ...this.manualExternalCategories || {} };
      if (value) nextManual[key] = value;
      else delete nextManual[key];
      this.manualExternalCategories = nextManual;
      this._applyDetectedCategories();
      this.apiPost("/api/pending/category-overrides", { key, category: value || null }).catch((e2) => {
        console.warn("Failed to save category override", e2);
      });
      const resolvedCategory = this._resolveExtCategory(key) || "external";
      if (this.selectedMeta.size === 0) return;
      const nextMeta = new Map(this.selectedMeta);
      let changed = false;
      for (const [metaKey, meta] of nextMeta.entries()) {
        if (metaKey === key || metaKey.startsWith(`${key}/`)) {
          nextMeta.set(metaKey, { ...meta, category: resolvedCategory });
          changed = true;
        }
      }
      if (changed) this.selectedMeta = nextMeta;
    },

    /**
     * Resolve the visual type badge for a queue item based on its path.
     * Returns 'pack', 'episode', or null.
     */
    resolvedItemType(item) {
      const itype = (item.itype || "").toLowerCase();
      if (itype.includes("pack") || itype === "tv show") return "pack";
      if (itype === "tv episode") return "episode";
      if (item.category === "tv") {
        const lastSeg = (item.path || "").replace(/\\/g, "/").split("/").pop() || "";
        const fileExtRe = /\.(mkv|mp4|avi|ts|m4v|mov|wmv|rar|zip|7z|nzb)$/i;
        if (!fileExtRe.test(lastSeg)) return "pack";
        return "episode";
      }
      return null;
    },

    getCategoryIcon(catId) {
      const cat = this.categories.find((c2) => c2.id === catId);
      if (cat && cat.icon) return cat.icon;
      const meta = categoryMeta[catId];
      if (meta && meta.icon) return meta.icon;
      const map = { movies: "film", tv: "tv", misc: "package", external: "folder-input" };
      return map[catId] || "folder";
    },

    getCategoryColor(catId) {
      const cat = this.categories.find((c2) => c2.id === catId);
      const raw = (cat && cat.color) || (categoryMeta[catId] && categoryMeta[catId].color) || { movies: "purple", tv: "cyan", misc: "orange", external: "emerald" }[catId] || "gray";
      return raw.replace(/-\d+$/, "");
    },

    getCategoryStyleMeta(catId) {
      const palette = {
        cyan: { border: "#0e7490", background: "rgba(8, 145, 178, 0.16)", text: "#22d3ee" },
        purple: { border: "#7e22ce", background: "rgba(147, 51, 234, 0.16)", text: "#c084fc" },
        orange: { border: "#9a3412", background: "rgba(249, 115, 22, 0.16)", text: "#fb923c" },
        amber: { border: "#a16207", background: "rgba(245, 158, 11, 0.16)", text: "#fbbf24" },
        pink: { border: "#9d174d", background: "rgba(236, 72, 153, 0.16)", text: "#f472b6" },
        slate: { border: "#64748b", background: "rgba(148, 163, 184, 0.16)", text: "#e2e8f0" },
        zinc: { border: "#71717a", background: "rgba(161, 161, 170, 0.16)", text: "#e4e4e7" },
        green: { border: "#15803d", background: "rgba(34, 197, 94, 0.16)", text: "#4ade80" },
        emerald: { border: "#047857", background: "rgba(16, 185, 129, 0.16)", text: "#34d399" },
        blue: { border: "#2563eb", background: "rgba(59, 130, 246, 0.16)", text: "#60a5fa" },
        red: { border: "#991b1b", background: "rgba(239, 68, 68, 0.16)", text: "#f87171" },
        gray: { border: "#4b5563", background: "rgba(107, 114, 128, 0.16)", text: "#d1d5db" }
      };
      const raw = this.getCategoryColor(catId);
      const normalized = String(raw || "").trim().toLowerCase();
      if (normalized.startsWith("#")) {
        const hex = normalized;
        return {
          border: hex,
          background: `${hex}26`,
          text: hex
        };
      }
      return palette[normalized] || palette.gray;
    },

    getCategorySwatchStyle(catId) {
      const style = this.getCategoryStyleMeta(catId);
      return {
        backgroundColor: style.background,
        border: `1px solid ${style.border}`,
        color: style.text,
        boxShadow: `inset 0 0 0 1px ${style.border}33`
      };
    },

    getCategoryIconStyle(catId) {
      const style = this.getCategoryStyleMeta(catId);
      return {
        color: style.text
      };
    },

    getCategoryBadgeStyle(catId) {
      const style = this.getCategoryStyleMeta(catId);
      return {
        backgroundColor: style.background,
        borderColor: style.border,
        color: style.text
      };
    },

    getExternalItemCategory(item) {
      const cat = this.getCategoryForItem(item);
      return cat && cat !== "external" ? cat : "";
    },

    getUploadCategoryForItem(item) {
      if (this.isSelectionIgnored(item)) return "";
      if (this.isPackOnlyExternalChild(item)) return "";
      const cat = this.getCategoryForItem(item);
      return cat && cat !== "external" ? cat : "";
    },

    getCategoryForItem(item) {
      if (!item) return "";
      const normalizeCategory = (value) => {
        const raw = (value || "").toString().trim().toLowerCase();
        if (!raw) return "";
        if (/^tv\d*$/.test(raw) || /^series\d*$/.test(raw) || /^shows?\d*$/.test(raw)) return "tv";
        if (/^movies?\d*$/.test(raw) || /^films?\d*$/.test(raw)) return "movies";
        if (/^anime\d*$/.test(raw)) return "anime";
        if (/^ebooks?\d*$/.test(raw)) return "ebooks";
        if (/^audiobooks?\d*$/.test(raw)) return "audiobooks";
        if (/^books?\d*$/.test(raw)) return "books";
        if (/^music\d*$/.test(raw)) return "music";
        if (/^apps?\d*$/.test(raw) || /^games?\d*$/.test(raw)) return "apps";
        if (/^disc\d*$/.test(raw)) return "disc";
        if (/^misc\d*$/.test(raw) || /^other\d*$/.test(raw)) return "misc";
        const map = {
          movie: "movies",
          movies: "movies",
          tv: "tv",
          television: "tv",
          anime: "anime",
          disc: "disc",
          music: "music",
          book: "books",
          books: "books",
          ebook: "ebooks",
          ebooks: "ebooks",
          audiobook: "audiobooks",
          audiobooks: "audiobooks",
          app: "apps",
          apps: "apps",
          game: "apps",
          games: "apps",
          misc: "misc",
          other: "misc"
        };
        return map[raw] || raw;
      };
      const inheritedExternalCategory = this.isExternalDescendantNode(item) ? normalizeCategory(this.findExternalAncestorCategory(item)) : "";
      if (inheritedExternalCategory && inheritedExternalCategory !== "external") return inheritedExternalCategory;
      const directManualCategory = item.key ? normalizeCategory(this.manualExternalCategories[item.key]) : "";
      if (directManualCategory && directManualCategory !== "external") return directManualCategory;
      if ((item.itype || "").toLowerCase() === "anime") return "anime";
      const detectedCategory = normalizeCategory(item.detected_category);
      if (detectedCategory && detectedCategory !== "external") return detectedCategory;
      const explicitCategory = normalizeCategory(item.category);
      if (explicitCategory && explicitCategory !== "external") return explicitCategory;
      const safeAssigned = normalizeCategory(item.assigned_category_safe);
      if (safeAssigned && safeAssigned !== "external") return safeAssigned;
      const resolvedExternal = item.key ? normalizeCategory(this._resolveExtCategory(item.key)) : "";
      if (resolvedExternal && resolvedExternal !== "external") return resolvedExternal;
      const itype = (item.itype || "").toLowerCase();
      if (itype === "external") {
        return detectedCategory || "";
      }
      if (itype.includes("season pack") || itype.includes("tv show")) return "tv";
      if (itype.includes("tv") || itype.includes("episode")) return "tv";
      if (itype.includes("movie")) return "movies";
      const itypeCategory = this.itypeToCategory(item.itype);
      if (itypeCategory) return itypeCategory;
      return detectedCategory || "";
    },

    _resolveExtCategory(key) {
      if (!key) return "";
      let cursor = key;
      while (cursor) {
        if (this.manualExternalCategories[cursor]) return this.manualExternalCategories[cursor];
        if (this.externalCategories[cursor]) return this.externalCategories[cursor];
        const slashIdx = cursor.lastIndexOf("/");
        if (slashIdx <= 0) break;
        cursor = cursor.substring(0, slashIdx);
      }
      return "";
    },

    // ============================================================
    //  Force Upload Flyout
    // ============================================================
    openCategoryFilter(event) {
      const rect = (event.target.closest("button") || event.target).getBoundingClientRect();
      this.categoryFilterPos = { x: rect.left, y: rect.bottom + 4 };
      this.categoryFilterOpen = !this.categoryFilterOpen;
      this.bulkSelectOpen = false;
      this.filterModeOpen = false;
    },

    // ============================================================
    //  Queue Display Helpers
    // ============================================================
    categoryBadgeClass(cat) {
      const map = {
        tv: "bg-cyan-500/15 text-cyan-400",
        movies: "bg-purple-500/15 text-purple-400",
        anime: "bg-pink-500/15 text-pink-400",
        disc: "bg-[#E0E0E0] text-[#2A2A2A] border-[#B9B9B9]",
        books: "bg-emerald-500/15 text-emerald-400",
        ebooks: "bg-emerald-500/15 text-emerald-400",
        audiobooks: "bg-orange-500/15 text-orange-400",
        music: "bg-blue-500/15 text-blue-400",
        apps: "bg-red-500/15 text-red-400",
        misc: "bg-orange-500/15 text-orange-400"
      };
      return map[cat] || "bg-notion-bg-hover text-notion-text-tertiary";
    },

    categorySelectWidthClass(cat) {
      const map = {
        tv: "w-[3.4rem]",
        disc: "w-[4.2rem]",
        apps: "w-[4.2rem]",
        misc: "w-[4.3rem]",
        music: "w-[4.6rem]",
        anime: "w-[4.7rem]",
        books: "w-[4.7rem]",
        movies: "w-[4.9rem]",
        ebooks: "w-[4.9rem]",
        audiobooks: "w-[6.2rem]"
      };
      return map[cat] || "w-[4.9rem]";
    },
  },
};
