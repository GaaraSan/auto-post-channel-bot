from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from db.database import SessionLocal
from db.models import Anime
from bot.formatter import format_anime_post
from bot.telegram_sender import send_post


def get_random_anime(session: Session) -> Anime:
    return (
        session.query(Anime)
        .options(joinedload(Anime.genres))
        .order_by(func.random())
        .first()
    )


def main():
    session = SessionLocal()

    try:
        anime = get_random_anime(session)
        post_text = format_anime_post(anime)

        send_post(
            text=post_text,
            image_url=anime.image_url
        )

        print("✔ Пост успешно отправлен в канал")

    finally:
        session.close()


if __name__ == "__main__":
    main()
