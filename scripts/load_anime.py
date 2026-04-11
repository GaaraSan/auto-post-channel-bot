import logging
import time

from db.database import SessionLocal
from db.models import Anime, Genre
from parsers.shikimori_parser import get_anime_ids, parse_shikimori_anime

logger = logging.getLogger(__name__)


def load_many_anime(pages: int = 3) -> None:
    session = SessionLocal()
    genre_cache: dict[str, Genre] = {}

    try:
        logger.info("Старт загрузки аниме, страниц: %s", pages)
        for page in range(1, pages + 1):
            logger.info("Страница %s", page)
            anime_ids = get_anime_ids(page=page)

            for anime_id in anime_ids:
                try:
                    data = parse_shikimori_anime(anime_id)

                    # проверка на дубликат аниме
                    exists = session.query(Anime).filter_by(
                        shikimori_id=data["shikimori_id"]
                    ).first()
                    if exists:
                        continue

                    anime = Anime(
                        shikimori_id=data["shikimori_id"],
                        title_ru=data.get("russian"),
                        title_en=data.get("name"),
                        title_original=data.get("japanese"),
                        description=data.get("description"),
                        status=data.get("status"),
                        episodes=data.get("episodes"),
                        episodes_aired=data.get("episodes_aired"),
                        year=data.get("year"),
                        rating=data.get("rating"),
                        image_url=data.get("image"),
                    )

                    # ✅ СНАЧАЛА добавляем anime в session
                    session.add(anime)
                    session.flush()  # получаем anime.id

                    # жанры
                    for genre_name in data.get("genres", []):
                        genre = genre_cache.get(genre_name)

                        if not genre:
                            genre = session.query(Genre).filter_by(
                                name=genre_name
                            ).first()

                            if not genre:
                                genre = Genre(name=genre_name)
                                session.add(genre)
                                session.flush()

                            genre_cache[genre_name] = genre

                        anime.genres.append(genre)

                    logger.info("Загружено: %s", anime.title_ru)
                    time.sleep(0.5)

                except Exception as e:
                    logger.warning("Пропуск anime_id=%s: %s", anime_id, e)

        session.commit()
        logger.info("Загрузка завершена успешно")

    except Exception:
        session.rollback()
        logger.exception("Критическая ошибка при загрузке аниме")
        raise

    finally:
        session.close()


if __name__ == "__main__":
    from app.logging_config import setup_logging
    setup_logging()
    load_many_anime(pages=2)
