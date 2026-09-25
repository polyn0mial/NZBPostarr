// Queue page: Browser storage for the pending list: session cache and the saved external group order.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.
import { deepFreezePendingTree } from "./pending-tree.logic.js";


var CACHE_KEY = "nzbpostarr_pending_cache";

var EXTERNAL_GROUP_ORDER_KEY = "nzbpostarr_pending_external_group_order";

var EXTERNAL_GROUP_LOCK_KEY = "nzbpostarr_pending_external_group_order_locked";

var SESSION_CACHE_MAX_BYTES = 2e6;

export default {
  methods: {
    loadExternalGroupOrder() {
      try {
        const raw = localStorage.getItem(EXTERNAL_GROUP_ORDER_KEY);
        const parsed = JSON.parse(raw || "[]");
        return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
      } catch (_e2) {
        return [];
      }
    },

    async loadExternalGroupOrderState(forceRefresh = false) {
      if (this.pendingExternalGroupOrderLoaded && !forceRefresh) {
        return this.pendingExternalGroupOrder;
      }
      try {
        const data = await this.apiFetch("/api/pending/order");
        const serverOrder = Array.isArray(data == null ? void 0 : data.order) ? data.order.filter(Boolean).map(String) : [];
        const localOrder = this.loadExternalGroupOrder();
        const order = serverOrder.length ? serverOrder : localOrder;
        this.pendingExternalGroupOrder = order;
        this.pendingExternalGroupOrderLocked = !!(data == null ? void 0 : data.locked);
        this.pendingExternalGroupOrderLoaded = true;
        try {
          localStorage.setItem(EXTERNAL_GROUP_ORDER_KEY, JSON.stringify(order));
          localStorage.setItem(EXTERNAL_GROUP_LOCK_KEY, this.pendingExternalGroupOrderLocked ? "true" : "false");
        } catch (_storageError) {
          // Storage full or disabled: keep the in-memory state.
        }
        if (!serverOrder.length && order.length) {
          void this.apiPut("/api/pending/order", { order }).catch(() => {
          });
        }
        return order;
      } catch (_e2) {
        const fallbackOrder = this.loadExternalGroupOrder();
        this.pendingExternalGroupOrder = fallbackOrder;
        this.pendingExternalGroupOrderLocked = this.loadExternalGroupOrderLocked();
        this.pendingExternalGroupOrderLoaded = true;
        return fallbackOrder;
      }
    },

    saveExternalGroupOrder(order) {
      const normalized = Array.isArray(order) ? order.filter(Boolean).map(String) : [];
      this.pendingExternalGroupOrder = normalized;
      this.pendingExternalGroupOrderLoaded = true;
      try {
        localStorage.setItem(EXTERNAL_GROUP_ORDER_KEY, JSON.stringify(normalized));
      } catch (_storageError) {
        // Storage full or disabled: keep the in-memory state.
      }
      void this.apiPut("/api/pending/order", { order: normalized }).catch(() => {
      });
      this._externalGroupOrderVersion += 1;
    },

    loadExternalGroupOrderLocked() {
      try {
        return localStorage.getItem(EXTERNAL_GROUP_LOCK_KEY) === "true";
      } catch (_e2) {
        return false;
      }
    },

    saveExternalGroupOrderLocked(locked) {
      this.pendingExternalGroupOrderLocked = !!locked;
      this.pendingExternalGroupOrderLoaded = true;
      try {
        localStorage.setItem(EXTERNAL_GROUP_LOCK_KEY, this.pendingExternalGroupOrderLocked ? "true" : "false");
      } catch (_storageError) {
        // Storage full or disabled: keep the in-memory state.
      }
      void this.apiPut("/api/pending/order/locked", { locked: this.pendingExternalGroupOrderLocked }).catch(() => {
      });
      if (this.pendingExternalGroupOrderLocked) {
        this.destroyPendingExternalGroupsSortable();
      } else {
        this.$nextTick(() => this.initPendingExternalGroupsSortable());
      }
    },

    _saveSessionCache(data) {
      try {
        const payload = JSON.stringify({
          items: data.items,
          indexers: data.indexers,
          summary: data.summary,
          cached_at: data.cached_at,
          ts: Date.now()
        });
        if (payload.length > SESSION_CACHE_MAX_BYTES) {
          sessionStorage.removeItem(CACHE_KEY);
          return;
        }
        sessionStorage.setItem(CACHE_KEY, payload);
      } catch (_storageError) {
        // Storage full, disabled or holding a stale shape: skip the session cache.
      }
    },

    _restoreSessionCache() {
      try {
        const raw = sessionStorage.getItem(CACHE_KEY);
        if (!raw) return;
        const cached = JSON.parse(raw);
        if (Date.now() - cached.ts > 3e5) return;
        const cachedItems = cached.items || { movies: [], misc: [], external: [] };
        this._normalizePendingItemsForState(cachedItems);
        this.items = deepFreezePendingTree(cachedItems);
        this.activeIndexers = (cached.indexers || []).slice();
        this.summary = cached.summary || {};
        this.cachedAt = cached.cached_at || null;
        const sel = {};
        this.activeIndexers.forEach((idx) => {
          sel[idx.id] = true;
        });
        this.markIndexerSelection = sel;
        this.syncExternalGroupOrder();
        this.$nextTick(() => this.initPendingExternalGroupsSortable());
      } catch (_storageError) {
        // Storage full, disabled or holding a stale shape: skip the session cache.
      }
    },
  },
};

export { CACHE_KEY, EXTERNAL_GROUP_ORDER_KEY, EXTERNAL_GROUP_LOCK_KEY, SESSION_CACHE_MAX_BYTES };
