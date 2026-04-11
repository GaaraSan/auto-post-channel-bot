import random
import logging
import sys
from sqlalchemy.orm import joinedload  # type: ignore[reportMissingImports]

from db.database import SessionLocal
from db.models import Anime, PublishedAnime
from app.priority_weights import (
    WINDOW_SIZE,
    get_publication_stats,
    get_penalty_multipliers,
    get_penalty_for_anime,
)

from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# Последняя ошибка выбора (для сервисов/управления через Telegram).
# Нужна, чтобы отличать "нет кандидатов" от "упали при выборе".
LAST_SELECT_ERROR: Exception | None = None

COOLDOWN_DAYS = {
    "released": 90,
    "ongoing": 14,
    "anons": 30,
}

def calculate_weight(anime) -> float:
    """
    Чем больше вес — тем раньше аниме
    попадёт в случайной сортировке
    """

    weight = 1.0

    # 🆕 новизна
    if anime.year:
        if anime.year >= 2022:
            weight *= 2.0
        elif anime.year >= 2015:
            weight *= 1.5
        elif anime.year >= 2005:
            weight *= 1.0
        else:
            weight *= 0.7

    # 🔥 статус
    if anime.status == "ongoing":
        weight *= 1.8
    elif anime.status == "anons":
        weight *= 1.3

    return weight

def get_random_anime():
    session = SessionLocal()

    try:
        global LAST_SELECT_ERROR
        LAST_SELECT_ERROR = None

        stats = get_publication_stats(session, WINDOW_SIZE)
        penalties = get_penalty_multipliers(stats)

        animes = (
            session.query(Anime)
            .options(joinedload(Anime.genres))
            .all()
        )

        def effective_weight(anime):
            base = calculate_weight(anime)
            penalty = get_penalty_for_anime(anime, penalties)
            return base * penalty

        animes = sorted(
            animes,
            key=lambda a: random.random() / effective_weight(a),
        )

        for anime in animes:
            last_pub = (
                session.query(PublishedAnime)
                .filter(PublishedAnime.anime_id == anime.id)
                .order_by(PublishedAnime.published_at.desc())
                .first()
            )

            if can_publish(anime, last_pub):
                return anime

        logger.debug("Нет аниме, подходящих под условия публикации")
        return None

    except Exception:
        LAST_SELECT_ERROR = sys.exc_info()[1]  # type: ignore[assignment]
        logger.exception("Ошибка при выборе аниме")
        return None

    finally:
        session.close()


def can_publish(anime, last_pub) -> bool:
    """
    Решает, можно ли публиковать аниме снова
    """

    # если никогда не публиковалось
    if not last_pub:
        return True

    now = datetime.now(UTC)

    # 1️⃣ если ongoing и вышли новые серии
    if anime.status == "ongoing":
        if (
            anime.episodes_aired
            and last_pub.episodes
            and anime.episodes_aired > last_pub.episodes
        ):
            return True

    # 2️⃣ cooldown по статусу
    cooldown_days = COOLDOWN_DAYS.get(anime.status, 60)
    last_dt = last_pub.published_at
    # SQLite/SQLAlchemy часто возвращает naive datetime; считаем его UTC для совместимости
    if getattr(last_dt, "tzinfo", None) is None:
        last_dt = last_dt.replace(tzinfo=UTC)
    cooldown_until = last_dt + timedelta(days=cooldown_days)

    return now >= cooldown_until


if __name__ == "__main__":
    from app.logging_config import setup_logging
    setup_logging()
    anime = get_random_anime()
    if anime:
        logger.info(
            "Найдено аниме для публикации: %s (год=%s, статус=%s)",
            anime.title_ru, anime.year, anime.status
        )
    else:
        logger.warning("Нет подходящих аниме для публикации")
