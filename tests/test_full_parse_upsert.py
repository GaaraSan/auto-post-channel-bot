"""
tests/test_full_parse_upsert.py

Unit-тесты для логики upsert в scripts/full_parse.py.

Проверяют три сценария build_anime_object:
  1. Новое аниме → 'new', объект добавляется в сессию
  2. Существующее с изменёнными полями → 'updated', изменения видны в БД после commit
  3. Существующее без изменений → 'unchanged', UPDATE в БД не выдаётся

Используют SQLite in-memory через conftest.py (StaticPool).
"""
import pytest
from unittest.mock import patch

from db.models import Anime, Genre
from scripts import full_parse


# ── Фабрика сырых данных (имитация ответа parse_shikimori_anime) ─────────────

def _api_data(**overrides) -> dict:
    """Базовый словарь, имитирующий данные от Shikimori API."""
    base = {
        "shikimori_id": 42,
        "name":         "Test Anime",
        "russian":      "Тестовое аниме",
        "japanese":     "テストアニメ",
        "description":  "Описание тестового аниме для проверки.",
        "status":       "ongoing",
        "kind":         "tv",
        "episodes":     12,
        "episodes_aired": 6,
        "year":         "2024",
        "rating":       "7.50",
        "members":      10000,
        "image":        "https://shikimori.one/img/test.jpg",
        "genres":       ["Action"],
    }
    base.update(overrides)
    return base


# ── Хелпер: сброс глобального состояния full_parse перед каждым тестом ───────

@pytest.fixture(autouse=True)
def reset_full_parse_globals():
    """
    build_anime_object работает с модульными глобалами existing_map / genres_cache.
    Сбрасываем их перед каждым тестом, чтобы тесты были изолированы.
    """
    full_parse.existing_map  = {}
    full_parse.genres_cache  = {}
    yield
    full_parse.existing_map  = {}
    full_parse.genres_cache  = {}


# ── Тест 1: новое аниме ───────────────────────────────────────────────────────

def test_build_new_anime_returns_new(db_session):
    """
    Аниме, которого нет в existing_map → возвращается ('new', Anime).
    После session.add + commit запись появляется в БД.
    """
    data = _api_data()

    action, obj = full_parse.build_anime_object(data, db_session)

    assert action == "new"
    assert obj is not None
    assert obj.shikimori_id == 42
    assert obj.title_ru == "Тестовое аниме"
    assert obj.episodes_aired == 6

    db_session.add(obj)
    db_session.commit()

    saved = db_session.query(Anime).filter_by(shikimori_id=42).one()
    assert saved.title_ru == "Тестовое аниме"


def test_build_new_anime_registers_in_existing_map(db_session):
    """
    После обработки нового аниме его shikimori_id должен появиться в existing_map,
    чтобы дублирующий ID в рамках одного прогона не создал вторую запись.
    """
    data = _api_data()

    full_parse.build_anime_object(data, db_session)

    assert 42 in full_parse.existing_map


def test_build_new_anime_creates_genre(db_session):
    """
    Жанр, которого ещё нет в genres_cache, должен быть создан и добавлен в БД.
    """
    data = _api_data(genres=["Fantasy"])

    action, obj = full_parse.build_anime_object(data, db_session)
    db_session.add(obj)
    db_session.commit()

    assert "Fantasy" in full_parse.genres_cache
    saved = db_session.query(Anime).filter_by(shikimori_id=42).one()
    assert any(g.name == "Fantasy" for g in saved.genres)


# ── Тест 2: обновление существующей записи ────────────────────────────────────

# Базовые значения, совпадающие с _api_data() по умолчанию — используются
# в 'unchanged'-тестах, чтобы гарантировать нулевое расхождение.
_SEED_DEFAULTS = dict(
    shikimori_id=42,
    title_ru="Старое название",   # не volatile — не перезапишется
    title_en="Old Name",
    kind="tv",
    year=2024,
    # volatile-поля совпадают с _api_data() по умолчанию:
    status="ongoing",
    episodes=12,
    episodes_aired=6,
    rating=7.5,
    members=10000,
    image_url="https://shikimori.one/img/test.jpg",
    description="Описание тестового аниме для проверки.",
    tg_file_id="cached_file_id_abc",
)


def _seed_existing(db_session, **field_overrides) -> Anime:
    """Добавляет аниме с shikimori_id=42 в БД и регистрирует в existing_map."""
    genre = Genre(name="Action")
    fields = {**_SEED_DEFAULTS, **field_overrides}  # overrides побеждают
    anime = Anime(genres=[genre], **fields)
    db_session.add_all([genre, anime])
    db_session.commit()
    db_session.refresh(anime)
    full_parse.existing_map[42] = anime
    full_parse.genres_cache["Action"] = genre
    return anime


def test_update_episodes_aired(db_session):
    """
    Если episodes_aired изменилось → action='updated', новое значение сохраняется.
    """
    existing = _seed_existing(db_session)

    data = _api_data(episodes_aired=8)  # было 6, стало 8
    action, obj = full_parse.build_anime_object(data, db_session)

    assert action == "updated"
    assert obj is None

    db_session.commit()
    db_session.refresh(existing)
    assert existing.episodes_aired == 8


def test_update_members_and_rating(db_session):
    """
    Изменились members и rating → оба поля обновляются за один вызов.
    """
    existing = _seed_existing(db_session)

    data = _api_data(members=99000, rating="8.10")
    action, _ = full_parse.build_anime_object(data, db_session)

    assert action == "updated"

    db_session.commit()
    db_session.refresh(existing)
    assert existing.members == 99000
    assert abs(existing.rating - 8.1) < 1e-6


def test_update_status_ongoing_to_released(db_session):
    """
    Аниме перешло из ongoing в released → status обновляется.
    """
    existing = _seed_existing(db_session, status="ongoing")

    data = _api_data(status="released")
    action, _ = full_parse.build_anime_object(data, db_session)

    assert action == "updated"

    db_session.commit()
    db_session.refresh(existing)
    assert existing.status == "released"


def test_update_image_url_clears_tg_file_id(db_session):
    """
    Если image_url изменился, tg_file_id должен быть сброшен в None,
    чтобы при следующей публикации картинка загрузилась заново.
    """
    existing = _seed_existing(db_session)
    assert existing.tg_file_id == "cached_file_id_abc"

    data = _api_data(image="https://shikimori.one/img/NEW_POSTER.jpg")
    action, _ = full_parse.build_anime_object(data, db_session)

    assert action == "updated"

    db_session.commit()
    db_session.refresh(existing)
    assert existing.image_url == "https://shikimori.one/img/NEW_POSTER.jpg"
    assert existing.tg_file_id is None  # кеш инвалидирован


def test_update_same_image_url_preserves_tg_file_id(db_session):
    """
    Если image_url не изменился, tg_file_id должен остаться нетронутым.
    """
    existing = _seed_existing(db_session)

    data = _api_data()  # image тот же: "https://shikimori.one/img/test.jpg"
    full_parse.build_anime_object(data, db_session)

    db_session.commit()
    db_session.refresh(existing)
    assert existing.tg_file_id == "cached_file_id_abc"  # не тронут


def test_title_not_overwritten_on_update(db_session):
    """
    title_ru — не volatile поле, при обновлении не должно перезаписываться.
    """
    existing = _seed_existing(db_session)

    data = _api_data(members=20000)  # что-то изменилось, но не title
    full_parse.build_anime_object(data, db_session)

    db_session.commit()
    db_session.refresh(existing)
    assert existing.title_ru == "Старое название"  # не "Тестовое аниме" из API


# ── Тест 3: запись не изменилась ─────────────────────────────────────────────

def test_unchanged_returns_unchanged(db_session):
    """
    Если все volatile поля идентичны данным в API → action='unchanged'.
    Никаких UPDATE не должно выдаваться.
    """
    existing = _seed_existing(db_session)

    # Данные в _api_data() по умолчанию совпадают с тем, что записано в _seed_existing.
    data = _api_data()
    action, obj = full_parse.build_anime_object(data, db_session)

    assert action == "unchanged"
    assert obj is None


def test_unchanged_does_not_dirty_session(db_session):
    """
    При action='unchanged' SQLAlchemy не должен помечать объект как dirty,
    то есть session.dirty должен быть пустым после вызова.
    """
    _seed_existing(db_session)

    data = _api_data()
    full_parse.build_anime_object(data, db_session)

    # После вызова с идентичными данными ни один объект не должен быть изменён.
    assert len(db_session.dirty) == 0
