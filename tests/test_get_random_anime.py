from db.models import Anime
from scripts.get_random_anime import get_random_anime


def test_get_random_anime_returns_anime_or_none():
    """Функция не должна падать и должна возвращать Anime или None."""
    anime = get_random_anime()
    assert (anime is None) or isinstance(anime, Anime)

