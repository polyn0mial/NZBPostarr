// Queue page: Pure helpers behind row selection.
// No Vue, no DOM: node tests import this file directly.

// Whether a selection key/path names a folder. The selection meta records is_dir when the row
// was selected; otherwise a path whose last segment has a media/archive extension is a file.
function selectionPathIsDirectory(key, meta, fallbackPath = "") {
  if (meta && typeof meta.is_dir === "boolean") return meta.is_dir;
  const path = fallbackPath || meta && meta.path || "";
  const effectivePath = path || (key ? String(key).replace(/^ext:[^:]*:/, "").replace(/\\/g, "/") : "");
  const lastSeg = String(effectivePath).replace(/\\/g, "/").split("/").pop() || "";
  return !/\.(mkv|mp4|avi|ts|m4v|mov|wmv|rar|zip|7z|nzb|iso|img|epub|m4b|mp3|flac|pdf)$/i.test(lastSeg);
}

// A row whose name or path spans seasons ("S01-S03").
function isMultiSeasonRange(item) {
  if (!item) return false;
  const path = item.path || item.target_path || item.source_path || "";
  const name = item.name || String(path).replace(/\\/g, "/").split("/").pop() || "";
  const label = `${name} ${path}`;
  return /\bS\d{1,2}\s*[-–]\s*S\d{1,2}\b/i.test(label);
}

// Drop entries without an item key and every repeat of a key, keeping the first.
function dedupeSelectionEntries(entries) {
  const seen = /* @__PURE__ */ new Set();
  const deduped = [];
  for (const entry of entries) {
    const key = entry?.item?.key;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    deduped.push(entry);
  }
  return deduped;
}

export { selectionPathIsDirectory, isMultiSeasonRange, dedupeSelectionEntries };
