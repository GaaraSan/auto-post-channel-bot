import math
import random
import logging
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from db.database import SessionLocal
from db.models import Anime, PublishedAnime
from app.priority_weights import (
    WINDOW_SIZE,
    get_publication_stats,
    get_penalty_multipliers,
    get_penalty_for_anime,
)

logger = logging.getLogger(__name__)

# Last exception raised inside get_random_anime(), exposed for post_cycle error reporting.
LAST_SELECT_ERROR: Exception | None = None

# ---------------------------------------------------------------------------
# Cooldown configuration
# ---------------------------------------------------------------------------

COOLDOWN_DAYS = {
    "released": 90,
    "ongoing": 14,
    "anons": 30,
}

# Minimum cooldown for ongoing anime even when new episodes are available.
ONGOING_MIN_COOLDOWN_DAYS = 2

# ---------------------------------------------------------------------------
# Quality filter thresholds
# ---------------------------------------------------------------------------

EXCLUDED_KINDS = {"music", "cm", "pv"}  # ads and clips — not suitable for the channel
MIN_MEMBERS    = 500                     # ignore obscure titles with tiny audiences
MIN_RATING     = 3.0                     # filter out obviously bad entries
MIN_DESC_LEN   = 120                     # skip entries with too little description text

# Primary pool: top N by popularity — consistently good quality.
POOL_TOP    = 300
# Secondary pool: random N — gives newer / less popular titles a chance.
POOL_RANDOM = 200
# Final candidate list size before random.choice.
TOP_N       = 50

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def calculate_weight(anime) -> float:
    """
    Compute a composite weight for random-weighted selection.

    Components:
      - Base: rating × 2 + log10(members) × 2
        (coefficient was reduced from 3.0 to prevent old high-member titles
         from dominating the pool)
      - Year bonus: stepped +5/+3/+1.5/0/-1 favouring recent releases
      - Episode bonus: +1.5 for series with ≥ 12 episodes
      - Episode penalty: -0.5 for single-episode entries (but not blocked)
      - Status bonus: +2 for ongoing, +1 for announced

    Always returns a positive value (minimum 0.1).
    """
    current_year = datetime.now(UTC).year

    rating_part  = (anime.rating or 5.5) * 2.0
    members_part = math.log10((anime.members or 1) + 1) * 2.0
    score = rating_part + members_part

    # Stepped year bonus — newer titles get a higher priority boost.
    if anime.year:
        if anime.year >= current_year - 1:    # released in the last 1–2 years
            score += 5.0
        elif anime.year >= current_year - 3:  # last 3 years
            score += 3.0
        elif anime.year >= current_year - 7:  # last 7 years
            score += 1.5
        elif anime.year >= current_year - 15: # last 15 years
            score += 0.0
        else:                                 # older than 15 years
            score -= 1.0

    if anime.episodes and anime.episodes >= 12:
        score += 1.5

    # Small penalty for single-episode entries — not a hard exclusion.
    if anime.episodes == 1:
        score -= 0.5

    if anime.status == "ongoing":
        score += 2.0
    elif anime.status == "anons":
        score += 1.0

    return max(score, 0.1)


def get_random_anime():
    session = SessionLocal()

    try:
        global LAST_SELECT_ERROR
        LAST_SELECT_ERROR = None

        # Step 1: gather recent publication stats for the self-correction system.
        stats    = get_publication_stats(session, WINDOW_SIZE)
        penalties = get_penalty_multipliers(stats)

        # Step 2: build two candidate pools with a single SQL round-trip each.
        # Quality filters are computed once outside the retry loop.
        quality_filters = (
            Anime.members.isnot(None),
            Anime.members >= MIN_MEMBERS,
            Anime.image_url.isnot(None),
            Anime.description.isnot(None),
            Anime.genres.any(),          # at least one genre (no JOIN needed)
            # Allow NULL rating (new titles), only reject obvious junk.
            ~(
                Anime.rating.isnot(None)
                & (Anime.rating < MIN_RATING)
            ),
            ~Anime.kind.in_(list(EXCLUDED_KINDS)),
        )

        def _base_query():
            return (
                session.query(Anime)
                .options(joinedload(Anime.genres))
                .filter(*quality_filters)
            )

        # If no eligible anime are found after scoring, double both pool sizes and retry once.
        eligible = []
        for multiplier in (1, 2):
            # Pool A: top by popularity — reliable quality baseline.
            top_candidates = (
                _base_query()
                .order_by(Anime.members.desc(), Anime.rating.desc())
                .limit(POOL_TOP * multiplier)
                .all()
            )

            # Pool B: random sample — chance for newer / less popular titles.
            random_candidates = (
                _base_query()
                .order_by(func.random())
                .limit(POOL_RANDOM * multiplier)
                .all()
            )

            # Merge pools, de-duplicate by id (dict preserves insertion order).
            seen: dict = {}
            for a in top_candidates + random_candidates:
                if a.id not in seen:
                    seen[a.id] = a
            candidates = list(seen.values())

            if not candidates:
                logger.debug("No candidates after SQL filtering (multiplier=%d)", multiplier)
                break

            # Step 3: apply cooldown and description-length filters in Python.
            # All data is already in memory — no additional queries (no N+1).
            candidate_ids = [a.id for a in candidates]
            last_pubs = (
                session.query(PublishedAnime)
                .filter(PublishedAnime.anime_id.in_(candidate_ids))
                .order_by(PublishedAnime.published_at.desc())
                .all()
            )
            # Keep only the most recent publication record per anime.
            last_pub_map: dict = {}
            for pub in last_pubs:
                if pub.anime_id not in last_pub_map:
                    last_pub_map[pub.anime_id] = pub

            eligible = [
                a for a in candidates
                if (
                    len(a.description or "") >= MIN_DESC_LEN
                    and can_publish(a, last_pub_map.get(a.id))
                )
            ]

            if eligible:
                break
            logger.warning(
                "No anime passed cooldown/description filter (multiplier=%d) — expanding pool…",
                multiplier,
            )

        if not eligible:
            logger.warning("No anime available even with expanded pool")
            return None

        # Step 4: rank by effective weight (base score × penalty multiplier) and pick randomly from top N.
        def effective_weight(a):
            base    = calculate_weight(a)
            penalty = get_penalty_for_anime(a, penalties)
            return base * penalty

        eligible.sort(key=effective_weight, reverse=True)
        top = eligible[:TOP_N]

        # Random choice from the top N avoids always picking the highest-scored title.
        chosen = random.choice(top)
        logger.debug(
            "Selected: %s (year=%s, members=%s, weight=%.2f)",
            chosen.title_ru, chosen.year, chosen.members, effective_weight(chosen)
        )
        return chosen

    except Exception:
        LAST_SELECT_ERROR = sys.exc_info()[1]  # type: ignore[assignment]
        logger.exception("Error selecting anime")
        return None

    finally:
        session.close()


def can_publish(anime, last_pub) -> bool:
    """
    Determine whether an anime is eligible for (re-)publication.

    Rules:
      - Never published → always eligible.
      - Ongoing with new episodes aired since last post → eligible after
        ONGOING_MIN_COOLDOWN_DAYS (prevents instant re-posts when a new
        episode drops).
      - All other cases → eligible after the status-specific cooldown
        (COOLDOWN_DAYS); unknown statuses default to 60 days.

    Handles naive datetimes from SQLite by treating them as UTC.
    """
    if not last_pub:
        return True

    now = datetime.now(UTC)

    # SQLite often stores naive datetimes — treat them as UTC to avoid TypeError.
    last_dt = last_pub.published_at
    if getattr(last_dt, "tzinfo", None) is None:
        last_dt = last_dt.replace(tzinfo=UTC)

    # Ongoing with new episodes: apply minimum cooldown instead of the full 14-day one.
    if anime.status == "ongoing":
        if (
            anime.episodes_aired
            and last_pub.episodes
            and anime.episodes_aired > last_pub.episodes
        ):
            return now >= last_dt + timedelta(days=ONGOING_MIN_COOLDOWN_DAYS)

    cooldown_days = COOLDOWN_DAYS.get(anime.status, 60)
    return now >= last_dt + timedelta(days=cooldown_days)


if __name__ == "__main__":
    from app.logging_config import setup_logging
    setup_logging()
    anime = get_random_anime()
    if anime:
        logger.info(
            "Found anime for posting: %s (year=%s, status=%s)",
            anime.title_ru, anime.year, anime.status
        )
    else:
        logger.warning("No suitable anime found for posting")
