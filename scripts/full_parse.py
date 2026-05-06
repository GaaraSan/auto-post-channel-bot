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
genres_cache = {}    # genre name → Genre ORM object
existing_ids = set() # shikimori_ids already in the DB


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
    global existing_ids, genres_cache

    print("[*] Pre-loading data from DB…")

    # Load all existing shikimori_ids for O(1) duplicate checks.
    existing_ids = set(
        x[0] for x in session.query(Anime.shikimori_id).all()
    )

    genres = session.query(Genre).all()
    genres_cache = {g.name: g for g in genres}

    print(f"[+] Loaded {len(existing_ids)} anime and {len(genres_cache)} genres")


# ── Core ──────────────────────────────────────────────────────────────────────

def build_anime_object(data, session):
    anime_id = data['shikimori_id']

    if anime_id in existing_ids:
        return None  # already in DB — skip

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

    existing_ids.add(anime_id)
    return new_anime


def main():
    session = SessionLocal()
    page = load_last_page()

    print(f"[*] Starting from page: {page}")

    preload_data(session)

    try:
        while True:
            print(f"\n--- Page {page} ---")

            ids = get_anime_ids(page=page, limit=PER_PAGE)
            if not ids:
                print("[*] Parsing complete — no new anime.")
                break

            batch = []

            for a_id in ids:
                try:
                    data = parse_shikimori_anime(a_id)
                    anime_obj = build_anime_object(data, session)

                    if anime_obj:
                        batch.append(anime_obj)

                    time.sleep(0.3)

                except Exception as e:
                    print(f"[!] Error processing ID {a_id}: {e}")
                    continue

            if batch:
                session.add_all(batch)
                session.commit()

            print(f"[+] Page {page} done. Added: {len(batch)}")

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
        print("[*] Done")


if __name__ == "__main__":
    main()