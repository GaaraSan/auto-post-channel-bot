import math
import random
import logging
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import joinedload

from db.database import SessionLocal
from db.models import Anime, PublishedAnime
from app.priority_weights import (
    WINDOW_SIZE,
    get_publication_stats,
    get_penalty_multipliers,
    get_penalty_for_anime,
)

logger = logging.getLogger(__name__)

# Последняя ошибка выбора (для сервисов/управления через Telegram).
LAST_SELECT_ERROR: Exception | None = None

# ---------------------------------------------------------------------------
# Конфигурация cooldown
# ---------------------------------------------------------------------------

COOLDOWN_DAYS = {
    "released": 90,
    "ongoing": 14,
    "anons": 30,
}

# Минимальный cooldown для ongoing, даже если вышли новые серии
ONGOING_MIN_COOLDOWN_DAYS = 2

# ---------------------------------------------------------------------------
# Конфигурация фильтра качества (убрать мусор)
# ---------------------------------------------------------------------------

EXCLUDED_KINDS = {"music", "cm", "pv"}   # реклама и клипы
MIN_MEMBERS    = 500                      # минимально популярность
MIN_RATING     = 3.0                      # очевидный хлам
MIN_DESC_LEN   = 120                      # мин. длина описания

# SQL LIMIT: сколько записей подтягиваем в Python для скоринга
CANDIDATE_POOL = 500
# TOP-N перед random.choice
TOP_N          = 50

# ---------------------------------------------------------------------------
# Скоринг (Python-сторона, чтобы не зависеть от log10 в SQLite)
# ---------------------------------------------------------------------------

def calculate_weight(anime) -> float:
    """
    Гибридный вес для случайной сортировки.
    Основа: rating + популярность (members) + бонус за новизну/статус.
    """
    current_year = datetime.now(UTC).year

    # База: rating (если NULL — нейтральное 5.5) + log10(members)
    rating_part  = (anime.rating or 5.5) * 2.0
    members_part = math.log10((anime.members or 1) + 1) * 3.0
    score = rating_part + members_part

    # Бонус: новинка последних двух лет
    if anime.year and anime.year >= current_year - 1:
        score += 3.0

    # Бонус: сериальный (хотя бы 12 серий)
    if anime.episodes and anime.episodes >= 12:
        score += 1.5

    # Лёгкий штраф: односерийный (но НЕ заблокируем фильмы!)
    if anime.episodes == 1:
        score -= 0.5

    # Бонус статуса
    if anime.status == "ongoing":
        score += 2.0
    elif anime.status == "anons":
        score += 1.0

    return max(score, 0.1)  # всегда положительный

def get_random_anime():
    session = SessionLocal()

    try:
        global LAST_SELECT_ERROR
        LAST_SELECT_ERROR = None

        # ШТАП 1: статистика предыдущих пуБликаций (для self-correction)
        stats    = get_publication_stats(session, WINDOW_SIZE)
        penalties = get_penalty_multipliers(stats)

        # ШТАП 2: SQL-фильтрация — только качественный пул pool (без N+1)
        # ————————————————————————————————————————————————————————————————————————
        # Один SQL-запрос, нет N+1, нет .all() на всю таблицу

        # Retry: если eligible пустой — расширяем пул вдвое (без дублирования кода)
        eligible = []
        for pool_limit in (CANDIDATE_POOL, CANDIDATE_POOL * 2):
            candidates = (
                session.query(Anime)
                .options(joinedload(Anime.genres))
                .filter(
                    # ФИЛЬТРАЦИЯ КАЧЕСТВА — убираем мусор
                    Anime.members.isnot(None),
                    Anime.members >= MIN_MEMBERS,
                    Anime.image_url.isnot(None),
                    Anime.description.isnot(None),
                    Anime.genres.any(),             # хоть один жанр (без JOIN)
                    # rating IS NULL — OK (новинки), только очевидный хлам выкидываем
                    ~(
                        Anime.rating.isnot(None)
                        & (Anime.rating < MIN_RATING)
                    ),
                    # исключаем рекламу/клипы
                    ~Anime.kind.in_(list(EXCLUDED_KINDS)),
                )
                .order_by(Anime.members.desc(), Anime.rating.desc())
                .limit(pool_limit)
                .all()
            )

            if not candidates:
                logger.debug("Нет кандидатов после SQL-фильтрации (pool=%d)", pool_limit)
                break

            # ШТАП 3: Python-скоринг (нет N+1, всё уже в памяти)
            candidate_ids = [a.id for a in candidates]
            last_pubs = (
                session.query(PublishedAnime)
                .filter(PublishedAnime.anime_id.in_(candidate_ids))
                .order_by(PublishedAnime.published_at.desc())
                .all()
            )
            # last_pub_map: anime_id -> последняя запись PublishedAnime
            last_pub_map: dict = {}
            for pub in last_pubs:
                if pub.anime_id not in last_pub_map:
                    last_pub_map[pub.anime_id] = pub

            eligible = [
                a for a in candidates
                if (
                    len(a.description or "") >= MIN_DESC_LEN
                    and can_publish(a, last_pub_map.get(a.id))
                )
            ]

            if eligible:
                break
            logger.warning(
                "Нет аниме после cooldown/description (pool=%d) — расширяю пул...",
                pool_limit,
            )

        if not eligible:
            logger.warning("Нет аниме после фильтра cooldown/description даже с расширенным пулом")
            return None

        # Скоринг: calculate_weight + penalty из priority_weights
        def effective_weight(a):
            base    = calculate_weight(a)
            penalty = get_penalty_for_anime(a, penalties)
            return base * penalty

        # Сортируем по score DESC + цепляем верх TOP_N
        eligible.sort(key=effective_weight, reverse=True)
        top = eligible[:TOP_N]

        # Случайный выбор из TOP_N — нет зацикливания на одном тайтле
        chosen = random.choice(top)
        logger.debug(
            "Выбрано: %s (год=%s, members=%s, weight=%.2f)",
            chosen.title_ru, chosen.year, chosen.members, effective_weight(chosen)
        )
        return chosen

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

    # Нормализуем datetime: SQLite часто возвращает naive — считаем его UTC
    last_dt = last_pub.published_at
    if getattr(last_dt, "tzinfo", None) is None:
        last_dt = last_dt.replace(tzinfo=UTC)

    # 1️⃣ если ongoing и вышли новые серии — с минимальным cooldown
    if anime.status == "ongoing":
        if (
            anime.episodes_aired
            and last_pub.episodes
            and anime.episodes_aired > last_pub.episodes
        ):
            # Новые серии есть, но всё равно ждём ONGOING_MIN_COOLDOWN_DAYS
            return now >= last_dt + timedelta(days=ONGOING_MIN_COOLDOWN_DAYS)

    # 2️⃣ cooldown по статусу
    cooldown_days = COOLDOWN_DAYS.get(anime.status, 60)
    return now >= last_dt + timedelta(days=cooldown_days)


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
