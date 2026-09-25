// Grouped-view model for the History page: server-side upload groups -> group rows with
// per-season completeness stats. Pure functions; the page's computed properties call them.
import { isMovieType } from 'page-base';

// Per-season completeness stats for one TV season bucket built by buildUploadGroupSeasons:
// total size, the missing-episode list (when there are at least 2 known episode numbers to
// bound a range), and whether the whole season looks entirely absent.
function computeUploadSeasonStats(season) {
    season.totalSize = season.items.reduce((sum, it) => sum + (Number(it.filesize) || 0), 0);
    season.missingCount = 0;
    season.missingList = [];
    season.expectedEps = 0;
    season.foundEps = 0;
    season.firstEp = 0;
    season.lastEp = 0;
    season.allEpMissing = false;

    if (season.epNumbers.length === 0 && season.items.length > 0) {
        // Season 0 (specials) are inherently incomplete - never flag them as "All EP Missing".
        if (season.num !== 0) {
            season.allEpMissing = true;
        }
    } else if (season.epNumbers.length >= 2) {
        season.epNumbers.sort((a, b) => a - b);
        const first = season.epNumbers[0];
        const last = season.epNumbers[season.epNumbers.length - 1];
        const epSet = new Set(season.epNumbers);
        const missing = [];
        for (let e = first; e <= last; e++) {
            if (!epSet.has(e)) missing.push(e);
        }
        season.firstEp = first;
        season.lastEp = last;
        season.expectedEps = last - first + 1;
        season.foundEps = epSet.size;
        season.missingCount = missing.length;
        season.missingList = missing.slice(0, 20);
    }
}

// Bucket a TV group's items into season sub-groups (or its stray movie-item bucket), expand
// multi-episode ranges (e.g. E05-E06 -> [5, 6]), then compute per-season stats. Fills in
// `group.movieItems`, `.seasonList`, `.missingCount`, and `.allEpMissingSeasonsCount` in place.
function buildUploadGroupSeasons(group) {
    const seasonsMap = new Map();
    for (const item of group.items) {
        // Items individually classified as movies shouldn't be jammed into Season 0 - keep
        // them in a separate bucket.
        if (isMovieType(item.media_type) && !item.season_number && !item.episode_number) {
            group.movieItems.push(item);
            continue;
        }

        const seasonNum = item.season_number || 0;
        const epNum = item.episode_number || null;
        const epEnd = item.episode_end_number || null;

        if (!seasonsMap.has(seasonNum)) {
            seasonsMap.set(seasonNum, {
                num: seasonNum,
                label: 'S' + String(seasonNum).padStart(2, '0'),
                items: [],
                epNumbers: [],
            });
        }
        const season = seasonsMap.get(seasonNum);
        season.items.push(item);
        if (epNum !== null) {
            if (epEnd !== null && epEnd > epNum) {
                for (let e = epNum; e <= epEnd; e++) {
                    season.epNumbers.push(e);
                }
            } else {
                season.epNumbers.push(epNum);
            }
        }
    }

    group.seasonList = Array.from(seasonsMap.values()).sort((a, b) => a.num - b.num);

    group.missingCount = 0;
    group.allEpMissingSeasonsCount = 0;
    for (const season of group.seasonList) {
        computeUploadSeasonStats(season);
        if (season.allEpMissing) group.allEpMissingSeasonsCount++;
        group.missingCount += season.missingCount;
    }
}

// One server-side group -> one row for the grouped-uploads view. A summary-only group (details
// not loaded yet) and a movie group both short-circuit before season analysis; only a loaded TV
// group needs buildUploadGroupSeasons.
export function buildUploadGroupFromServerGroup(sg) {
    const rawItems = Array.isArray(sg.items) ? sg.items : [];
    const detailsLoaded = Array.isArray(sg.items) && !sg.summary_only;
    const itemCount = Number(sg.item_count ?? rawItems.length) || 0;
    const titleKey = sg.title_key || (sg.show_name || '').toLowerCase();

    if (!detailsLoaded) {
        return {
            key: titleKey,
            titleKey,
            name: sg.show_name,
            items: rawItems,
            itemCount,
            mediaType: (sg.media_type || 'other').toLowerCase(),
            isMovie: isMovieType(sg.media_type || ''),
            movieItems: [],
            seasonList: [],
            missingCount: 0,
            allEpMissingSeasonsCount: 0,
            totalSize: Number(sg.total_size || 0),
            latestDate: sg.latest_date || null,
            detailsLoaded,
        };
    }

    // Determine media type: use server-provided value, or vote across items for the most
    // common type.
    const rawType = (sg.media_type || '').toLowerCase();
    const isMovie = isMovieType(rawType);

    const group = {
        key: titleKey,
        titleKey,
        name: sg.show_name,
        items: rawItems,
        itemCount,
        mediaType: rawType || 'other',
        isMovie: isMovie,
        movieItems: [],   // movie-typed items inside a TV group
        detailsLoaded,
    };

    group.totalSize = group.items.reduce((sum, it) => sum + (Number(it.filesize) || 0), 0);
    group.latestDate = group.items.reduce((latest, it) => {
        return it.updated_at > latest ? it.updated_at : latest;
    }, group.items[0].updated_at);

    // Movies: skip season/episode analysis entirely
    if (isMovie) {
        group.seasonList = [];
        group.missingCount = 0;
        group.allEpMissingSeasonsCount = 0;
        return group;
    }

    buildUploadGroupSeasons(group);
    return group;
}
