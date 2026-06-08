import sys
import time
from pathlib import Path

from db.database import SessionLocal
from db.models import Anime, Genre

try:
    from parsers.shikimori_parser import get_anime_ids, parse_shikimori_anime
except ImportError as e:
    print(f"[!] Parser import error: {e}")
    sys.exit(1)

STATE_FILE = Path("parse_state.txt")
PER_PAGE = 50

# In-memory caches to avoid redundant DB queries during a parse run.
genres_cache = {}  # genre name → Genre ORM object
existing_map  = {}  # shikimori_id → Anime ORM object (O(1) lookup + in-place updates)

# Fields that can legitimately change on Shikimori over time.
# Titles, kind, year, and genres are treated as immutable after initial import.
# tg_file_id is our internal cache — never overwritten by the parser.
# image_url is handled separately (changing it also clears tg_file_id).
VOLATILE_FIELDS = ("episodes_aired", "episodes", "rating", "members", "status", "description")


# ── Helpers ──────────────────────────────────────────────────────────────────

def safe_int(val):
    return int(val) if val and str(val).isdigit() else None

def safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def load_last_page():
    if STATE_FILE.is_file():
        try:
            return int(STATE_FILE.read_text().strip())
        except Exception:
            pass
    return 1

def save_last_page(page):
    STATE_FILE.write_text(str(page))


# ── Cache pre-load ────────────────────────────────────────────────────────────

def preload_data(session):
    global existing_map, genres_cache

    print("[*] Pre-loading data from DB…")

    # Load ALL Anime ORM objects into the session in one query.
    # This gives us O(1) lookup by shikimori_id and lets SQLAlchemy track
    # any field mutations automatically — no extra per-record queries needed.
    all_anime = session.query(Anime).all()
    existing_map = {a.shikimori_id: a for a in all_anime}

    genres = session.query(Genre).all()
    genres_cache = {g.name: g for g in genres}

    print(f"[+] Loaded {len(existing_map)} anime and {len(genres_cache)} genres")


# ── Core ──────────────────────────────────────────────────────────────────────

def _extract_volatile(data: dict) -> dict:
    """Map raw API data to the subset of fields we sync on every parse run."""
    return {
        "episodes_aired": data.get("episodes_aired"),
        "episodes":       data.get("episodes"),
        "rating":         safe_float(data.get("rating")),
        "members":        data.get("members"),
        "status":         data.get("status"),
        "description":    data.get("description"),
    }


def build_anime_object(data, session) -> tuple[str, Anime | None]:
    """
    Insert or update a single anime record.

    Returns one of:
        ('new',       Anime)  — brand-new record; caller must session.add() it
        ('updated',   None)   — existing record mutated in-place (auto-tracked by session)
        ('unchanged', None)   — existing record; nothing differed, nothing written
    """
    anime_id = data['shikimori_id']

    # ── UPDATE path ──────────────────────────────────────────────────────────
    if anime_id in existing_map:
        existing  = existing_map[anime_id]
        new_values = _extract_volatile(data)

        changed = False
        for field, new_val in new_values.items():
            if getattr(existing, field) != new_val:
                setattr(existing, field, new_val)
                changed = True

        # image_url is checked separately: if the poster URL changed the cached
        # tg_file_id is stale (it points to the old image) and must be cleared.
        new_image = data.get("image")
        if existing.image_url != new_image:
            existing.image_url = new_image
            existing.tg_file_id = None  # invalidate stale Telegram cache
            changed = True

        return ("updated", None) if changed else ("unchanged", None)

    # ── INSERT path ──────────────────────────────────────────────────────────
    title_ru = data.get('russian') or data.get('name') or "Без названия"

    new_anime = Anime(
        shikimori_id=anime_id,
        title_ru=title_ru,
        title_en=data.get('name'),
        title_original=data.get('japanese'),
        description=data.get('description'),
        status=data.get('status'),
        kind=data.get('kind'),
        episodes=data.get('episodes'),
        episodes_aired=data.get('episodes_aired'),
        year=safe_int(data.get('year')),
        rating=safe_float(data.get('rating')),
        members=data.get('members'),
        image_url=data.get('image')
    )

    for g_name in data.get('genres', []):
        if not g_name:
            continue

        if g_name not in genres_cache:
            # New genre — persist it and add to the local cache.
            genre_obj = Genre(name=g_name)
            session.add(genre_obj)
            genres_cache[g_name] = genre_obj
        else:
            genre_obj = genres_cache[g_name]

        new_anime.genres.append(genre_obj)

    # Register in map so duplicate shikimori_ids within the same run are caught.
    existing_map[anime_id] = new_anime
    return ("new", new_anime)


def main():
    session = SessionLocal()
    page = load_last_page()

    print(f"[*] Starting from page: {page}")

    preload_data(session)

    total_added   = 0
    total_updated = 0

    try:
        while True:
            print(f"\n--- Page {page} ---")

            ids = get_anime_ids(page=page, limit=PER_PAGE)
            if not ids:
                print("[*] Parsing complete — no new anime.")
                break

            batch_new    = []
            page_added   = 0
            page_updated = 0

            for a_id in ids:
                try:
                    data   = parse_shikimori_anime(a_id)
                    action, anime_obj = build_anime_object(data, session)

                    if action == "new":
                        batch_new.append(anime_obj)
                        page_added += 1
                    elif action == "updated":
                        page_updated += 1
                    # "unchanged" → nothing to do

                    time.sleep(0.3)

                except Exception as e:
                    print(f"[!] Error processing ID {a_id}: {e}")
                    continue

            if batch_new:
                session.add_all(batch_new)

            # Single commit per page:
            #   • INSERTs for batch_new
            #   • UPDATEs for dirty existing objects (auto-tracked by SQLAlchemy)
            session.commit()

            total_added   += page_added
            total_updated += page_updated

            print(
                f"[+] Page {page} done. "
                f"Added: {page_added}, Updated: {page_updated}"
            )

            save_last_page(page)
            page += 1

            time.sleep(1.5)

    except KeyboardInterrupt:
        print("\n[!] Interrupted by user — committing current state…")
        session.commit()

    except Exception as e:
        print(f"\n[!] Fatal error: {e}")
        session.rollback()

    finally:
        session.close()
        print(f"[*] Done. Total added: {total_added}, total updated: {total_updated}")


if __name__ == "__main__":
    main()