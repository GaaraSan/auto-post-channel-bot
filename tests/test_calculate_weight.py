"""
Tests for calculate_weight — the numeric anime scoring logic.

All tests are isolated: use SimpleNamespace only, no DB required.
"""
from datetime import UTC, datetime
from types import SimpleNamespace

from scripts.get_random_anime import calculate_weight

_CURRENT_YEAR = datetime.now(UTC).year


def _anime(**kwargs) -> SimpleNamespace:
    """Minimal anime object with sensible defaults."""
    defaults = dict(
        rating=7.0, members=5000, year=_CURRENT_YEAR - 5,
        episodes=12, status="released",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_calculate_weight_always_positive():
    """Weight is always > 0 even for a worst-case anime."""
    worst = _anime(rating=0.1, members=1, year=1970, episodes=1, status="released")
    assert calculate_weight(worst) > 0


def test_calculate_weight_recent_anime_gets_bonus():
    """Current-year anime gets a +5 bonus over anime from 10 years ago."""
    recent = _anime(year=_CURRENT_YEAR)
    old    = _anime(year=_CURRENT_YEAR - 10)
    assert calculate_weight(recent) > calculate_weight(old)


def test_calculate_weight_old_anime_gets_penalty():
    """Anime older than 15 years has a lower weight than 5-year-old anime."""
    very_old = _anime(year=_CURRENT_YEAR - 20)
    mid      = _anime(year=_CURRENT_YEAR - 5)
    assert calculate_weight(very_old) < calculate_weight(mid)


def test_calculate_weight_status_bonus():
    """Ongoing status gets a higher bonus than released."""
    ongoing  = _anime(status="ongoing")
    released = _anime(status="released")
    assert calculate_weight(ongoing) > calculate_weight(released)
