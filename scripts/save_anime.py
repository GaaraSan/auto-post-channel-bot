from db.database import SessionLocal
from db.models import Anime, Genre
from parsers.shikimori_parser import parse_shikimori_anime


def save_single_anime(shikimori_id: int):
    session = SessionLocal()

    try:
        exists = session.query(Anime).filter_by(
            shikimori_id=shikimori_id
        ).first()

        if exists:
            print("Аниме уже есть в базе")
            return

        data = parse_shikimori_anime(shikimori_id)

        anime = Anime(
            shikimori_id=data["shikimori_id"],
            title_ru=data["russian"],
            title_en=data["name"],
            title_original=None,
            description=data["description"],
            status=data["status"],
            episodes=data["episodes"],
            episodes_aired=data["episodes_aired"],
            year=data["year"],
            rating=data["rating"],
            image_url=data["image"],
        )

        # 🔴 ВАЖНО: сначала добавляем anime в сессию
        session.add(anime)
        session.flush()  # получаем anime.id

        # Жанры
        for genre_name in data["genres"]:
            genre = session.query(Genre).filter_by(name=genre_name).first()

            if not genre:
                genre = Genre(name=genre_name)
                session.add(genre)
                session.flush()

            anime.genres.append(genre)

        session.commit()
        print(f"Сохранено: {anime.title_ru}")

    except Exception as e:
        session.rollback()
        print("Ошибка:", e)

    finally:
        session.close()


if __name__ == "__main__":
    save_single_anime(5114)
