"""
Tests for the priority self-correction system (app/priority_weights.py).

All functions accept plain dicts/SimpleNamespace — no DB, no mocks needed.
"""
from types import SimpleNamespace

from app.priority_weights import (
    MIN_PENALTY,
    _year_bucket,
    get_penalty_for_anime,
    get_penalty_multipliers,
)


# ── _year_bucket ──────────────────────────────────────────────────────────────

def test_year_bucket_none_returns_old():
    assert _year_bucket(None) == "old"


def test_year_bucket_boundaries():
    assert _year_bucket(1999) == "old"
    assert _year_bucket(2000) == "2000-2009"
    assert _year_bucket(2020) == "2020+"


# ── get_penalty_multipliers ───────────────────────────────────────────────────

def test_get_penalty_multipliers_balanced_no_penalty():
    """Balanced distribution → no penalties (all multipliers == 1.0)."""
    stats = {
        "_n": 6,
        "status": {"released": 3, "ongoing": 3},
        "year_bucket": {"2020+": 3, "2010-2014": 3},
        "genre_id": {1: 3, 2: 3},
    }
    penalties = get_penalty_multipliers(stats, n=6)
    for cat in ("status", "year_bucket", "genre_id"):
        for val in penalties[cat].values():
            assert val == 1.0, f"{cat} should be 1.0 for a balanced distribution"


def test_get_penalty_multipliers_clamped_to_min():
    """Dominant category must not drop below MIN_PENALTY."""
    stats = {
        "_n": 10,
        "status": {"released": 10},   # 100% skew — maximum imbalance
        "year_bucket": {},
        "genre_id": {},
    }
    penalties = get_penalty_multipliers(stats, n=10)
    assert penalties["status"]["released"] >= MIN_PENALTY


def test_get_penalty_multipliers_no_crash_on_empty():
    """Empty statistics must not raise any exceptions."""
    stats = {"_n": 0, "status": {}, "year_bucket": {}, "genre_id": {}}
    penalties = get_penalty_multipliers(stats, n=0)
    assert "status" in penalties


# ── get_penalty_for_anime ─────────────────────────────────────────────────────

def test_get_penalty_for_anime_combines_multipliers():
    """Combined multiplier == status × year_bucket × genre product."""
    genre = SimpleNamespace(id=1)
    anime = SimpleNamespace(status="released", year=2021, genres=[genre])

    penalties = {
        "status":      {"released": 0.5},
        "year_bucket": {"2020+": 0.8},
        "genre_id":    {1: 0.6},
    }
    result = get_penalty_for_anime(anime, penalties)
    expected = 0.5 * 0.8 * 0.6
    assert abs(result - expected) < 1e-9
