# -*- coding: utf-8 -*-
"""
Миграция: добавить колонки kind и members в таблицу anime.

Идемпотентна — безопасно запускать несколько раз:
если колонки уже существуют, скрипт завершится без ошибок.

Запуск: python -m scripts.migrate_add_kind_members
"""
import logging

from sqlalchemy import text

from app.logging_config import setup_logging
from db.database import engine

setup_logging()
logger = logging.getLogger(__name__)

TABLE = "anime"
COLUMNS = {
    "kind":    "TEXT",
    "members": "INTEGER",
}


def run() -> None:
    try:
        with engine.begin() as conn:
            result = conn.execute(text(f"PRAGMA table_info({TABLE})"))
            existing = {row[1] for row in result.fetchall()}

            for col, col_type in COLUMNS.items():
                if col in existing:
                    logger.info(
                        "Колонка '%s' уже существует в таблице '%s' — пропускаем.",
                        col, TABLE,
                    )
                else:
                    conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {col} {col_type}"))
                    logger.info(
                        "Колонка '%s' успешно добавлена в таблицу '%s'.",
                        col, TABLE,
                    )
    except Exception:
        logger.exception("Ошибка при выполнении миграции")
        raise


if __name__ == "__main__":
    run()
