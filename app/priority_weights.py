# -*- coding: utf-8 -*-
"""
Система самокоррекции приоритетов публикации.

Снижает приоритет категорий (жанр, год, статус), перепредставленных
в последних N публикациях. Не меняет cooldown и can_publish.
Веса считаются из скользящего окна — возврат к норме автоматический.
"""

from collections import defaultdict
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from db.models import PublishedAnime, Anime


# --- Конфигурируемые коэффициенты (прозрачные, объяснимые) ---

# Размер окна последних публикаций для подсчёта перекоса
WINDOW_SIZE = 30

# Минимальный множитель: категория не может быть полностью «заглушена»
MIN_PENALTY = 0.3

# Годовые корзины для анализа перекоса по году выпуска
# (name, min_year включительно). Порядок: от старых к новым.
YEAR_BUCKETS = [
    ("old", None),       # year < 2000 или None
    ("2000-2009", 2000),
    ("2010-2014", 2010),
    ("2015-2019", 2015),
    ("2020+", 2020),
]


def _year_bucket(year: Optional[int]) -> str:
    """Определяет корзину года выпуска."""
    if year is None:
        return "old"
    for name, min_year in reversed(YEAR_BUCKETS):
        if min_year is not None and year >= min_year:
            return name
    return "old"


def get_publication_stats(session: Session, n: int = WINDOW_SIZE) -> dict:
    """
    Собирает статистику по последним n публикациям.

    Возвращает:
      - "status": { status_str: count }
      - "year_bucket": { bucket_name: count }
      - "genre_id": { genre_id: count }
    """
    pub_list = (
        session.query(PublishedAnime)
        .options(
            joinedload(PublishedAnime.anime).joinedload(Anime.genres)
        )
        .order_by(PublishedAnime.published_at.desc())
        .limit(n)
        .all()
    )

    status_count = defaultdict(int)
    year_count = defaultdict(int)
    genre_count = defaultdict(int)

    for pa in pub_list:
        anime = pa.anime
        if not anime:
            continue
        status_count[anime.status or "unknown"] += 1
        year_count[_year_bucket(anime.year)] += 1
        for g in anime.genres or []:
            genre_count[g.id] += 1

    return {
        "_n": len(pub_list),
        "status": dict(status_count),
        "year_bucket": dict(year_count),
        "genre_id": dict(genre_count),
    }


def get_penalty_multipliers(stats: dict, n: Optional[int] = None) -> dict:
    """
    По статистике последних n публикаций считает множители (< 1 = понижение приоритета).
    n: размер окна; если не передан, берётся stats["_n"] или WINDOW_SIZE.

    Возвращает:
      - "status": { status_str: multiplier }
      - "year_bucket": { bucket_name: multiplier }
      - "genre_id": { genre_id: multiplier }
    """
    n = n if n is not None else stats.get("_n", WINDOW_SIZE)

    def penalty_for_counts(counts: dict, num_categories: int) -> dict:
        if not counts or n <= 0:
            return {k: 1.0 for k in counts}
        expected = n / max(num_categories, 1)
        result = {}
        for cat, actual in counts.items():
            if actual <= 0:
                result[cat] = 1.0
            else:
                raw = expected / actual
                result[cat] = max(MIN_PENALTY, min(1.0, raw))
        return result

    num_statuses = max(len(stats.get("status", {})), 1)
    num_year_buckets = max(len(stats.get("year_bucket", {})), 1)
    num_genres = max(len(stats.get("genre_id", {})), 1)

    return {
        "status": penalty_for_counts(stats.get("status", {}), num_statuses),
        "year_bucket": penalty_for_counts(stats.get("year_bucket", {}), num_year_buckets),
        "genre_id": penalty_for_counts(stats.get("genre_id", {}), num_genres),
    }


def get_penalty_for_anime(anime: Anime, penalties: dict) -> float:
    """
    Итоговый множитель для одного аниме по предпосчитанным penalties.

    Умножается на базовый вес; чем меньше — тем ниже приоритет.
    """
    status_mult = penalties["status"].get(anime.status or "unknown", 1.0)
    year_mult = penalties["year_bucket"].get(_year_bucket(anime.year), 1.0)

    genre_mults = []
    for g in anime.genres or []:
        genre_mults.append(penalties["genre_id"].get(g.id, 1.0))
    genre_mult = 1.0
    if genre_mults:
        # произведение по жанрам — несколько перепредставленных жанров сильнее снижают
        for m in genre_mults:
            genre_mult *= m

    return status_mult * year_mult * genre_mult
