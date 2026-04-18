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
    Очищает описание от BB-тегов Shikimori/стандартного BBcode.

    Стратегия:
      1. Парные теги с содержимым → оставляем только содержимое (не жадные regex)
      2. Одиночные/незакрытые/остаточные теги → удаляем по whitelist известных имён
         (универсальный [.*?] НЕ используется — он ломает обычный текст)
      3. Нормализация пробелов и переносов строк
    """
    if not text:
        return ""

    # ── Шаг 1: парные теги ─────────────────────────────────────────────────────

    # [character=123]Имя[/character] → Имя
    text = re.sub(r"\[character=\d+\](.*?)\[/character\]", r"\1", text, flags=re.DOTALL)

    # [[Имя]] → Имя  (двойные скобки — внутренние ссылки Shikimori)
    text = re.sub(r"\[\[(.+?)\]\]", r"\1", text)

    # [url=http://...]Текст[/url] → Текст
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text, flags=re.DOTALL)

    # [url]http://...[/url] → удаляем (голая ссылка без текста не нужна)
    text = re.sub(r"\[url\].*?\[/url\]", "", text, flags=re.DOTALL)

    # [spoiler]Текст[/spoiler] и [spoiler=Заголовок]Текст[/spoiler] → Текст
    text = re.sub(r"\[spoiler(?:=[^\]]*)?\](.*?)\[/spoiler\]", r"\1", text, flags=re.DOTALL)

    # [quote]Текст[/quote] и [quote=Автор]Текст[/quote] → Текст
    text = re.sub(r"\[quote(?:=[^\]]*)?\](.*?)\[/quote\]", r"\1", text, flags=re.DOTALL)

    # [b]/[i]/[u]/[s]/[center]/[right]/[size=N] → только текст внутри
    for tag in ("b", "i", "u", "s", "center", "right", "size"):
        text = re.sub(
            rf"\[{tag}(?:=[^\]]*)?\](.*?)\[/{tag}\]",
            r"\1", text, flags=re.DOTALL,
        )

    # [anime=123]Название[/anime] → Название
    text = re.sub(r"\[anime=\d+\](.*?)\[/anime\]", r"\1", text, flags=re.DOTALL)

    # Любой вид [tag=id]...[/tag] — общий fallback для ranobe/manga/person/etc.
    text = re.sub(r"\[\w+=\d+\](.*?)\[/\w+\]", r"\1", text, flags=re.DOTALL)

    # ── Шаг 2: одиночные / остаточные теги ────────────────────────────────────
    # Работаем ТОЛЬКО по whitelist — не задеваем обычные скобки в тексте.

    _KNOWN = (
        "anime", "manga", "ranobe", "character", "person",
        "url", "spoiler", "quote",
        "b", "i", "u", "s", "center", "right", "size",
        r"img", r"list", r"\*",
    )
    text = re.sub(
        rf"\[/?(?:{'|'.join(_KNOWN)})(?:=[^\]]*)?\]",
        "", text,
    )

    # ── Шаг 3: нормализация ────────────────────────────────────────────────────

    # Не более двух переносов строк подряд
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Пробелы в начале строки (артефакты после удаления тегов)
    text = re.sub(r"^ +", "", text, flags=re.MULTILINE)

    # Двойные пробелы внутри строки (не трогаем переносы)
    text = re.sub(r"[ \t]{2,}", " ", text)

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
