"""
Tests for can_publish — cooldown and re-publication logic.
Extends the original 3 cases with 3 additional edge cases.
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


# ── Original cases ────────────────────────────────────────────────────────────

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


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_can_publish_naive_datetime():
    """SQLite often returns naive datetimes — must not raise TypeError."""
    # naive datetime with no tzinfo
    naive_dt = datetime.utcnow() - timedelta(days=200)
    anime    = DummyAnime(status="released")
    last_pub = DummyPub("released", None, naive_dt)

    # 200 days >> 90-day cooldown → True, and no TypeError
    assert can_publish(anime, last_pub) is True


def test_can_publish_ongoing_min_cooldown_not_passed():
    """
    Ongoing anime with new episodes, but ONGOING_MIN_COOLDOWN_DAYS=2
    has not yet elapsed → False.
    """
    now      = datetime.now(UTC)
    anime    = DummyAnime(status="ongoing", episodes_aired=12)
    last_pub = DummyPub("ongoing", episodes=10, published_at=now - timedelta(days=1))

    # 1 day < 2-day minimum cooldown → cannot publish
    assert can_publish(anime, last_pub) is False


def test_can_publish_unknown_status_uses_60_day_default():
    """Status not in COOLDOWN_DAYS → defaults to 60-day cooldown."""
    now = datetime.now(UTC)
    anime = DummyAnime(status="paused")  # not in COOLDOWN_DAYS

    last_pub_50 = DummyPub("paused", None, now - timedelta(days=50))
    last_pub_70 = DummyPub("paused", None, now - timedelta(days=70))

    assert can_publish(anime, last_pub_50) is False  # 50 < 60 → blocked
    assert can_publish(anime, last_pub_70) is True   # 70 > 60 → allowed
