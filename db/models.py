from sqlalchemy import Column, Integer, DateTime, String, Text, Float, ForeignKey, Table
from sqlalchemy.orm import relationship
from datetime import UTC, datetime

from db.database import Base

# Промежуточная таблица many-to-many
anime_genres = Table(
    "anime_genres",
    Base.metadata,
    Column("anime_id", ForeignKey("anime.id"), primary_key=True),
    Column("genre_id", ForeignKey("genres.id"), primary_key=True),
)


class Anime(Base):
    __tablename__ = "anime"

    id = Column(Integer, primary_key=True)
    shikimori_id = Column(Integer, unique=True, nullable=False)

    title_ru = Column(String, nullable=False)
    title_en = Column(String)
    title_original = Column(String)

    description = Column(Text)
    status = Column(String)

    episodes = Column(Integer)
    episodes_aired = Column(Integer)

    year = Column(Integer)
    rating = Column(Float)

    image_url = Column(String)

    # связь с жанрами
    genres = relationship(
        "Genre",
        secondary=anime_genres,
        back_populates="anime"
    )


class Genre(Base):
    __tablename__ = "genres"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    anime = relationship(
        "Anime",
        secondary=anime_genres,
        back_populates="genres"
    )

class PublishedAnime(Base):
    __tablename__ = "published_anime"

    id = Column(Integer, primary_key=True)

    anime_id = Column(
        Integer,
        ForeignKey("anime.id"),
        nullable=False
    )

    status = Column(String, nullable=False)
    episodes = Column(Integer)

    published_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        nullable=False
    )

    anime = relationship("Anime")

