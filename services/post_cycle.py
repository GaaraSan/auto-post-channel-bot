# -*- coding: utf-8 -*-
import logging
import os

from scripts import get_random_anime as random_picker
from services.publisher import publish_anime
from app.lockfile import file_lock, LockAlreadyHeldError
from app.runtime_state import STATE

logger = logging.getLogger(__name__)


def run_post_cycle(*, dry_run: bool = False) -> dict:
    """
    Execute one posting cycle: pick an anime → publish → return a status dict.

    A file lock prevents concurrent cycles. The is_posting_now flag is set
    for the duration so /status can report an in-progress post.

    Return values:
        {"status": "ok",       "anime_id": int, "title": str}
        {"status": "no_anime"}
        {"status": "error",    "reason": "select_failed"}
        {"status": "locked"}
    """
    lock_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "posting.lock")
    try:
        with file_lock(lock_path):
            STATE.set_is_posting_now(True)

            anime = random_picker.get_random_anime()
            if not anime:
                if random_picker.LAST_SELECT_ERROR is not None:
                    logger.error("Anime selection failed; skipping cycle")
                    return {"status": "error", "reason": "select_failed"}
                logger.warning("No anime available for posting")
                return {"status": "no_anime"}

            publish_anime(anime, dry_run=dry_run)
            return {"status": "ok", "anime_id": getattr(anime, "id", None), "title": getattr(anime, "title_ru", None)}
    except LockAlreadyHeldError:
        logger.warning("Posting skipped: another cycle is already running (lock)")
        return {"status": "locked"}
    finally:
        STATE.set_is_posting_now(False)
