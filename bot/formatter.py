import re


# Максимальная длина описания (безопасно для caption)
MAX_DESCRIPTION_LENGTH = 700


STATUS_MAP = {
    "released": "Вышел",
    "ongoing": "Онгоинг",
    "anons": "Анонс",
    "paused": "Приостановлен",
}


def clean_text(text: str) -> str:
    """
    Очищает текст описания:
    - убирает только BB-теги Shikimori [anime=123] и [/anime]
    - не трогает текст в скобках (имена персонажей и т.п.)
    - убирает лишние переносы строк и двойные пробелы
    """
    if not text:
        return ""

    # Убираем только [anime=123] и [/anime] — не удаляем любые [...], чтобы не терять имена персонажей
    text = re.sub(r"\[/?anime.*?\]", "", text)

    # Заменяем множественные переводы строк
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Убираем двойные пробелы
    text = re.sub(r"\s{2,}", " ", text)

    return text.strip()


def truncate_text(text: str, limit: int) -> str:
    """
    Обрезает текст по границе слова и добавляет многоточие
    """
    if len(text) <= limit:
        return text

    truncated = text[:limit]
    truncated = truncated.rsplit(" ", 1)[0]
    return truncated + "…"


def normalize_genre(name: str) -> str:
    return (
        name.lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )


GENRE_TAGS = {
    "action": "экшен",
    "adventure": "приключения",
    "comedy": "комедия",
    "drama": "драма",
    "fantasy": "фэнтези",
    "scifi": "фантастика",
    "sliceoflife": "повседневность",
    "supernatural": "сверхъестественное",
    "psychological": "психологическое",
    "thriller": "триллер",
    "horror": "ужасы",
    "sports": "спорт",
    "music": "музыка",
    "mecha": "меха",
    "military": "военное",
    "historical": "исторический",
    "school": "школа",
    "kids": "детское",
    "shounen": "сёнэн",
    "shoujo": "сёдзё",
    "seinen": "сэйнэн",
    "josei": "дзёсэй",
    "ecchi": "этти",
    "harem": "гарем",
    "demons": "демоны",
    "vampire": "вампиры",
    "magic": "магия",
    "samurai": "самураи",
    "martialarts": "боевыеИскусства",
    "police": "полиция",
    "space": "космос",
    "game": "игры",
    "games": "игры",
    "cars": "машины",
    "parody": "пародия",
    "dementia": "безумие",
    "superpower": "суперСила",
    "romance": "романтика",
}


def format_genres(genres) -> str:
    tags = []

    for genre in genres:
        if genre.name:
            key = normalize_genre(genre.name)
            tag = GENRE_TAGS.get(key)
            if tag:
                tags.append(f"#{tag}")

    tags = sorted(set(tags))

    if not tags:
        return ""

    return "🎭 Жанры:\n" + " ".join(tags)


def format_anime_post(anime) -> str:
    """
    Формирует финальный текст поста для Telegram
    """

    title = anime.title_ru or anime.title_en or "Без названия"
    year = anime.year or "—"
    episodes = anime.episodes or "?"
    rating = anime.rating or "—"

    status = STATUS_MAP.get(anime.status, anime.status or "—")

    description = clean_text(anime.description)
    description = truncate_text(description, MAX_DESCRIPTION_LENGTH)
    if not description:
        description = "Описание отсутствует."

    genres_block = format_genres(anime.genres)

    post = (
        f"🎬 {title}\n\n"
        f"📅 {year}\n"
        f"📺 Серий: {episodes}\n"
        f"📡 Статус: {status}\n"
        f"⭐️ Рейтинг: {rating}\n\n"
    )


    if genres_block:
        post += genres_block + "\n\n"

    if description:
        post += f"📖 Описание:\n{description}"

    return post.strip()
