# -*- coding: utf-8 -*-
"""
Priority self-correction system.

Reduces the priority of categories (genre, year bucket, status) that are
over-represented in the last N publications. Does not affect cooldown logic.
Weights are recalculated from a sliding window, so balance is restored automatically.
"""

from collections import defaultdict
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from db.models import PublishedAnime, Anime


# --- Tunable coefficients ---

# Number of recent publications used to detect over-representation.
WINDOW_SIZE = 30

# Floor multiplier: no category can be silenced completely.
MIN_PENALTY = 0.3

# Year buckets for skew analysis (name, inclusive min_year), oldest first.
YEAR_BUCKETS = [
    ("old", None),       # year < 2000 or None
    ("2000-2009", 2000),
    ("2010-2014", 2010),
    ("2015-2019", 2015),
    ("2020+", 2020),
]


def _year_bucket(year: Optional[int]) -> str:
    """Map a release year to its bucket name."""
    if year is None:
        return "old"
    for name, min_year in reversed(YEAR_BUCKETS):
        if min_year is not None and year >= min_year:
            return name
    return "old"


def get_publication_stats(session: Session, n: int = WINDOW_SIZE) -> dict:
    """
    Aggregate statistics over the last n publications.

    Returns a dict with keys:
        "_n"          – actual number of records found
        "status"      – {status_str: count}
        "year_bucket" – {bucket_name: count}
        "genre_id"    – {genre_id: count}
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
    Compute penalty multipliers (< 1.0 means lower priority) from publication stats.

    Formula: multiplier = expected_share / actual_share, clamped to [MIN_PENALTY, 1.0].
    A balanced distribution yields 1.0 for all categories.

    Args:
        stats: output of get_publication_stats()
        n:     window size override; defaults to stats["_n"] or WINDOW_SIZE
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
    Compute the combined penalty multiplier for a single anime.

    Multiplies the status, year-bucket, and genre penalties together.
    Multiple over-represented genres compound the reduction (product, not average).
    """
    status_mult = penalties["status"].get(anime.status or "unknown", 1.0)
    year_mult = penalties["year_bucket"].get(_year_bucket(anime.year), 1.0)

    genre_mult = 1.0
    for g in anime.genres or []:
        genre_mult *= penalties["genre_id"].get(g.id, 1.0)

    return status_mult * year_mult * genre_mult
