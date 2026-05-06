# -*- coding: utf-8 -*-
import logging
from datetime import UTC, datetime

from telegram.error import NetworkError

from db.database import SessionLocal
from db.models import PublishedAnime
from bot.formatter import format_anime_post
from bot.telegram_sender import send_post_with_cache

logger = logging.getLogger(__name__)


def publish_anime(anime, *, dry_run: bool = False) -> None:
    """
    Single entry point for publishing an anime post.

    Steps:
      1. Format the post text.
      2. Send to Telegram with file_id caching (skipped in dry_run mode).
      3. Record the publication in PublishedAnime (skipped in dry_run mode).

    The Telegram send and the DB write share a single commit so they succeed
    or roll back together — no orphaned records or silent double-posts.
    """
    session = SessionLocal()
    try:
        title = getattr(anime, "title_ru", None) or getattr(anime, "title_en", None) or "Без названия"
        logger.info("Publishing anime: %s (dry_run=%s)", title, dry_run)

        post_text = format_anime_post(anime)

        if dry_run:
            logger.info("DRY RUN: message NOT sent, DB NOT updated")
            logger.debug("DRY RUN text:\n%s", post_text)
            return

        # send_post_with_cache: downloads photo → sends → saves file_id in session (no commit yet)
        send_post_with_cache(text=post_text, anime=anime, session=session)

        pub = PublishedAnime(
            anime_id=anime.id,
            status=anime.status,
            episodes=anime.episodes_aired,
            published_at=datetime.now(UTC),
        )
        session.add(pub)
        session.commit()  # single commit: tg_file_id update + PublishedAnime insert

        logger.info("Published: %s", title)

    except NetworkError as e:
        # Telegram network / DNS / timeout error — log a warning and re-raise.
        # The caller (post_cycle) handles the retry / skip logic.
        session.rollback()
        logger.warning("Telegram network error during publish: %s", e)
        raise
    except Exception:
        session.rollback()
        logger.exception("Unexpected error while publishing anime")
        raise

    finally:
        session.close()
