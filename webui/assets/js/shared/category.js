// Category id <-> content-type label mapping for the pending tree (pure; no Vue).

/**
 * Map a display itype string (e.g. "TV Show", "Movie", "Anime") to an upload
 * category id (e.g. "tv", "movies", "misc").
 *
 * For content types that *may* have a dedicated folder category (music,
 * audiobook, ebook), pass `availableCategories` (an array of {id} objects)
 * so the function can prefer a specific category over falling back to "misc".
 */
// Extracted from itypeToCategory to keep its own branching down: finds the
// best available-category match by an optional exact id, then by keyword.
function findCategoryMatch(availableCategories, exactId, keywordIncludes) {
    if (exactId) {
        const exact = availableCategories.find(c => c.id.toLowerCase() === exactId);
        if (exact) return exact.id;
    }
    const found = availableCategories.find(c => c.id.toLowerCase().includes(keywordIncludes));
    return found ? found.id : '';
}

export function itypeToCategory(itype, availableCategories = []) {
    if (!itype) return '';
    switch (itype) {
        case 'TV Show':
        case 'TV Episode':
            return 'tv';
        case 'Anime':
            return 'anime';
        case 'Movie':
        case 'Movies':
            return 'movies';
        case 'Music':
            return findCategoryMatch(availableCategories, null, 'music');
        case 'Audiobook':
            return findCategoryMatch(availableCategories, 'audiobooks', 'audiobook');
        case 'Ebook':
            return findCategoryMatch(availableCategories, 'ebooks', 'ebook');
        default:
            return '';
    }
}

/**
 * Map an upload category id back to its best matching content-type label.
 */
export function categoryToItype(category, fallbackItype = 'Misc') {
    switch ((category || '').toLowerCase()) {
        case 'tv':
            return 'TV Episode';
        case 'movies':
            return 'Movie';
        case 'anime':
            return 'Anime';
        case 'music':
            return 'Music';
        case 'books':
            return 'Ebook';
        case 'apps':
            return 'Apps';
        case 'audiobooks':
            return 'Audiobook';
        case 'ebooks':
            return 'Ebook';
        case 'misc':
            return 'Misc';
        default:
            return fallbackItype || 'Misc';
    }
}

// The queue page's category badge classes (DECISIONS "Category colours": TV cyan, Misc orange,
// grey DISC badge). Literal class strings only: the Tailwind build scans this file.
export const CATEGORY_BADGE_CLASS = {
    tv: 'bg-cyan-500/15 text-cyan-400',
    movies: 'bg-purple-500/15 text-purple-400',
    anime: 'bg-pink-500/15 text-pink-400',
    disc: 'bg-[#E0E0E0] text-[#2A2A2A] border-[#B9B9B9]',
    books: 'bg-emerald-500/15 text-emerald-400',
    ebooks: 'bg-emerald-500/15 text-emerald-400',
    audiobooks: 'bg-orange-500/15 text-orange-400',
    music: 'bg-blue-500/15 text-blue-400',
    apps: 'bg-red-500/15 text-red-400',
    misc: 'bg-orange-500/15 text-orange-400',
};

export function categoryBadgeClass(category) {
    return CATEGORY_BADGE_CLASS[category] || 'bg-notion-bg-hover text-notion-text-tertiary';
}

// History rows carry either a category id or a DB media_type ("movie", "episode", "other").
const MEDIA_TYPE_CATEGORY = { movie: 'movies', episode: 'tv', other: 'misc' };

/**
 * Resolve a category id or DB media_type to a key of `meta` (a categoryMeta table), or null.
 */
export function resolveCategoryKey(typeOrCategory, meta) {
    if (!typeOrCategory) return null;
    const t = typeOrCategory.toLowerCase();
    if (Object.hasOwn(meta, t)) return t;
    return MEDIA_TYPE_CATEGORY[t] || null;
}

// Icon tint per category colour for the History type icons. Literal class strings so the
// Tailwind build sees them. 'gray' (External) is left out on purpose: its text-gray-400 /
// bg-gray-500/15 were never compiled, so that icon has always rendered untinted.
const CATEGORY_ICON_TINT = {
    cyan: { text: 'text-cyan-400', bg: 'bg-cyan-500/15' },
    purple: { text: 'text-purple-400', bg: 'bg-purple-500/15' },
    amber: { text: 'text-amber-400', bg: 'bg-amber-500/15' },
    pink: { text: 'text-pink-400', bg: 'bg-pink-500/15' },
    slate: { text: 'text-slate-400', bg: 'bg-slate-500/15' },
    green: { text: 'text-green-400', bg: 'bg-green-500/15' },
    blue: { text: 'text-blue-400', bg: 'bg-blue-500/15' },
    orange: { text: 'text-orange-400', bg: 'bg-orange-500/15' },
};

export function categoryIconTint(color) {
    return CATEGORY_ICON_TINT[color] || { text: '', bg: '' };
}
