"""
Integration tests for run_post_cycle (services/post_cycle.py).

Mocked: get_random_anime, publish_anime.
file_lock runs for real — for the "locked" test we raise LockAlreadyHeldError directly.
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
    """A no-op lock context manager — always acquired, no filesystem interaction."""
    yield


@contextmanager
def _busy_lock(_path):
    """Simulates an already-held lock."""
    raise LockAlreadyHeldError("locked in test")
    yield  # unreachable — required by @contextmanager


# ── Тесты ─────────────────────────────────────────────────────────────────────

def test_run_post_cycle_returns_ok():
    """Happy path: anime found and published → status=ok."""
    fake = _fake_anime(anime_id=42, title="Naruto")

    with patch("services.post_cycle.file_lock", _noop_lock):
        with patch("services.post_cycle.random_picker.get_random_anime", return_value=fake):
            with patch("services.post_cycle.publish_anime"):
                result = run_post_cycle(dry_run=True)

    assert result["status"] == "ok"
    assert result["anime_id"] == 42
    assert result["title"] == "Naruto"


def test_run_post_cycle_returns_no_anime():
    """get_random_anime returned None with no error → status=no_anime."""
    with patch("services.post_cycle.file_lock", _noop_lock):
        with patch("services.post_cycle.random_picker.get_random_anime", return_value=None):
            # Ensure LAST_SELECT_ERROR is None (no selection error)
            original = picker_module.LAST_SELECT_ERROR
            picker_module.LAST_SELECT_ERROR = None
            try:
                result = run_post_cycle()
            finally:
                picker_module.LAST_SELECT_ERROR = original

    assert result["status"] == "no_anime"


def test_run_post_cycle_returns_error_on_select_failure():
    """get_random_anime returned None and LAST_SELECT_ERROR is set → status=error."""
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
    """Lock already held (concurrent cycle) → status=locked, nothing published."""
    with patch("services.post_cycle.file_lock", _busy_lock):
        with patch("services.post_cycle.publish_anime") as mock_pub:
            result = run_post_cycle()

    assert result == {"status": "locked"}
    mock_pub.assert_not_called()
