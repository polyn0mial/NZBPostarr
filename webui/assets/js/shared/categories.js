// One table for category badge classes (DECISIONS "Category colours": the queue values are
// canonical: TV cyan, Misc orange, grey DISC badge). Literal class strings only: the Tailwind
// build scans this file.

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
