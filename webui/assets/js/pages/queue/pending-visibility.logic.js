// Queue page: Pure helpers for which pending rows are visible.
// No Vue, no DOM: node tests import this file directly.

var FILTER_MODE_OPTIONS = [
  { value: "hideQueued", label: "Hide Staged" },
  { value: "onlyQueued", label: "Only Staged" },
  { value: "hideCompleted", label: "Hide Completed" },
  { value: "showCompleted", label: "Show Completed" },
  { value: "hideIgnored", label: "Hide Ignored" }
];

// The pending search box. Literal mode is a plain substring match; otherwise release
// separators (. - _ [ ] ( ) ') count as spaces and every query word must appear.
function searchMatches(name, query, literal) {
  if (!query) return true;
  const sep = /[\.\-_\[\]()']/g;
  if (literal) {
    return name.toLowerCase().includes(query.toLowerCase());
  }
  const target = name.toLowerCase().replace(sep, " ");
  const words = query.toLowerCase().replace(sep, " ").split(/\s+/).filter(Boolean);
  return words.every((w2) => target.includes(w2));
}

export { FILTER_MODE_OPTIONS, searchMatches };
