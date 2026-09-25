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
