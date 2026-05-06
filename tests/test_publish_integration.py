"""
Integration tests for publish_anime (services/publisher.py).

Only send_post_with_cache is mocked (real Telegram request).
SessionLocal is replaced with the in-memory factory from conftest.
"""
import pytest
from unittest.mock import patch

from db.models import PublishedAnime
from services.publisher import publish_anime


def _count_published(session_factory) -> int:
    s = session_factory()
    count = s.query(PublishedAnime).count()
    s.close()
    return count


def test_publish_anime_dry_run_does_not_write_db(session_factory, sample_anime):
    """dry_run=True → no PublishedAnime record created, send not called."""
    with patch("services.publisher.SessionLocal", session_factory):
        with patch("services.publisher.send_post_with_cache") as mock_send:
            publish_anime(sample_anime, dry_run=True)

    mock_send.assert_not_called()
    assert _count_published(session_factory) == 0


def test_publish_anime_creates_published_record(session_factory, sample_anime):
    """dry_run=False → PublishedAnime record created with correct fields."""
    with patch("services.publisher.SessionLocal", session_factory):
        with patch("services.publisher.send_post_with_cache"):
            publish_anime(sample_anime, dry_run=False)

    s = session_factory()
    pubs = s.query(PublishedAnime).all()
    s.close()

    assert len(pubs) == 1
    assert pubs[0].anime_id == sample_anime.id
    assert pubs[0].status == sample_anime.status


def test_publish_anime_rollback_on_send_error(session_factory, sample_anime):
    """Send error → rollback → no PublishedAnime record created."""
    from telegram.error import NetworkError

    with patch("services.publisher.SessionLocal", session_factory):
        with patch(
            "services.publisher.send_post_with_cache",
            side_effect=NetworkError("timeout"),
        ):
            with pytest.raises(NetworkError):
                publish_anime(sample_anime, dry_run=False)

    assert _count_published(session_factory) == 0
