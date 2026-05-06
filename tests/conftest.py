"""
Shared fixtures for tests.

StaticPool ensures all sessions share the same underlying connection,
so data written in db_session is visible to session_factory() calls.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import Anime, Genre


@pytest.fixture(scope="function")
def in_memory_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def session_factory(in_memory_engine):
    """Session factory bound to the in-memory database."""
    return sessionmaker(bind=in_memory_engine, autoflush=False, autocommit=False)


@pytest.fixture(scope="function")
def db_session(session_factory):
    session = session_factory()
    yield session
    session.close()


@pytest.fixture(scope="function")
def sample_anime(db_session):
    """Minimally valid anime that passes all quality filters."""
    genre = Genre(id=1, name="Action")
    anime = Anime(
        shikimori_id=1001,
        title_ru="Тест аниме",
        title_en="Test Anime",
        status="released",
        members=5000,
        rating=7.5,
        episodes=12,
        episodes_aired=12,
        year=2021,
        image_url="https://example.com/img.jpg",
        description="А" * 150,  # >= MIN_DESC_LEN (120)
        kind="tv",
        genres=[genre],
    )
    db_session.add_all([genre, anime])
    db_session.commit()
    db_session.refresh(anime)
    return anime
