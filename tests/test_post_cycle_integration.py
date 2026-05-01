"""
Интеграционные тесты run_post_cycle (services/post_cycle.py).

Мокируем: get_random_anime, publish_anime.
file_lock работает реально — для теста «locked» создаём lock-файл вручную.
"""
import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import scripts.get_random_anime as picker_module
from app.lockfile import LockAlreadyHeldError
from services.post_cycle import run_post_cycle


def _fake_anime(anime_id: int = 1, title: str = "Тест") -> SimpleNamespace:
    return SimpleNamespace(id=anime_id, title_ru=title, title_en=None)


# ── Helpers ───────────────────────────────────────────────────────────────────

@contextmanager
def _noop_lock(_path):
    """Фиктивный lock, который всегда захватывается без файловой системы."""
    yield


@contextmanager
def _busy_lock(_path):
    """Симулирует уже захваченный lock."""
    raise LockAlreadyHeldError("locked in test")
    yield  # для @contextmanager — недостижимо


# ── Тесты ─────────────────────────────────────────────────────────────────────

def test_run_post_cycle_returns_ok():
    """Счастливый путь: аниме найдено и опубликовано → status=ok."""
    fake = _fake_anime(anime_id=42, title="Naruto")

    with patch("services.post_cycle.file_lock", _noop_lock):
        with patch("services.post_cycle.random_picker.get_random_anime", return_value=fake):
            with patch("services.post_cycle.publish_anime"):
                result = run_post_cycle(dry_run=True)

    assert result["status"] == "ok"
    assert result["anime_id"] == 42
    assert result["title"] == "Naruto"


def test_run_post_cycle_returns_no_anime():
    """get_random_anime вернул None без ошибки → status=no_anime."""
    with patch("services.post_cycle.file_lock", _noop_lock):
        with patch("services.post_cycle.random_picker.get_random_anime", return_value=None):
            # Убеждаемся: LAST_SELECT_ERROR = None (нет ошибки выборки)
            original = picker_module.LAST_SELECT_ERROR
            picker_module.LAST_SELECT_ERROR = None
            try:
                result = run_post_cycle()
            finally:
                picker_module.LAST_SELECT_ERROR = original

    assert result["status"] == "no_anime"


def test_run_post_cycle_returns_error_on_select_failure():
    """get_random_anime вернул None + LAST_SELECT_ERROR установлен → status=error."""
    original = picker_module.LAST_SELECT_ERROR
    picker_module.LAST_SELECT_ERROR = RuntimeError("db fail")
    try:
        with patch("services.post_cycle.file_lock", _noop_lock):
            with patch("services.post_cycle.random_picker.get_random_anime", return_value=None):
                result = run_post_cycle()
    finally:
        picker_module.LAST_SELECT_ERROR = original

    assert result["status"] == "error"
    assert result["reason"] == "select_failed"


def test_run_post_cycle_returns_locked():
    """Lock уже захвачен (конкурентный цикл) → status=locked, публикации нет."""
    with patch("services.post_cycle.file_lock", _busy_lock):
        with patch("services.post_cycle.publish_anime") as mock_pub:
            result = run_post_cycle()

    assert result == {"status": "locked"}
    mock_pub.assert_not_called()
