// Queue page: Ignored rows (TV/anime only, never done ones) and the auto-selection rules around them.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.

// Release-source tags (WEB-DL, BluRay, ...). Only used to decide whether a folder shows
// the 'Ignored' warning bubble; category detection stays on the server.
const SOURCE_TOKEN_PATTERN = /(?:^|[.\s_-])(?:WEB(?:[.\s_-]?DL|[.\s_-]?Rip|[.\s_-]?HD)?|WEBDL|WEBRip|WEBHD|BluRay|BDRip|BRRip|REMUX|HDRip|PDRip|HDTV|PDTV|SDTV|TV|TVRip|HQSATRip|SATRip|DSR|DVB|DVDRip|DVD|VHS(?:Rip)?|DV|UHD|AMZN|NF|NFLX|DSNP|PCOK|HMAX|MAX|HULU|ATVP|AUBC|iT|iP|STAN|CR|PMTP|PMNT|CTV|CBC|BBC|PBS|TBS|TNT|NBC|ABC|CBS|FOX|HBO|SHOWTIME|SHO)(?:[.\s_-]|$)/i;

function hasSourceToken(name) {
  return SOURCE_TOKEN_PATTERN.test(String(name || ""));
}

export default {
  computed: {
    ignoredCount() {
      return Array.isArray(this.ignoredPaths) ? this.ignoredPaths.length : 0;
    },
  },
  methods: {
    isAutoIgnoreOverridden(item) {
      const key = this.itemPathKey(item);
      if (!key || !Array.isArray(this.unignoredPaths)) return false;
      const keys = new Set([key]);
      if (item && item.key) keys.add(this.normalizePathKey(item.key));
      if (item && item.path) keys.add(this.normalizePathKey(item.path));
      for (const candidate of keys) {
        if (candidate && this.unignoredPaths.includes(candidate)) return true;
      }
      return false;
    },

    isItemIgnored(item) {
      if (!item) return false;
      const key = this.itemPathKey(item);
      const keys = new Set([key]);
      if (item && item.key) keys.add(this.normalizePathKey(item.key));
      if (item && item.path) keys.add(this.normalizePathKey(item.path));
      const manuallyIgnored = (this.ignoredPaths || []).some((stored) => keys.has(this.normalizePathKey(stored)));
      return !!this.isSelectionIgnored(item) || manuallyIgnored;
    },

    unignoreItem(item) {
      const key = this.itemPathKey(item);
      if (!key) return;
      const next = new Set(this.unignoredPaths || []);
      next.add(key);
      if (item && item.key) next.add(this.normalizePathKey(item.key));
      if (item && item.path) next.add(this.normalizePathKey(item.path));
      this.unignoredPaths = Array.from(next);
      this.ignoredPaths = (this.ignoredPaths || []).filter((path) => {
        const normalized = this.normalizePathKey(path);
        return normalized !== key
          && normalized !== this.normalizePathKey(item && item.key ? item.key : "")
          && normalized !== this.normalizePathKey(item && item.path ? item.path : "");
      });
      this._bumpFilterVersion();
      this.showToast("success", "Unignored", "This item can now be staged if it otherwise passes filters");
    },

    ignoreItem(item) {
      const key = this.itemPathKey(item);
      if (!key) return;
      const merged = new Set(this.ignoredPaths || []);
      merged.add(key);
      if (item && item.key) merged.add(this.normalizePathKey(item.key));
      if (item && item.path) merged.add(this.normalizePathKey(item.path));
      this.ignoredPaths = Array.from(merged);
      this.unignoredPaths = (this.unignoredPaths || []).filter((path) => {
        const normalized = this.normalizePathKey(path);
        return normalized !== key
          && normalized !== this.normalizePathKey(item && item.key ? item.key : "")
          && normalized !== this.normalizePathKey(item && item.path ? item.path : "");
      });
      this._bumpFilterVersion();
      this.showToast("success", "Ignored", "This item is hidden from staging until unignored");
    },

    toggleIgnoredItem(item) {
      if (this.isItemIgnored(item)) {
        this.unignoreItem(item);
      } else {
        this.ignoreItem(item);
      }
    },

    ignoreSelectedItems() {
      const selection = this.buildSelectedActionPayloads();
      if (selection.items.length === 0) return;
      const merged = new Set(this.ignoredPaths || []);
      for (const item of selection.items) {
        if (!item || !item.path) continue;
        merged.add(this.normalizePathKey(item.path));
      }
      this.ignoredPaths = Array.from(merged);
      const count = selection.items.length;
      this.selectedItems = /* @__PURE__ */ new Set();
      this.selectedMeta = /* @__PURE__ */ new Map();
      this.showToast("success", "Ignored", `${count} item(s) added to ignored list`);
    },

    async clearIgnoredItems() {
      const count = this.ignoredCount;
      if (count === 0) return;
      const ok = await this.confirmDialog(`Clear ${count} ignored item(s)?`, {
        title: "Clear Ignored List",
        detail: "Those items will show up in the pending list again.",
        danger: true,
        confirmLabel: "Clear List"
      });
      if (!ok) return;
      this.ignoredPaths = [];
      this.showToast("success", "Cleared", "Ignored list cleared");
    },

    isSelectionIgnored(item) {
      if (!item) return false;
      if (this.isAutoIgnoreOverridden(item)) return false;
      if (this.isPackOnlyExternalChild(item)) return false;
      const resolvedCategory = this.getCategoryForItem(item);
      if (["disc", "music", "books", "ebooks", "audiobooks"].includes(resolvedCategory)) return false;
      if (["DISC", "MUSIC", "EBOOK", "AUDIOBOOK"].includes((item.itype || "").toString().toUpperCase())) return false;
      const status = String(item.status || "").toUpperCase();
      return item.ignored === true
        || item.auto_select_ignored === true
        || status === "IGNORED"
        || status === "SKIPPED";
    },

    isAutoSelectable(item) {
      if (!item || this.isItemSkipped(item) || this.isItemExcluded(item)) return false;
      if (this.isPackOnlyExternalChild(item)) return false;
      if (String(item.status || "").toUpperCase() === "VALID" && item.eligible !== false && item.ignored !== true) return true;
      const resolvedCategory = this.getCategoryForItem(item);
      if (["disc", "music", "books", "ebooks", "audiobooks"].includes(resolvedCategory)) return true;
      if (["DISC", "MUSIC", "EBOOK", "AUDIOBOOK"].includes((item.itype || "").toString().toUpperCase())) return true;
      if (this.isSelectionIgnored(item)) return false;
      return item.auto_selectable !== false;
    },

    isPartiallyIgnored(item) {
      if (!item || !item.is_dir || !Array.isArray(item.children) || item.children.length === 0) return false;
      if (this.getCategoryForItem(item) === "disc") return false;
      if ((item.itype || "").toString().toUpperCase() === "DISC") return false;
      let hasIgnored = false;
      let hasSelectable = false;
      const visit = (node) => {
        if (!node || hasIgnored && hasSelectable) return;
        if (this.isSelectionIgnored(node)) hasIgnored = true;
        if (this.isAutoSelectable(node)) hasSelectable = true;
        for (const child of node.children || []) {
          visit(child);
          if (hasIgnored && hasSelectable) return;
        }
      };
      for (const child of item.children) {
        visit(child);
        if (hasIgnored && hasSelectable) break;
      }
      return hasIgnored && hasSelectable;
    },

    isFullyIgnoredTree(item) {
      if (!item) return false;
      if (!item.is_dir || !Array.isArray(item.children) || item.children.length === 0) {
        if (item.child_count > 0) return false;
        return this.isSelectionIgnored(item);
      }
      let leafCount = 0;
      let ignoredLeafCount = 0;
      const visit = (node) => {
        if (!node) return;
        const kids = Array.isArray(node.children) ? node.children : [];
        if (kids.length === 0 || !node.is_dir) {
          leafCount += 1;
          if (this.isSelectionIgnored(node)) ignoredLeafCount += 1;
          return;
        }
        for (const child of kids) {
          visit(child);
        }
      };
      visit(item);
      return leafCount > 0 && ignoredLeafCount === leafCount;
    },

    shouldShowYieldBubble(item) {
      if (!item) return false;
      if (item.is_dir) {
        const folderName = String(item.name || item.path || "");
        if (hasSourceToken(folderName)) return false;
        if (/(?:\bTV\b|season|series|S\d{1,2})/i.test(folderName)) return false;
        return this.isFullyIgnoredTree(item);
      }
      return this.isSelectionIgnored(item) && !this.isPartiallyIgnored(item);
    },

    getDetectionLabel(item) {
      if (!item) return "";
      const category = item.detected_category || this.getCategoryForItem(item);
      const method = item.detection_method || "Folder fallback";
      const flags = Array.isArray(item.detection_flags) && item.detection_flags.length ? ` [${item.detection_flags.map((flag) => String(flag).toUpperCase()).join(", ")}]` : "";
      const override = item.detection_override ? ` - ${item.detection_override}` : "";
      return category ? `${category}${flags} via ${method}${override}` : `${method}${flags}${override}`;
    },
  },
};

export { SOURCE_TOKEN_PATTERN, hasSourceToken };
