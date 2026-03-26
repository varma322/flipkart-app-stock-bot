"""Cleanup old screenshots to prevent unbounded disk usage."""

import os
import time
from utils.logger import logger

SCREENSHOT_DIR = "screenshots"
MAX_AGE_DAYS = 3


def cleanup_old_screenshots(max_age_days=MAX_AGE_DAYS):
    """Delete screenshots older than `max_age_days` days."""
    if not os.path.isdir(SCREENSHOT_DIR):
        return

    cutoff = time.time() - (max_age_days * 86400)
    deleted = 0

    for fname in os.listdir(SCREENSHOT_DIR):
        fpath = os.path.join(SCREENSHOT_DIR, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            try:
                os.remove(fpath)
                deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete old screenshot {fpath}: {e}")

    if deleted:
        logger.info(f"🧹 Cleaned up {deleted} old screenshot(s)")
