import re


# Maximum description length — safe upper bound for a Telegram caption.
MAX_DESCRIPTION_LENGTH = 700


STATUS_MAP = {
    "released": "Вышел",
    "ongoing": "Онгоинг",
    "anons": "Анонс",
    "paused": "Приостановлен",
}


def clean_text(text: str) -> str:
    """
    Strip Shikimori / BBCode markup from a description string.

    Strategy:
      1. Paired tags with content — keep inner text only (non-greedy regex).
      2. Remaining unpaired / residual tags — remove via a known-tag whitelist
         (a universal [.*?] pattern is intentionally avoided — it breaks
         ordinary bracketed text in descriptions).
      3. Normalize whitespace and blank lines.
    """
    if not text:
        return ""

    # ── Step 1: paired tags ───────────────────────────────────────────────────

    # [character=123]Name[/character] → Name
    text = re.sub(r"\[character=\d+\](.*?)\[/character\]", r"\1", text, flags=re.DOTALL)

    # [[Name]] → Name  (Shikimori internal wiki links)
    text = re.sub(r"\[\[(.+?)\]\]", r"\1", text)

    # [url=http://...]Label[/url] → Label
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text, flags=re.DOTALL)

    # [url]http://...[/url] → "" (bare URL with no label — not useful in a post)
    text = re.sub(r"\[url\].*?\[/url\]", "", text, flags=re.DOTALL)

    # [spoiler]Text[/spoiler] and [spoiler=Title]Text[/spoiler] → Text
    text = re.sub(r"\[spoiler(?:=[^\]]*)?](.*?)\[/spoiler\]", r"\1", text, flags=re.DOTALL)

    # [quote]Text[/quote] and [quote=Author]Text[/quote] → Text
    text = re.sub(r"\[quote(?:=[^\]]*)?](.*?)\[/quote\]", r"\1", text, flags=re.DOTALL)

    # Formatting tags: [b], [i], [u], [s], [center], [right], [size=N] → inner text
    for tag in ("b", "i", "u", "s", "center", "right", "size"):
        text = re.sub(
            rf"\[{tag}(?:=[^\]]*)?\](.*?)\[/{tag}\]",
            r"\1", text, flags=re.DOTALL,
        )

    # [anime=123]Title[/anime] → Title
    text = re.sub(r"\[anime=\d+\](.*?)\[/anime\]", r"\1", text, flags=re.DOTALL)

    # Generic fallback for [tag=id]...[/tag] (ranobe, manga, person, etc.)
    text = re.sub(r"\[\w+=\d+\](.*?)\[/\w+\]", r"\1", text, flags=re.DOTALL)

    # ── Step 2: unpaired / residual tags (whitelist only) ────────────────────

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

    # ── Step 3: normalise whitespace ─────────────────────────────────────────

    # Collapse 3+ consecutive newlines to 2.
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove leading spaces on each line (artifact from removed tags).
    text = re.sub(r"^ +", "", text, flags=re.MULTILINE)

    # Collapse multiple spaces/tabs within a line.
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text.strip()


def truncate_text(text: str, limit: int) -> str:
    """Trim text to at most `limit` characters, breaking at a word boundary."""
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
    """Build the final Telegram post text for the given anime."""

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
