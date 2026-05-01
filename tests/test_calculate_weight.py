"""
Тесты calculate_weight — числовая логика скоринга аниме.

Все тесты изолированы: используют только SimpleNamespace, без БД.
"""
from datetime import UTC, datetime
from types import SimpleNamespace

from scripts.get_random_anime import calculate_weight

_CURRENT_YEAR = datetime.now(UTC).year


def _anime(**kwargs) -> SimpleNamespace:
    """Минимальный аниме-объект с дефолтами."""
    defaults = dict(
        rating=7.0, members=5000, year=_CURRENT_YEAR - 5,
        episodes=12, status="released",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_calculate_weight_always_positive():
    """Вес всегда > 0 даже для «мусорного» аниме."""
    worst = _anime(rating=0.1, members=1, year=1970, episodes=1, status="released")
    assert calculate_weight(worst) > 0


def test_calculate_weight_recent_anime_gets_bonus():
    """Аниме текущего года получает бонус +5 по сравнению с аниме 10-летней давности."""
    recent = _anime(year=_CURRENT_YEAR)
    old    = _anime(year=_CURRENT_YEAR - 10)
    assert calculate_weight(recent) > calculate_weight(old)


def test_calculate_weight_old_anime_gets_penalty():
    """Аниме старше 15 лет имеет меньший вес, чем аниме 5-летней давности."""
    very_old = _anime(year=_CURRENT_YEAR - 20)
    mid      = _anime(year=_CURRENT_YEAR - 5)
    assert calculate_weight(very_old) < calculate_weight(mid)


def test_calculate_weight_status_bonus():
    """ongoing получает больший бонус, чем released."""
    ongoing  = _anime(status="ongoing")
    released = _anime(status="released")
    assert calculate_weight(ongoing) > calculate_weight(released)
