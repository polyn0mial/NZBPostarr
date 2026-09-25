"""Removal of an item's temporary pipeline artifacts (NZB, rar/par tmp folder, mediainfo)."""

from __future__ import annotations

import shutil
import time

from loguru import logger

from core.config import get_config
from core.logging import log_verbose


def purge_item_data(name: str) -> None:
    """Complete cleanup of temporary data for a given item name."""

    conf = get_config()

    # 1. Purge NZB
    nzb_path = conf.get_nzb_path(name)
    if nzb_path.exists():
        try:
            nzb_path.unlink()
        except OSError:
            pass

    # 2. Purge TMP folder (Rar/Par artifacts)
    tmp_folder = conf.tmp_sub / name
    if tmp_folder.exists():
        shutil.rmtree(tmp_folder, ignore_errors=True)

    # 3. Purge Mediainfo
    info_path = conf.mediainfo_sub / f"{name}.mediainfo.nfo"
    if info_path.exists():
        try:
            info_path.unlink()
        except OSError:
            pass

    log_verbose(f"Purged all temp data for: {name}")


def run_global_purge() -> None:
    """Clean all temporary internal directories (tmp, mediainfo, nzbs)."""

    conf = get_config()
    targets = [conf.tmp_sub, conf.mediainfo_sub, conf.nzb_sub]

    log_verbose("Running global purge of internal temporary directories...")
    start_time = time.time()
    count = 0

    for target in targets:
        if target.exists():
            # Optimization: Use a faster way to iterate and delete if many files exist
            for item in target.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                    count += 1
                except Exception as e:
                    logger.debug(f"Failed to purge {item}: {e}")

    elapsed = time.time() - start_time
    if count > 0:
        log_verbose(f"Global purge complete. Removed {count} items in {elapsed:.2f}s.")
    else:
        log_verbose("Global purge complete (no items to remove).")

