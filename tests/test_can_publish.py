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


def test_can_publish_released_cooldown():
    anime = DummyAnime(status="released")
    now = datetime.now(UTC)
    last_pub_recent = DummyPub("released", None, now - timedelta(days=80))
    last_pub_old = DummyPub("released", None, now - timedelta(days=100))

    assert can_publish(anime, last_pub_recent) is False
    assert can_publish(anime, last_pub_old) is True


def test_can_publish_ongoing_new_episodes():
    now = datetime.now(UTC)
    anime = DummyAnime(status="ongoing", episodes_aired=12)
    last_pub = DummyPub("ongoing", episodes=10, published_at=now)

    assert can_publish(anime, last_pub) is True


def test_can_publish_never_published():
    anime = DummyAnime(status="released")
    assert can_publish(anime, None) is True

