// Queue page: Pure helpers for the pending tree: category normalisation, node normalisation, deep freeze.
// No Vue, no DOM: node tests import this file directly.

function deepFreezePendingTree(items) {
  if (!items || typeof items !== "object") return items;
  const visit = (node) => {
    if (!node || typeof node !== "object" || Object.isFrozen(node)) return;
    if (node.indexers && typeof node.indexers === "object") Object.freeze(node.indexers);
    if (node.indexer_errors && typeof node.indexer_errors === "object") Object.freeze(node.indexer_errors);
    const children = node.children;
    if (Array.isArray(children)) {
      for (const child of children) visit(child);
      Object.freeze(children);
    }
    Object.freeze(node);
  };
  if (Array.isArray(items.external)) {
    for (const group of items.external) {
      if (Array.isArray(group.items)) {
        for (const it2 of group.items) visit(it2);
        Object.freeze(group.items);
      }
      Object.freeze(group);
    }
    Object.freeze(items.external);
  }
  for (const [key, val] of Object.entries(items)) {
    if (key === "external" || !Array.isArray(val)) continue;
    for (const it2 of val) visit(it2);
    Object.freeze(val);
  }
  return Object.freeze(items);
}

function normalizePendingCategory(value) {
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
  return raw;
}


function isSyntheticPendingRootFilesNode(node) {
  if (!node || typeof node !== "object") return false;
  const rawName = String(node.name || node.title || "").toLowerCase();
  if (!rawName.includes("(root files)")) return false;
  const hasPath = !!node.path;
  const hasNested = Array.isArray(node.children) && node.children.length > 0 || Array.isArray(node.files) && node.files.length > 0;
  return hasPath && hasNested;
}


// `self` is the Vue page instance (the `_normalizePendingItemsForState` caller passes `this`).
// Kept as a free-standing recursive function - a nested closure here would keep rolling its
// branches up into whichever method calls it, which is exactly what this split is undoing.
function normalizePendingNode(self, node, parentCategory = "") {
  if (!node || typeof node !== "object") return;
  if (node.__normalizing) return;
  node.__normalizing = true;
  if (!node.key && node.path) {
    node.key = `path:${self.normalizePathKey(node.path)}`;
  }
  const inferredSelfCategory = normalizePendingCategory(node.itype ? self.itypeToCategory(node.itype) : "");
  const detectedSelfCategory = normalizePendingCategory(node.detected_category);
  const explicitSelfCategory = normalizePendingCategory(node.category);
  const inheritedCategory = normalizePendingCategory(parentCategory);
  const safeCategory = detectedSelfCategory || explicitSelfCategory || inheritedCategory || inferredSelfCategory || "";
  node.assigned_category_safe = safeCategory;
  if (!node.detected_category && safeCategory) node.detected_category = safeCategory;
  if ((!Array.isArray(node.children) || node.children.length === 0) && Array.isArray(node.files) && node.files.length > 0) {
    node.children = node.files;
    if (typeof node.is_dir === "undefined") node.is_dir = true;
  }
  if (Array.isArray(node.children)) {
    node.children.forEach((child) => normalizePendingNode(self, child, safeCategory));
  }
  delete node.__normalizing;
}


function normalizePendingExternalGroups(self, items) {
  if (!Array.isArray(items.external)) return;
  items.external.forEach((group, index2) => {
    if (!group || typeof group !== "object") return;
    const firstPath = group.items && group.items[0] && group.items[0].path ? String(group.items[0].path).replace(/\\/g, "/") : "";
    const inferredFolder = firstPath ? firstPath.split("/").slice(0, -1).join("/") : "";
    const rawKey = group.key || group.id || group.label || group.folder_name || inferredFolder || `external-${index2}`;
    group.__ui_key = `external:${index2}:${self.normalizePathKey(rawKey)}`;
    if (!group.folder_name) {
      group.folder_name = group.label || (inferredFolder.split("/").pop() || `External ${index2 + 1}`);
    }
    group.items = group.items || [];
    const groupCatHint = "";
    (group.items || []).forEach((node) => normalizePendingNode(self, node, groupCatHint));
    if (group.items.length === 1 && isSyntheticPendingRootFilesNode(group.items[0])) {
      const synthetic = group.items[0];
      const extracted = Array.isArray(synthetic.children) && synthetic.children.length > 0 ? synthetic.children : Array.isArray(synthetic.files) ? synthetic.files : [];
      if (extracted.length > 0) {
        group.items = extracted;
        group.items.forEach((node) => normalizePendingNode(self, node, groupCatHint));
      }
    }
  });
}

export { deepFreezePendingTree, normalizePendingCategory, isSyntheticPendingRootFilesNode, normalizePendingNode, normalizePendingExternalGroups };
