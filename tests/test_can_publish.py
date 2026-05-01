"""
Тесты can_publish — логика cooldown и повторной публикации.
Расширяет существующие 3 кейса ещё тремя новыми.
"""
from datetime import UTC, datetime, timedelta

from scripts.get_random_anime import can_publish


class DummyAnime:
    def __init__(self, status: str, episodes_aired: int | None = None):
        self.status = status
        self.episodes_aired = episodes_aired


class DummyPub:
    def __init__(self, status: str, episodes: int | None, published_at: datetime):
        self.status = status
        self.episodes = episodes
        self.published_at = published_at


# ── Существующие кейсы (не трогаем) ─────────────────────────────────────────

def test_can_publish_released_cooldown():
    anime = DummyAnime(status="released")
    now = datetime.now(UTC)
    last_pub_recent = DummyPub("released", None, now - timedelta(days=80))
    last_pub_old    = DummyPub("released", None, now - timedelta(days=100))

    assert can_publish(anime, last_pub_recent) is False
    assert can_publish(anime, last_pub_old)    is True


def test_can_publish_ongoing_new_episodes():
    now = datetime.now(UTC)
    anime    = DummyAnime(status="ongoing", episodes_aired=12)
    last_pub = DummyPub("ongoing", episodes=10, published_at=now - timedelta(days=3))

    assert can_publish(anime, last_pub) is True


def test_can_publish_never_published():
    anime = DummyAnime(status="released")
    assert can_publish(anime, None) is True


# ── Новые кейсы ───────────────────────────────────────────────────────────────

def test_can_publish_naive_datetime():
    """SQLite часто возвращает naive datetime — не должен падать с TypeError."""
    # naive datetime без tzinfo (без UTC)
    naive_dt = datetime.utcnow() - timedelta(days=200)
    anime    = DummyAnime(status="released")
    last_pub = DummyPub("released", None, naive_dt)

    # 200 дней >> 90-дневный cooldown → True, и без TypeError
    assert can_publish(anime, last_pub) is True


def test_can_publish_ongoing_min_cooldown_not_passed():
    """
    ongoing + новые серии, но минимальный cooldown (ONGOING_MIN_COOLDOWN_DAYS=2)
    ещё не прошёл → False.
    """
    now      = datetime.now(UTC)
    anime    = DummyAnime(status="ongoing", episodes_aired=12)
    last_pub = DummyPub("ongoing", episodes=10, published_at=now - timedelta(days=1))

    # 1 день < 2 дня минимального cooldown → нельзя публиковать
    assert can_publish(anime, last_pub) is False


def test_can_publish_unknown_status_uses_60_day_default():
    """Статус не из COOLDOWN_DAYS → дефолтный cooldown 60 дней."""
    now = datetime.now(UTC)
    anime = DummyAnime(status="paused")  # нет в COOLDOWN_DAYS

    last_pub_50 = DummyPub("paused", None, now - timedelta(days=50))
    last_pub_70 = DummyPub("paused", None, now - timedelta(days=70))

    assert can_publish(anime, last_pub_50) is False  # 50 < 60 → нельзя
    assert can_publish(anime, last_pub_70) is True   # 70 > 60 → можно
