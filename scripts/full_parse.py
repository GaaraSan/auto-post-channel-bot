import sys
import time
from pathlib import Path

from db.database import SessionLocal
from db.models import Anime, Genre

try:
    from parsers.shikimori_parser import get_anime_ids, parse_shikimori_anime
except ImportError as e:
    print(f"[!] Ошибка импорта парсера: {e}")
    sys.exit(1)

STATE_FILE = Path("parse_state.txt")
PER_PAGE = 50

# --- КЕШИ ---
genres_cache = {}       # name -> Genre объект
existing_ids = set()    # уже существующие shikimori_id


# -------------------- UTILS --------------------

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


# -------------------- INIT CACHE --------------------

def preload_data(session):
    global existing_ids, genres_cache

    print("[*] Предзагрузка данных из БД...")

    # Все существующие ID загружаем в память для мгновенной проверки
    existing_ids = set(
        x[0] for x in session.query(Anime.shikimori_id).all()
    )

    # Все жанры загружаем в память
    genres = session.query(Genre).all()
    genres_cache = {g.name: g for g in genres}

    print(f"[+] Загружено {len(existing_ids)} аниме и {len(genres_cache)} жанров")


# -------------------- CORE --------------------

def build_anime_object(data, session):
    anime_id = data['shikimori_id']

    # Мгновенная проверка на дубликат (О(1))
    if anime_id in existing_ids:
        return None

    title_ru = data.get('russian') or data.get('name') or "Без названия"

    new_anime = Anime(
        shikimori_id=anime_id,
        title_ru=title_ru,
        title_en=data.get('name'),
        title_original=data.get('name'),
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

    # --- ЖАНРЫ С КЕШЕМ ---
    for g_name in data.get('genres', []):
        if not g_name:
            continue

        if g_name not in genres_cache:
            # Новый жанр - добавляем в БД и в кэш
            genre_obj = Genre(name=g_name)
            session.add(genre_obj)
            genres_cache[g_name] = genre_obj
        else:
            # Жанр уже есть - берем из кэша
            genre_obj = genres_cache[g_name]

        new_anime.genres.append(genre_obj)

    existing_ids.add(anime_id) # Добавляем новый ID в кэш
    return new_anime


def main():
    session = SessionLocal()
    page = load_last_page()

    print(f"[*] Старт со страницы: {page}")

    preload_data(session)

    try:
        while True:
            print(f"\n--- Страница {page} ---")

            ids = get_anime_ids(page=page, limit=PER_PAGE)
            if not ids:
                print("[*] Парсинг завершен! Новых аниме нет.")
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
                    print(f"[!] Ошибка обработки ID {a_id}: {e}")
                    continue

            # --- BATCH INSERT ---
            if batch:
                session.add_all(batch)
                session.commit()
            
            print(f"[+] Страница {page} обработана. Добавлено: {len(batch)}")

            save_last_page(page)
            page += 1

            time.sleep(1.5)

    except KeyboardInterrupt:
        print("\n[!] Остановка скрипта...")
        session.commit()

    except Exception as e:
        print(f"\n[!] Критическая ошибка: {e}")
        session.rollback()

    finally:
        session.close()
        print("[*] Завершено")


if __name__ == "__main__":
    main()