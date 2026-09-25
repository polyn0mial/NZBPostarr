"""Serve the web app for the browser smoke tests, isolated from the checkout's runtime state.

Usage: python tests/e2e/serve_app.py <state dir> <port>

NZBPOSTARR_CONFIG must point at the fixture config. The history DB, the tmp, NZB and
mediainfo folders (which the startup purge empties), the updater state and the caches
normally live under <app root>/data, so they are redirected into the state dir before
the app is imported.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _state_path(root: Path, *parts: str, is_dir: bool = True) -> property:
    def _path(_self: object) -> Path:
        path = root.joinpath(*parts)
        (path if is_dir else path.parent).mkdir(parents=True, exist_ok=True)
        return path

    return property(_path)


def _seed_history() -> None:
    from core import database as db

    db.init_database()
    db.record_nntp_success("/media/TV/Example.Show.S01E01.1080p.WEB-DL.x264-GRP", 734003200, "TV Episode")


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2])

    from core import config as config_mod

    config_mod.Config.log_db = _state_path(root, "history", "usenet_uploads.db", is_dir=False)  # type: ignore[method-assign]
    config_mod.Config.tmp_sub = _state_path(root, "tmp")  # type: ignore[method-assign]
    config_mod.Config.nzb_sub = _state_path(root, "nzbs")  # type: ignore[method-assign]
    config_mod.Config.mediainfo_sub = _state_path(root, "mediainfo")  # type: ignore[method-assign]

    from logic import updater
    from logic.pending import overrides as pending_overrides
    from logic.classify import anime as anime_cache

    updater.STATE_DIR = root / "updater"
    updater.STATE_FILE = updater.STATE_DIR / "state.json"
    updater.BACKUP_DIR = updater.STATE_DIR / "backups"
    anime_cache._cache_path = root / "cache" / "anime.json"
    pending_overrides._path = root / "category_overrides.json"

    _seed_history()

    import uvicorn

    import app as app_mod

    uvicorn.run(app_mod.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
