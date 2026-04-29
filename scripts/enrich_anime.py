"""
scripts/enrich_anime.py

Дообогащает существующие записи в таблице anime полями kind и members,
запрашивая данные из Shikimori API.

Запуск:
    python scripts/enrich_anime.py
    python scripts/enrich_anime.py --batch-size 100 --delay 0.4
    python scripts/enrich_anime.py --reset-checkpoint   # начать с нуля
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

# Путь к checkpoint-файлу — рядом со скриптом
CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), "enrich_checkpoint.txt")


# ---------------------------------------------------------------------------
# Checkpoint: чтение / запись last_id для восстановления после остановки
# ---------------------------------------------------------------------------

def load_checkpoint() -> int:
    """Читает last_id из checkpoint-файла. Возвращает 0, если файла нет."""
    if not os.path.exists(CHECKPOINT_PATH):
        return 0
    try:
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            line = f.read().strip()          # «last_id=12345»
        value = int(line.split("=")[1])
        logger.info("Восстановление из checkpoint: last_id=%d (%s)", value, CHECKPOINT_PATH)
        return value
    except Exception as e:
        logger.warning("Не удалось прочитать checkpoint (%s): %s — старт с 0", CHECKPOINT_PATH, e)
        return 0


def save_checkpoint(last_id: int) -> None:
    """Сохраняет last_id в checkpoint-файл."""
    try:
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            f.write(f"last_id={last_id}\n")
    except Exception as e:
        logger.warning("Не удалось сохранить checkpoint: %s", e)


def delete_checkpoint() -> None:
    """Удаляет checkpoint-файл по завершении скрипта."""
    try:
        if os.path.exists(CHECKPOINT_PATH):
            os.remove(CHECKPOINT_PATH)
            logger.info("Checkpoint удалён (%s)", CHECKPOINT_PATH)
    except Exception as e:
        logger.warning("Не удалось удалить checkpoint: %s", e)



# ---------------------------------------------------------------------------
# Шаг 1: получить данные из Shikimori (с retry)
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_DELAY = 3.0   # секунд при 429 / сетевой ошибке

def fetch_kind_and_members(shikimori_id: int) -> tuple[str | None, int | None]:
    """
    Возвращает (kind, members) для указанного shikimori_id.
    Retry до MAX_RETRIES раз:
      - HTTP 429 → sleep RETRY_DELAY, повтор
      - timeout / сетевая ошибка → sleep RETRY_DELAY, повтор
      - прочие HTTP ошибки → сразу (None, None), retry не нужен
    """
    url = SHIKIMORI_API.format(shikimori_id)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)

            if resp.status_code == 429:
                wait = RETRY_DELAY * attempt
                logger.warning(
                    "Rate limit (429) для shikimori_id=%s, attempt=%d, sleep=%.1fs",
                    shikimori_id, attempt, wait,
                )
                time.sleep(wait)
                continue  # повторяем

            resp.raise_for_status()  # остальные 4xx/5xx — исключение

            data = resp.json()

            kind = data.get("kind")

            # Shikimori не отдаёт "members" напрямую;
            # считаем как сумму всех статусов из rates_statuses_stats
            stats = data.get("rates_statuses_stats") or []
            members = sum(entry.get("value", 0) for entry in stats)

            logger.debug(
                "shikimori_id=%s | kind=%s | members=%s | stats_len=%d",
                shikimori_id, kind, members, len(stats),
            )

            return kind, members

        except requests.Timeout:
            logger.warning(
                "Timeout для shikimori_id=%s, attempt=%d/%d",
                shikimori_id, attempt, MAX_RETRIES,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except requests.ConnectionError:
            logger.warning(
                "Сетевая ошибка для shikimori_id=%s, attempt=%d/%d",
                shikimori_id, attempt, MAX_RETRIES,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except requests.HTTPError as e:
            logger.warning("HTTP ошибка для shikimori_id=%s: %s", shikimori_id, e)
            break  # 4xx кроме 429 — бесполезно повторять
        except Exception as e:
            logger.warning("Неожиданная ошибка для shikimori_id=%s: %s", shikimori_id, e)
            break

    return None, None


# ---------------------------------------------------------------------------
# Шаг 2: основной цикл обогащения
# ---------------------------------------------------------------------------

def enrich(batch_size: int = 50, delay: float = 0.35) -> None:
    """
    Перебирает аниме, у которых members IS NULL или kind IS NULL,
    и дообогащает их из Shikimori API.

    Перед первым запуском на существующей БД выполни миграцию:
        python -m scripts.migrate_add_kind_members
    """
    session = SessionLocal()
    try:
        # Считаем, сколько записей нужно обработать (относительно всей таблицы)
        total = (
            session.query(Anime)
            .filter(Anime.members.is_(None) | Anime.kind.is_(None))
            .count()
        )
        logger.info("Записей для обогащения: %d", total)

        if total == 0:
            logger.info("Все записи уже обогащены — ничего делать не нужно.")
            delete_checkpoint()
            return

        # Восстановление: читаем last_id из checkpoint (0 если файла нет)
        last_id = load_checkpoint()

        processed = 0
        updated = 0
        skipped = 0
        batch_num = 0

        while True:
            # Cursor-based pagination: не смещаемся по строкам,
            # а идём по первичному ключу — стабильно и O(log n)
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
                    if members is not None:
                        anime.members = members
                    updated += 1
                else:
                    skipped += 1

                processed += 1
                time.sleep(delay)

            session.commit()
            batch_num += 1
            last_id = batch[-1].id  # двигаем cursor на последний обработанный id

            # Сохраняем checkpoint после каждого батча
            save_checkpoint(last_id)

            remaining = max(0, total - processed)
            logger.info(
                "Батч #%d | обработано: %d / %d | осталось: ~%d"
                " | обновлено: %d | пропущено: %d | last_id: %d",
                batch_num, processed, total, remaining,
                updated, skipped, last_id,
            )

        logger.info(
            "Обогащение завершено. Всего: %d | обновлено: %d | пропущено: %d",
            processed, updated, skipped,
        )
        delete_checkpoint()  # успешно завершили — checkpoint больше не нужен

    except KeyboardInterrupt:
        # Ctrl+C во время запроса к API или sleep
        # commit последнего батча уже был — checkpoint актуален
        session.rollback()  # откат текущего незакоммиченного батча (если был)
        logger.warning(
            "Прервано пользователем (Ctrl+C). "
            "Checkpoint сохранён: last_id=%d. "
            "Для продолжения запустите скрипт снова.",
            last_id if 'last_id' in locals() else 0,
        )
        # не re-raise — выходим чисто, без traceback
    except Exception:
        session.rollback()
        logger.exception(
            "Критическая ошибка при обогащении — сессия откатана. "
            "Checkpoint сохранён (last_id=%d), перезапустите скрипт для продолжения.",
            last_id if 'last_id' in locals() else 0,
        )
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Дообогащение аниме: kind + members")
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Размер батча (по умолчанию 50)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.35,
        help="Задержка между запросами в секундах (по умолчанию 0.35)",
    )
    parser.add_argument(
        "--reset-checkpoint", action="store_true",
        help="Удалить checkpoint и начать обогащение с нуля",
    )
    args = parser.parse_args()

    if args.reset_checkpoint:
        delete_checkpoint()
        logger.info("Checkpoint сброшен — старт с id=0")

    try:
        enrich(batch_size=args.batch_size, delay=args.delay)
    except KeyboardInterrupt:
        pass  # уже обработано внутри enrich() с логом
