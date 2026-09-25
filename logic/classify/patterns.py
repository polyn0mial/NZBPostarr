"""Release-name regexes shared by every classifier rule, and the legacy episode-number check."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List


_AUTO_TV_PATTERNS = re.compile(
    r"(?:"
    r"S\d{1,2}[.\s-]?E\d{1,3}"
    r"|S\d{1,2}[.\s-]?(?:Complete|COMPLETE|Full)"
    # The (?!\d) guards matter: without them "Season" or "Series" followed by a
    # resolution or a year swallows the leading digits of that token, so
    # "The.Last.Season.1080p" matched "Season.10" and classified as TV, and
    # "MINISERIES.1080p" matched "SERIES.10".
    r"|Season[.\s-]?\d{1,2}(?!\d)"
    r"|Series[.\s-]?\d{1,2}(?!\d)"
    r"|Vol(?:ume)?[.\s_-]*\d{1,3}"
    r"|(?:^|[.\s_(-])\d{1,2}x\d{2,3}(?:[.\s_)-]|$)"
    r"|(?:^|[.\s_-])S\d{2}(?:[.\s_-]|$)"
    r"|(?:19|20)\d{2}[.\s-]\d{2}[.\s-]\d{2}"
    r")",
    re.IGNORECASE,
)

_AUTO_MOVIE_PATTERNS = re.compile(
    r"(?:^|[.\s_(-])(?:19|20)\d{2}(?:[.\s_\])-]|$)",
    re.IGNORECASE,
)

_AUTO_YEAR_TOKEN_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

_AUTO_YEAR_RANGE_RE = re.compile(r"(?:19|20)\d{2}\s*[-/]\s*(?:19|20)\d{2}")

_AUTO_SEASON_TOKEN_RE = re.compile(r"\b(?:season|series)\b", re.IGNORECASE)

_AUTO_COMPLETE_SERIES_RANGE_RE = re.compile(
    r"(?:19|20)\d{2}\s*[-/–-]\s*(?:19|20)\d{2}.*\b(?:complete|season|series|s\d{1,2}|dvd|remux)\b",
    re.IGNORECASE,
)

_DATE_EPISODE_RE = re.compile(
    r"(?:^|[.\s_-])(?:19|20)\d{2}[.\s_-](?:0[1-9]|1[0-2])[.\s_-](?:0[1-9]|[12]\d|3[01])(?:[.\s_-]|$)"
)

_TRAILING_RELEASE_GROUP_YEAR_RE = re.compile(r"[-._\s]+[A-Za-z][A-Za-z0-9]{1,20}(?:19|20)\d{2}$")

_GENERIC_TV_SEASON_FOLDER_RE = re.compile(
    r"^(?:"
    r"s\d{1,2}(?:[.\s_-]+\d{1,2})?"
    r"|season[.\s_-]?\d{1,2}(?:[.\s_-]+\d{1,2})?"
    r"|series[.\s_-]?\d{1,2}(?:[.\s_-]+\d{1,2})?"
    r"|s\d{1,2}(?:[.\s_-]?(?:part|vol|volume)[.\s_-]?\d{1,2})?"
    r"|season[.\s_-]?\d{1,2}[.\s_-]*(?:complete|full)?"
    r"|s\d{1,2}[.\s_-]*(?:complete|full)?"
    r")$",
    re.IGNORECASE,
)

_AUTO_ANIME_FANSUB_RE = re.compile(r"^\[[^\]]{1,40}\]\s*", re.IGNORECASE)

_ANIME_SEASONAL_FOLDER_RE = re.compile(r"\b(?:winter|spring|summer|fall|autumn)\s+(?:19|20)\d{2}\b", re.IGNORECASE)

_GENERIC_ANIME_FOLDER_RE = re.compile(r"\b(?:anime|seasonal)\b", re.IGNORECASE)

_EXPLICIT_EPISODE_RE = re.compile(
    r"(?:"
    r"S\d{1,2}[.\s_-]*E\d{1,3}"
    r"|(?:^|[.\s_(-])\d{1,2}x\d{2,3}(?:[.\s_)-]|$)"
    r"|(?:^|[.\s_(-])E\d{1,3}(?:[.\s_)-]|$)"
    r"|(?:^|[.\s_(-])(?:EP?|Episode)[.\s_-]?\d{1,3}(?:[.\s_)-]|$)"
    r")",
    re.IGNORECASE,
)

_ANIME_ABSOLUTE_EPISODE_RE = re.compile(
    r"(?:"
    r"(?:^|[\s._-])-\s?\d{1,3}(?:v\d+)?(?:[\s._(\[]|$)"
    r"|(?:^|[\s._-])\d{1,3}(?:v\d+)?(?:[\s._(\[]|$)"
    r"|\[\d{1,3}(?:v\d+)?\]"
    r")",
    re.IGNORECASE,
)

_FRAMED_ABSOLUTE_EPISODE_RE = re.compile(
    r"(?:"
    r"(?:^|[\s._-])-[.\s_-]*(?!(?:19|20)\d{2}(?:[\s._(\[]|$))\d{1,5}(?:v\d+)?(?=[\s._(\[]|$)"
    r"|\[(?!(?:19|20)\d{2}\])\d{1,5}(?:v\d+)?\]"
    r")",
    re.IGNORECASE,
)

_BARE_HIGH_ABSOLUTE_EPISODE_RE = re.compile(
    r"(?:^|[\s._-])(?!(?:19|20)\d{2}(?:[\s._-]|$))\d{4,5}(?:v\d+)?"
    r"(?=[\s._-]+(?:2160p|1080[pi]?|720p|576[pi]?|480[pi]?|WEB|BluRay|BDRip|HDTV|DVD))",
    re.IGNORECASE,
)

_ANIME_EXTRA_RE = re.compile(
    r"(?:"
    r"(?:^|[\s._-])(NCOP|NCED|OP|ED)(?:[\s._-]|$)"
    r"|(?:^|[\s._-])(preview|pv|trailer|teaser|special|specials)(?:[\s._-]|$)"
    r")",
    re.IGNORECASE,
)

_TV_EPISODE_HINT_RE = re.compile(
    r"(?:"
    r"(?:^|[.\s_-])(?:S\d{1,2}[.\s_-]*)?(?:E\d{1,3}|EP?\d{1,3}|Episode[.\s_-]?\d{1,3})(?:[.\s_([+-]|$)"
    r"|(?:^|[.\s_-])\d{1,2}x\d{1,3}(?:[.\s_)-]|$)"
    r"|(?:^|[.\s_-])(?:Pilot|Finale|Final|Special|OVA|OAD)(?:[.\s_]|$)"
    r"|(?:^|[.\s_-])(?:1of\d{1,2}|Part\d{1,2}of\d{1,2})(?:[.\s_]|$)"
    r"|(?:^|[.\s_-])(?:\d{1,3}(?:[.\s_-]\d{2,4}){1,2})(?:[.\s_]|$)"
    r")",
    re.IGNORECASE,
)

_TV_PART_EPISODE_RE = re.compile(
    r"(?:^|[.\s_-])(?:Part|Pt|Chapter)[.\s_-]?\d{1,3}(?:[.\s_]|$)",
    re.IGNORECASE,
)

_STREAMING_EPISODE_SOURCE_RE = re.compile(
    r"(?=.*(?:^|[.\s_-])(?:AMZN|NF|HMAX|DSNP|ATVP)(?:[.\s_-]|$))(?=.*(?:^|[.\s_-])WEB(?:-DL)?(?:[.\s_-]|$))",
    re.IGNORECASE,
)

_SPORTS_LEAGUE_RE = re.compile(
    r"(?:^|[.\s_-])(?:UFC|Bellator|ONE[.\s_-]?FC|WWE|AEW|NFL|NBA|WNBA|NHL|MLB|MLS|NCAA|F1|MotoGP)(?:[.\s_-]|$)",
    re.IGNORECASE,
)

_SPORTS_EVENT_CONTEXT_RE = re.compile(
    r"(?:"
    r"(?:^|[.\s_-])(?:PPV|Fight[.\s_-]?Night|Main[.\s_-]?Event|Grand[.\s_-]?Prix)(?:[.\s_-]|$)"
    r"|(?:^|[.\s_-])(?:Week|Round|Game|Race)[.\s_-]?\d{1,3}(?:[.\s_-]|$)"
    r"|(?:^|[.\s_-])vs?\.?(?:[.\s_-]|$)"
    r")",
    re.IGNORECASE,
)

_COMPLETE_MINISERIES_RE = re.compile(
    r"(?:"
    r"(?:^|[.\s_-])complete[.\s_-]+mini[.\s_-]?series(?:[.\s_-]|$)"
    r"|(?:^|[.\s_-])mini[.\s_-]?series[.\s_-]+complete(?:[.\s_-]|$)"
    r")",
    re.IGNORECASE,
)

_MOVIE_COLLECTION_RE = re.compile(
    r"(?:^|[.\s_-])(?:anthology|box[.\s_-]?set|collection|duology|trilogy|quadrilogy|tetralogy)"
    r"(?:[.\s_-]|$)",
    re.IGNORECASE,
)

_GUESSIT_EPISODE_SHAPE_RE = re.compile(
    r"(?:"
    r"(?:^|[.\s_-])S\d{1,2}[.\s_-]+\d{1,3}(?:[.\s_-]|$)"
    r"|(?:^|[.\s_-])\d{1,3}[.\s_-]+of[.\s_-]+\d{1,3}(?:[.\s_-]|$)"
    r")",
    re.IGNORECASE,
)

_TV_PACK_EPISODE_RE = re.compile(r"S\d{1,2}[.\s_-]*E\d{1,3}", re.IGNORECASE)

_TV_PACK_RESOLUTION_RE = re.compile(
    r"(?:^|[.\s_-])(?:2160p|1080p|1080i|720p|576p|576i|480p|480i|ntsc|pal)(?:[.\s_-]|$)",
    re.IGNORECASE,
)

# Only real media source tokens count; streaming-service and network tags
# (AMZN, NF, DSNP, network names, bare TV/DV/UHD) alone do not.
SOURCE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"WEB(?:[.\s_-]?DL|[.\s_-]?Rip|[.\s_-]?HD|[.\s_-]?Cap)|WEBDL|WEBRip|WEBHD|WEBCap|WEB|VODRip|"
    r"BluRay(?:[.\s_-]?Screener)?|UHD(?:[.\s_-]?BluRay)?|BDRip|BRRip|BDScr|REMUX|HDRip|PDRip|"
    r"HDTV|PDTV|SDTV|TVRip|SATRip|DSR(?:ip)?|DVB(?:Rip)?|"
    r"DVDRip|DVD(?:5|9|[.\s_-]?R|[.\s_-]?Screener)?|DVDSCR|"
    r"VHS(?:Rip)?|Laserdisc|DDC|WP|CAM(?:Rip)?|TS|TC|R5(?:[.\s_-]?Line)?|"
    r"Telesync|Telecine|DCP|HC[.\s_-]?HD[.\s_-]?Rip"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_TV_PACK_EXTRA_RE = re.compile(
    r"(?:^|[.\s_\[-])(?:sample|samples|nced|ncop|op|ed|ova|oad|preview|pv|proof|screens?|subs?|subtitles?|extras?|featurettes?|trailer|teaser|special|specials)(?:[.\s_\]-]|$)",
    re.IGNORECASE,
)

_TV_PACK_HARD_EXTRA_RE = re.compile(
    r"(?:^|[.\s_\[-])(?:sample|samples|nced|ncop|op|ed|ova|oad|preview|pv|proof|screens?|subs?|subtitles?|featurettes?|trailer|teaser|special|specials)(?:[.\s_\]-]|$)",
    re.IGNORECASE,
)

_TV_PACK_EXTRAS_WORD_RE = re.compile(r"(?:^|[.\s_\[-])extras?(?:[.\s_\]-]|$)", re.IGNORECASE)

_ANIME_LOOKUP_CLEAN_RE = re.compile(
    r"\b(?:"
    r"S\d{1,2}E\d{1,3}|S\d{1,2}|E\d{1,3}|\d{1,2}x\d{2,3}|Episode\s?\d{1,3}|Season\s?\d{1,2}|Series\s?\d{1,2}"
    r"|(?:19|20)\d{2}"
    r"|\d{3,4}p|1080i|WEB(?:-DL)?|BluRay|BDRip|BRRip|Remux|Hybrid|DVD|DVDRip|HDTV|UHD|FullBR"
    r"|HEVC|AVC|x26[45]|H\.?26[45]|10bit|AAC\d?(?:\.\d)?|DTS(?:-HD)?|FLAC|MULTI|PROPER|REPACK|LIMITED|INTERNAL"
    r")\b",
    re.IGNORECASE,
)

_SERIES_SIGNATURE_STRIP_RE = re.compile(
    r"\b(?:"
    r"S\d{1,2}[.\s_-]*E\d{1,3}"
    r"|\d{1,2}x\d{2,3}"
    r"|E\d{1,3}"
    r"|(?:19|20)\d{2}"
    r"|\d{3,4}p"
    r"|WEB(?:-DL)?|BluRay|BDRip|BRRip|Remux|Hybrid|DVD|DVDRip"
    r"|HEVC|AVC|x26[45]|H\.?26[45]|10bit|AAC\d?(?:\.\d)?|DTS(?:-HD)?|FLAC"
    r"|PROPER|REPACK|LIMITED|INTERNAL|MULTI"
    r")\b",
    re.IGNORECASE,
)

_AUDIOBOOK_HINT_RE = re.compile(
    r"\b(?:"
    r"audio[.\s_-]?books?|audiobooks?|podcasts?|m4b|lectures?|unabridged|abridged"
    r"|narrators?|narrated(?:[.\s_-]+by)?|read[.\s_-]+by"
    r")\b",
    re.IGNORECASE,
)

_EBOOK_HINT_RE = re.compile(r"\b(?:e[.\s_-]?books?|ebooks?|books?|comics?|manga|pdfs?|epubs?)\b", re.IGNORECASE)

_MUSIC_RELEASE_HINT_RE = re.compile(
    r"\b(?:albums?|discography|soundtracks?|ost|ep|singles?|cd[.\s_-]?\d*|flac)\b",
    re.IGNORECASE,
)

_AUDIOBOOK_CHAPTER_RE = re.compile(
    r"(?:^|[.\s_-])(?:chapter|ch|part|book)[.\s_-]*\d{1,4}(?:[.\s_-]|$)",
    re.IGNORECASE,
)

_AMBIGUOUS_TRACK_RE = re.compile(r"(?:^|[.\s_-])track[.\s_-]*\d{1,4}(?:[.\s_-]|$)", re.IGNORECASE)

# Anime extras (NCOP/NCED/creditless openings and endings): the Pending page and auto-upload
# both file these as anime.
ANIME_BONUS_RE = re.compile(
    r"(?i)(?:\bncop\b|\bnced\b|\bcreditless\b|(?:^|[.\s_-])op\d{0,2}(?:$|[.\s_-])|(?:^|[.\s_-])ed\d{0,2}(?:$|[.\s_-]))"
)


_LEGACY_EPISODE_NUMBER_RE = re.compile(
    r"^(?P<prefix>.+?)(?:[.\s_-]+)(?P<code>[1-9]\d{2,3})(?:[.\s_-]+)(?P<suffix>.+)$",
    re.IGNORECASE,
)


def _normalize_release_segment(value: str) -> str:
    cleaned = Path(value).stem
    cleaned = re.sub(r"[\[\](){}]+", " ", cleaned)
    cleaned = re.sub(r"[._-]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def has_multi_file_episode_pattern(names: List[str], *, min_matches: int = 2) -> bool:
    """Return True when multiple names share legacy episodic numbering.

    This catches older TV releases that use repeated numeric episode codes like
    ``101``, ``102``, ``1001`` instead of explicit ``S01E01`` tags.
    """
    min_matches = max(min_matches, 2)

    matches_by_prefix: Dict[str, set[str]] = {}
    for raw_name in names:
        match = _LEGACY_EPISODE_NUMBER_RE.match(Path(raw_name).stem)
        if not match:
            continue

        code = match.group("code")
        code_value = int(code)
        if 1900 <= code_value <= 2099:
            continue

        prefix = _normalize_release_segment(match.group("prefix"))
        suffix = _normalize_release_segment(match.group("suffix"))
        if len(prefix) < 4 or len(suffix) < 2:
            continue

        seen_codes = matches_by_prefix.setdefault(prefix, set())
        seen_codes.add(code)
        if len(seen_codes) >= min_matches:
            return True

    return False
