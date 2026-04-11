# -*- coding: utf-8 -*-
import logging
from datetime import UTC, datetime

from db.database import SessionLocal
from db.models import PublishedAnime
from bot.formatter import format_anime_post
from bot.telegram_sender import send_post

logger = logging.getLogger(__name__)


def publish_anime(anime, *, dry_run: bool = False) -> None:
    """
    Единственная точка публикации аниме.

    Делает:
    - формирование текста поста;
    - отправку в Telegram (если dry_run=False);
    - запись факта публикации в PublishedAnime (если dry_run=False);
    - логирование ключевых шагов и ошибок.
    """
    session = SessionLocal()
    try:
        title = getattr(anime, "title_ru", None) or getattr(anime, "title_en", None) or "Без названия"
        logger.info("Публикация аниме: %s (dry_run=%s)", title, dry_run)

        post_text = format_anime_post(anime)

        if dry_run:
            logger.info("DRY RUN: сообщение НЕ отправлено и НЕ записано в БД")
            logger.debug("DRY RUN текст:\n%s", post_text)
            return

        send_post(text=post_text, image_url=getattr(anime, "image_url", None))

        pub = PublishedAnime(
            anime_id=anime.id,
            status=anime.status,
            episodes=anime.episodes_aired,
            published_at=datetime.now(UTC),
        )
        session.add(pub)
        session.commit()

        logger.info("Публикация завершена: %s", title)

    except Exception:
        session.rollback()
        logger.exception("Ошибка публикации аниме")
        raise

    finally:
        session.close()

