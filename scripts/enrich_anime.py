"""
scripts/enrich_anime.py

Back-fills the `kind` and `members` fields on existing anime records
by fetching data from the Shikimori API.

Usage:
    python scripts/enrich_anime.py
    python scripts/enrich_anime.py --batch-size 100 --delay 0.4
    python scripts/enrich_anime.py --reset-checkpoint   # restart from scratch
"""

import argparse
import logging
import os
import time

import requests


from app.logging_config import setup_logging
from db.database import SessionLocal
from db.models import Anime

setup_logging()
logger = logging.getLogger(__name__)

SHIKIMORI_API = "https://shikimori.one/api/animes/{}"
HEADERS = {"User-Agent": "anime-db/1.0", "Accept": "application/json"}

# Checkpoint file lives next to this script for easy access.
CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), "enrich_checkpoint.txt")


# ---------------------------------------------------------------------------
# Checkpoint helpers — persist progress so the script can be safely resumed
# ---------------------------------------------------------------------------

def load_checkpoint() -> int:
    """Read last_id from the checkpoint file. Returns 0 if file does not exist."""
    if not os.path.exists(CHECKPOINT_PATH):
        return 0
    try:
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            line = f.read().strip()          # format: "last_id=12345"
        value = int(line.split("=")[1])
        logger.info("Resuming from checkpoint: last_id=%d (%s)", value, CHECKPOINT_PATH)
        return value
    except Exception as e:
        logger.warning("Could not read checkpoint (%s): %s — starting from 0", CHECKPOINT_PATH, e)
        return 0


def save_checkpoint(last_id: int) -> None:
    """Persist last_id to the checkpoint file."""
    try:
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            f.write(f"last_id={last_id}\n")
    except Exception as e:
        logger.warning("Could not save checkpoint: %s", e)


def delete_checkpoint() -> None:
    """Remove the checkpoint file after a successful run."""
    try:
        if os.path.exists(CHECKPOINT_PATH):
            os.remove(CHECKPOINT_PATH)
            logger.info("Checkpoint deleted (%s)", CHECKPOINT_PATH)
    except Exception as e:
        logger.warning("Could not delete checkpoint: %s", e)


# ---------------------------------------------------------------------------
# Shikimori API fetch (with retry)
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_DELAY = 3.0   # seconds to wait after a 429 or network error

def fetch_kind_and_members(shikimori_id: int) -> tuple[str | None, int | None]:
    """
    Fetch (kind, members) for a given shikimori_id from the Shikimori API.

    Retry policy (up to MAX_RETRIES attempts):
      - HTTP 429 → sleep RETRY_DELAY × attempt, then retry
      - Timeout / connection error → sleep RETRY_DELAY, then retry
      - Other HTTP errors → return (None, None) immediately (retrying won't help)

    members is computed as the sum of all rates_statuses_stats values.
    Returns (None, None) if all attempts fail.
    """
    url = SHIKIMORI_API.format(shikimori_id)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)

            if resp.status_code == 429:
                wait = RETRY_DELAY * attempt
                logger.warning(
                    "Rate limited (429) for shikimori_id=%s, attempt=%d, sleeping=%.1fs",
                    shikimori_id, attempt, wait,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()  # raises for 4xx/5xx other than 429

            data = resp.json()

            kind = data.get("kind")

            # Shikimori does not expose a direct "members" field;
            # we derive it from the sum of all rates_statuses_stats entries.
            stats = data.get("rates_statuses_stats") or []
            members = sum(entry.get("value", 0) for entry in stats)

            logger.debug(
                "shikimori_id=%s | kind=%s | members=%s | stats_len=%d",
                shikimori_id, kind, members, len(stats),
            )

            return kind, members

        except requests.Timeout:
            logger.warning(
                "Timeout for shikimori_id=%s, attempt=%d/%d",
                shikimori_id, attempt, MAX_RETRIES,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except requests.ConnectionError:
            logger.warning(
                "Connection error for shikimori_id=%s, attempt=%d/%d",
                shikimori_id, attempt, MAX_RETRIES,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except requests.HTTPError as e:
            logger.warning("HTTP error for shikimori_id=%s: %s", shikimori_id, e)
            break  # non-429 4xx errors won't succeed on retry
        except Exception as e:
            logger.warning("Unexpected error for shikimori_id=%s: %s", shikimori_id, e)
            break

    return None, None


# ---------------------------------------------------------------------------
# Main enrichment loop
# ---------------------------------------------------------------------------

def enrich(batch_size: int = 50, delay: float = 0.35) -> None:
    """
    Iterate over anime where members or kind is NULL and back-fill from the API.

    Uses cursor-based pagination (filter by id > last_id) for O(log n) stability
    — OFFSET-based pagination degrades as the table grows and can skip rows.

    Run the migration before the first execution on an existing database:
        python -m scripts.migrate_add_kind_members
    """
    session = SessionLocal()
    try:
        total = (
            session.query(Anime)
            .filter(Anime.members.is_(None) | Anime.kind.is_(None))
            .count()
        )
        logger.info("Records to enrich: %d", total)

        if total == 0:
            logger.info("All records already enriched — nothing to do.")
            delete_checkpoint()
            return

        last_id = load_checkpoint()

        processed = 0
        updated = 0
        skipped = 0
        batch_num = 0

        while True:
            batch = (
                session.query(Anime)
                .filter(
                    Anime.id > last_id,
                    Anime.members.is_(None) | Anime.kind.is_(None),
                )
                .order_by(Anime.id)
                .limit(batch_size)
                .all()
            )

            if not batch:
                break

            for anime in batch:
                kind, members = fetch_kind_and_members(anime.shikimori_id)

                if kind is not None or members is not None:
                    if kind is not None:
                        anime.kind = kind
                    # Skip members=0: empty stats mean "no data yet", not "zero users".
                    # Writing 0 would permanently block this anime from the quality filter.
                    if members is not None and members > 0:
                        anime.members = members
                    updated += 1
                else:
                    skipped += 1

                processed += 1
                time.sleep(delay)

            session.commit()
            batch_num += 1
            last_id = batch[-1].id  # advance cursor to the last processed id

            save_checkpoint(last_id)

            remaining = max(0, total - processed)
            logger.info(
                "Batch #%d | processed: %d / %d | remaining: ~%d"
                " | updated: %d | skipped: %d | last_id: %d",
                batch_num, processed, total, remaining,
                updated, skipped, last_id,
            )

        logger.info(
            "Enrichment complete. Total: %d | updated: %d | skipped: %d",
            processed, updated, skipped,
        )
        delete_checkpoint()

    except KeyboardInterrupt:
        # The last batch was already committed; checkpoint is up to date.
        session.rollback()
        logger.warning(
            "Interrupted by user (Ctrl+C). "
            "Checkpoint saved: last_id=%d. "
            "Re-run the script to continue.",
            last_id if 'last_id' in locals() else 0,
        )
    except Exception:
        session.rollback()
        logger.exception(
            "Fatal error during enrichment — session rolled back. "
            "Checkpoint saved (last_id=%d); re-run to continue.",
            last_id if 'last_id' in locals() else 0,
        )
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Back-fill anime: kind + members")
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Records per batch (default: 50)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.35,
        help="Delay between API requests in seconds (default: 0.35)",
    )
    parser.add_argument(
        "--reset-checkpoint", action="store_true",
        help="Delete the checkpoint and restart from id=0",
    )
    args = parser.parse_args()

    if args.reset_checkpoint:
        delete_checkpoint()
        logger.info("Checkpoint reset — starting from id=0")

    try:
        enrich(batch_size=args.batch_size, delay=args.delay)
    except KeyboardInterrupt:
        pass  # already handled inside enrich() with a log message
