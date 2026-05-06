"""
Tests for post formatting functions (bot/formatter.py).

Pure functions with no external dependencies — no fixtures or mocks needed.
"""
from types import SimpleNamespace

from bot.formatter import clean_text, format_genres, truncate_text


# ── clean_text ────────────────────────────────────────────────────────────────

def test_clean_text_removes_bbcode_character_tag():
    raw = "[character=123]Наруто[/character] главный герой"
    assert clean_text(raw) == "Наруто главный герой"


def test_clean_text_removes_url_with_text():
    raw = "Смотреть [url=https://example.com]здесь[/url]."
    assert clean_text(raw) == "Смотреть здесь."


def test_clean_text_removes_bare_url():
    raw = "Ссылка: [url]https://example.com[/url]"
    assert clean_text(raw) == "Ссылка:"


def test_clean_text_empty_string():
    assert clean_text("") == ""


# ── truncate_text ─────────────────────────────────────────────────────────────

def test_truncate_text_short_text_unchanged():
    text = "Короткий текст"
    assert truncate_text(text, 100) == text


def test_truncate_text_respects_word_boundary():
    text = "один два три четыре пять"
    result = truncate_text(text, 12)
    # Truncates at a word boundary, appends "…", never cuts mid-word.
    assert result.endswith("…")
    assert "три" not in result  # doesn't fit within the limit
    assert result.startswith("один два")


# ── format_genres ─────────────────────────────────────────────────────────────

def test_format_genres_known_genre_returns_hashtag():
    genres = [SimpleNamespace(name="Action")]
    result = format_genres(genres)
    assert "#экшен" in result


def test_format_genres_unknown_genre_skipped():
    genres = [SimpleNamespace(name="NonExistentGenre")]
    assert format_genres(genres) == ""


def test_format_genres_empty_list():
    assert format_genres([]) == ""
