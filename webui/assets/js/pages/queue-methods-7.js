// Lazy child loading for the pending tree: loaded children are grafted into the
// (re-frozen) item tree, so expanded folders stay populated across refreshes.
import { deepFreezePendingTree } from "./queue.js";

export default {
_graftLoadedChildren(newBase) {
      const old = this._lastRawItems;
      if (!old || !newBase) return;
      const graft = (newNode, oldNode) => {
        if (!newNode || !oldNode) return;
        const oldKids = oldNode.children || oldNode.files || [];
        const newKids = newNode.children || newNode.files || [];
        if (oldKids.length > 0 && newKids.length === 0) {
          // The previous tree is deep-frozen; graft a thawed copy so the
          // normalizer can still annotate the grafted nodes.
          const kids = this._clonePendingItems({ kids: oldKids }).kids || [];
          newNode.children = kids;
          newNode.files = kids;
        }
        for (const nc of (newNode.children || [])) {
          const oc = oldKids.find(c => c.key === nc.key);
          if (oc) graft(nc, oc);
        }
      };
      for (const [gi, group] of (newBase.external || []).entries()) {
        const og = (old.external || [])[gi];
        if (!og) continue;
        for (const item of (group.items || [])) {
          const oi = (og.items || []).find(i => i.key === item.key);
          if (oi) graft(item, oi);
        }
      }
    },

_clonePendingItems(items) {
      if (!items || typeof items !== "object") return { movies: [], misc: [], external: [] };
      try {
        if (typeof structuredClone === "function") {
          return structuredClone(items);
        }
      } catch (_e2) {
      }
      try {
        return JSON.parse(JSON.stringify(items));
      } catch (_e2) {
        return { movies: [], misc: [], external: [] };
      }
    },

_findPendingNodeByKey(items, targetKey, targetPath = "") {
      if (!items || !targetKey) return null;
      const normalizedTargetPath = this.normalizePathKey(targetPath || "");
      const visit = (node) => {
        if (!node || typeof node !== "object") return null;
        if (String(node.key || "") === String(targetKey)) return node;
        if (normalizedTargetPath && this.normalizePathKey(node.path || "") === normalizedTargetPath) return node;
        const childLists = [];
        if (Array.isArray(node.children)) childLists.push(node.children);
        if (Array.isArray(node.files) && node.files !== node.children) childLists.push(node.files);
        if (Array.isArray(node.items)) childLists.push(node.items);
        for (const list of childLists) {
          for (const child of list) {
            const found = visit(child);
            if (found) return found;
          }
        }
        return null;
      };
      for (const section of Object.values(items)) {
        if (!Array.isArray(section)) continue;
        for (const entry of section) {
          const found = visit(entry);
          if (found) return found;
        }
      }
      return null;
    },

_findCurrentPendingNode(targetKey, targetPath = "") {
      return this._findPendingNodeByKey(this.items, targetKey, targetPath);
    },

_replacePendingTree(rawItems) {
      const nextRaw = rawItems && typeof rawItems === "object" ? rawItems : { movies: [], misc: [], external: [] };
      this._lastRawItems = nextRaw;
      this.skipFiles = this.skipFiles || { enabled: false, display_mode: "disabled" };
      const itemsForState = this.skipFiles.enabled && this.skipFiles.display_mode === "hidden" ? this._stripHiddenSkippedItems(nextRaw) : nextRaw;
      this._normalizePendingItemsForState(itemsForState);
      this.items = deepFreezePendingTree(itemsForState);
      this._applyDetectedCategories();
    },

async ensureExtChildrenLoaded(itemOrKey, fallbackPath = "") {
      const currentItem = typeof itemOrKey === "string" ? this._findCurrentPendingNode(itemOrKey, fallbackPath) : itemOrKey;
      if (!currentItem || !currentItem.is_dir) return false;
      const itemKey = String(currentItem.key || (typeof itemOrKey === "string" ? itemOrKey : ""));
      if (!itemKey) return false;
      const existingChildren = this._getLoadedRowChildren(currentItem);
      if (existingChildren.length > 0) return true;
      if (Number(currentItem.child_count || 0) <= 0) return false;
      if (this._loadingExtChildren[itemKey]) {
        try {
          await this._loadingExtChildren[itemKey];
        } catch (_e2) {
        }
        const refreshed = this._findCurrentPendingNode(itemKey, currentItem.path || fallbackPath);
        return this._getLoadedRowChildren(refreshed).length > 0;
      }
      const loader = (async () => {
        const params = new URLSearchParams();
        params.set("key", itemKey);
        if (currentItem.path || fallbackPath) params.set("path", currentItem.path || fallbackPath);
        const data = await this.apiFetch(`/api/pending/children?${params.toString()}`, { timeoutMs: 9e4 });
        const loadedChildren = Array.isArray(data == null ? void 0 : data.children) ? data.children : [];
        const rawBase = this._clonePendingItems(this._lastRawItems || this.items);
        const target = this._findPendingNodeByKey(rawBase, itemKey, currentItem.path || fallbackPath);
        if (!target) return false;
        target.children = loadedChildren;
        target.files = loadedChildren;
        target.child_count = Number(data == null ? void 0 : data.child_count) || loadedChildren.length;
        this._replacePendingTree(rawBase);
        if (loadedChildren.length > 0 && this.selectedItems.has(itemKey)) {
          const nextSet = new Set(this.selectedItems);
          loadedChildren.forEach((child) => { if (child && child.key) nextSet.add(child.key); });
          this.selectedItems = nextSet;
        }
        return loadedChildren.length > 0;
      })();
      this._loadingExtChildren[itemKey] = loader;
      try {
        return await loader;
      } catch (_e2) {
        if (_e2 && _e2.status === 401) {
          window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
        } else {
          this.showToast("error", "Error", "Failed to load child items");
        }
        return false;
      } finally {
        delete this._loadingExtChildren[itemKey];
      }
    },

isExtChildrenLoading(itemOrKey, fallbackPath = "") {
      const currentItem = typeof itemOrKey === "string" ? this._findCurrentPendingNode(itemOrKey, fallbackPath) : itemOrKey;
      const itemKey = currentItem && currentItem.key ? String(currentItem.key) : typeof itemOrKey === "string" ? itemOrKey : "";
      return !!(itemKey && this._loadingExtChildren[itemKey]);
    },

async _hydrateExpandedExtChildren() {
      if (this._expandedExtHydrationPromise) {
        return this._expandedExtHydrationPromise;
      }
      const expandedKeys = Object.entries(this.expandedExtItems || {}).filter(([, open]) => !!open).map(([key]) => key).filter(Boolean);
      if (expandedKeys.length === 0) return;
      this._expandedExtHydrationPromise = (async () => {
        for (const key of expandedKeys) {
          const node = this._findCurrentPendingNode(key);
          if (!node || !node.is_dir) continue;
          if (this._getLoadedRowChildren(node).length > 0) continue;
          if (Number(node.child_count || 0) <= 0) continue;
          await this.ensureExtChildrenLoaded(node);
        }
      })();
      try {
        await this._expandedExtHydrationPromise;
      } finally {
        this._expandedExtHydrationPromise = null;
      }
    },

/**
     * Recursively fetches children for any collapsed (never-expanded)
     * directory under `node`, so bulk-selecting a folder that was never
     * manually expanded still picks up every file inside it. Without
     * this, checking a collapsed folder's box only selected the folder
     * key itself (its children array was empty client-side), which
     * produced zero real uploadable items and silently did nothing.
     */
    async _ensureExtSubtreeLoaded(node) {
      if (!node || !node.is_dir) return node;
      let current = node;
      if (this._getLoadedRowChildren(current).length === 0 && Number(current.child_count || 0) > 0) {
        await this.ensureExtChildrenLoaded(current);
        current = this._findCurrentPendingNode(current.key, current.path) || current;
      }
      const kids = this._getLoadedRowChildren(current);
      for (const child of kids) {
        await this._ensureExtSubtreeLoaded(child);
      }
      return this._findCurrentPendingNode(current.key, current.path) || current;
    },
};
