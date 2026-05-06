"""
Integration tests for get_random_anime (scripts/get_random_anime.py).

Use SQLite in-memory via StaticPool.
Only SessionLocal is patched — real SQL logic runs in full.

Replaces test_get_random_anime.py which used the real anime.db.
"""
from unittest.mock import patch

import pytest

from db.models import Anime, Genre
from scripts.get_random_anime import get_random_anime


def test_get_random_anime_returns_result(session_factory, sample_anime):
    """If the DB contains a suitable anime, the function returns it."""
    with patch("scripts.get_random_anime.SessionLocal", session_factory):
        result = get_random_anime()

    assert result is not None
    assert result.id == sample_anime.id


def test_get_random_anime_returns_none_when_empty(session_factory):
    """Empty DB → None (not an exception)."""
    with patch("scripts.get_random_anime.SessionLocal", session_factory):
        result = get_random_anime()

    assert result is None


def test_get_random_anime_excludes_low_quality(session_factory, db_session):
    """
    Anime with members < MIN_MEMBERS (500) is rejected by the SQL quality filter.
    When no other candidates exist → None.
    """
    genre = Genre(id=10, name="Drama")
    bad_anime = Anime(
        shikimori_id=9999,
        title_ru="Плохое аниме",
        status="released",
        members=10,          # < MIN_MEMBERS=500 → filtered out
        rating=5.0,
        episodes=12,
        episodes_aired=12,
        year=2020,
        image_url="https://example.com/bad.jpg",
        description="А" * 150,
        kind="tv",
        genres=[genre],
    )
    db_session.add_all([genre, bad_anime])
    db_session.commit()

    with patch("scripts.get_random_anime.SessionLocal", session_factory):
        result = get_random_anime()

    assert result is None
