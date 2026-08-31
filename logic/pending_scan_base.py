# Auto-split from pending_scan.py - verbatim symbol bodies, synthesized imports.

from __future__ import annotations
import contextvars
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Set, Tuple
from core.release_name import parse_release_name
from core.utils import (
    AUDIOBOOK_EXTENSIONS,
    EBOOK_EXTENSIONS,
    MUSIC_EXTENSIONS,
    VIDEO_EXTENSIONS,
    has_multi_file_episode_pattern,
)
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
_TV_PACK_SOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"WEB(?:[.\s_-]?DL|[.\s_-]?Rip|[.\s_-]?HD)?|WEBDL|WEBRip|WEBHD|BluRay|BDRip|BRRip|REMUX|HDRip|PDRip|HDTV|PDTV|"
    r"SDTV|TV|TVRip|SATRip|DSR|DVB|DVDRip|DVD|VHS(?:Rip)?|DV|UHD|AMZN|NF|NFLX|DSNP|PCOK|HMAX|MAX|HULU|ATVP|AUBC|iT|iP|STAN|CR|"
    r"PMTP|PMNT|CTV|CBC|BBC|PBS|TBS|TNT|NBC|ABC|CBS|FOX|HBO|SHOWTIME|SHO|MIXED"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_TV_PACK_EXTRA_RE = re.compile(
    r"(?:^|[.\s_\[-])(?:sample|samples|nced|ncop|proof|screens?|subs?|subtitles?|extras?|featurettes?|trailer)(?:[.\s_\]-]|$)",
    re.IGNORECASE,
)
_TV_PACK_HARD_EXTRA_RE = re.compile(
    r"(?:^|[.\s_\[-])(?:sample|samples|nced|ncop|proof|screens?|subs?|subtitles?|featurettes?|trailer)(?:[.\s_\]-]|$)",
    re.IGNORECASE,
)
_TV_PACK_EXTRAS_WORD_RE = re.compile(r"(?:^|[.\s_\[-])extras?(?:[.\s_\]-]|$)", re.IGNORECASE)
DEFAULT_TV_PACK_IGNORE_RULES: dict[str, bool] = {
    "enabled": True,
    "ignore_non_video": True,
    "ignore_extras": True,
    "require_sxxexx": True,
    "require_resolution": False,
    "require_source": True,
}
TV_PACK_IGNORE_RULE_LABELS: dict[str, str] = {
    "ignore_non_video": "Non-video files",
    "ignore_extras": "Samples, NFO, proof, screens, subtitles, extras, featurettes, trailers, NCOP, NCED",
    "require_sxxexx": "Files without S##E## episode numbering",
    "require_resolution": "Files without quality/format such as NTSC/PAL/480i/480p/576i/576p/720p/1080p/2160p",
    "require_source": "Files without a media source token such as WEB, WEB-DL, WEBRip, BluRay, BRRip, HDRip, PDRip, REMUX, HDTV, SDTV, PDTV, TV, DVD, DVDRip, VHS",
}
TV_PACK_IGNORED_FILE_TYPES: tuple[str, ...] = (
    ".nfo",
    ".txt",
    ".srt",
    ".ass",
    ".ssa",
    ".sub",
    ".idx",
    ".sup",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".sfv",
    ".md5",
    ".par2",
    ".url",
)
TV_PACK_IGNORED_NAME_PATTERNS: tuple[str, ...] = (
    "sample",
    "samples",
    "nfo",
    "proof",
    "screen",
    "screens",
    "subs",
    "subtitles",
    "extras",
    "featurette",
    "featurettes",
    "trailer",
    "ncop",
    "nced",
)
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
_AUTO_ROOT_CATEGORIES = {"", "external", "auto"}
_DISC_IMAGE_EXTENSIONS = {".iso", ".img", ".mdf", ".mds", ".nrg"}
_DISC_STRUCTURE_DIRS = {"bdmv", "certificate", "video_ts"}
_APP_FILE_EXTENSIONS = {
    ".exe",
    ".msi",
    ".apk",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
    ".zip",
    ".rar",
    ".7z",
} | _DISC_IMAGE_EXTENSIONS
_FOLDER_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("audiobooks", ("audiobook", "audiobooks", "audio book", "audio books", "podcast", "podcasts", "m4b")),
    ("books", ("ebook", "ebooks", "book", "books", "comic", "comics", "manga", "pdf", "epub")),
    ("music", ("music", "album", "albums", "flac", "mp3", "discography")),
    ("apps", ("app", "apps", "game", "games", "software", "program", "programs")),
    ("anime", ("anime", "animes", "seasonal")),
    ("tv", ("tv", "show", "shows", "series", "season", "television")),
    ("movies", ("movie", "movies", "film", "films")),
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
_ITYPE_TO_CATEGORY = {
    "tv show": "tv",
    "tv episode": "tv",
    "tv pack": "tv",
    "season pack": "tv",
    "anime": "anime",
    "movie": "movies",
    "movie pack": "movies",
    "music": "music",
    "audiobook": "audiobooks",
    "ebook": "books",
    "app": "apps",
    "disc": "disc",
    "misc": "misc",
    "unknown": "misc",
}
_SCAN_CACHE_VAR: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_pending_scan_walk_cache",
    default=None,
)

__all__ = [
    'AUDIOBOOK_EXTENSIONS', 'Any', 'Callable', 'DEFAULT_TV_PACK_IGNORE_RULES', 'EBOOK_EXTENSIONS', 'Iterable',
    'List', 'MUSIC_EXTENSIONS', 'Optional', 'Path', 'Set', 'TV_PACK_IGNORED_FILE_TYPES',
    'TV_PACK_IGNORED_NAME_PATTERNS', 'TV_PACK_IGNORE_RULE_LABELS', 'Tuple', 'VIDEO_EXTENSIONS',
    '_AMBIGUOUS_TRACK_RE', '_ANIME_ABSOLUTE_EPISODE_RE', '_ANIME_EXTRA_RE', '_ANIME_LOOKUP_CLEAN_RE',
    '_ANIME_SEASONAL_FOLDER_RE', '_APP_FILE_EXTENSIONS', '_AUDIOBOOK_CHAPTER_RE', '_AUDIOBOOK_HINT_RE',
    '_AUTO_ANIME_FANSUB_RE', '_AUTO_COMPLETE_SERIES_RANGE_RE', '_AUTO_MOVIE_PATTERNS', '_AUTO_ROOT_CATEGORIES',
    '_AUTO_SEASON_TOKEN_RE', '_AUTO_TV_PATTERNS', '_AUTO_YEAR_RANGE_RE', '_AUTO_YEAR_TOKEN_RE',
    '_BARE_HIGH_ABSOLUTE_EPISODE_RE', '_COMPLETE_MINISERIES_RE', '_DATE_EPISODE_RE', '_DISC_IMAGE_EXTENSIONS',
    '_DISC_STRUCTURE_DIRS', '_EBOOK_HINT_RE', '_EXPLICIT_EPISODE_RE', '_FOLDER_CATEGORY_HINTS',
    '_FRAMED_ABSOLUTE_EPISODE_RE', '_GENERIC_ANIME_FOLDER_RE', '_GENERIC_TV_SEASON_FOLDER_RE',
    '_GUESSIT_EPISODE_SHAPE_RE', '_ITYPE_TO_CATEGORY', '_MOVIE_COLLECTION_RE', '_MUSIC_RELEASE_HINT_RE',
    '_SCAN_CACHE_VAR', '_SERIES_SIGNATURE_STRIP_RE', '_SPORTS_EVENT_CONTEXT_RE', '_SPORTS_LEAGUE_RE',
    '_STREAMING_EPISODE_SOURCE_RE', '_TRAILING_RELEASE_GROUP_YEAR_RE', '_TV_EPISODE_HINT_RE',
    '_TV_PACK_EPISODE_RE', '_TV_PACK_EXTRAS_WORD_RE', '_TV_PACK_EXTRA_RE', '_TV_PACK_HARD_EXTRA_RE',
    '_TV_PACK_RESOLUTION_RE', '_TV_PACK_SOURCE_RE', '_TV_PART_EPISODE_RE', 'annotations', 'contextvars',
    'dataclass', 'has_multi_file_episode_pattern', 'os', 'parse_release_name', 're', 'unicodedata',
]
