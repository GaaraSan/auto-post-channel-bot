# -*- coding: utf-8 -*-
"""
Миграция: добавить колонку tg_file_id в таблицу anime.

Идемпотентна — безопасно запускать несколько раз:
если колонка уже существует, скрипт завершится без ошибок.

Запуск: python -m scripts.migrate_add_tg_file_id
"""
import logging

from sqlalchemy import text

from app.logging_config import setup_logging
from db.database import engine

setup_logging()
logger = logging.getLogger(__name__)

COLUMN = "tg_file_id"
TABLE  = "anime"


def run() -> None:
    try:
        with engine.begin() as conn:  # auto-commit при выходе, rollback при исключении
            # Проверяем наличие колонки через PRAGMA
            result = conn.execute(text(f"PRAGMA table_info({TABLE})"))
            existing_columns = [row[1] for row in result.fetchall()]

            if COLUMN in existing_columns:
                logger.info(
                    "Колонка '%s' уже существует в таблице '%s' — пропускаем.",
                    COLUMN, TABLE,
                )
                return

            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} TEXT"))
            logger.info(
                "Колонка '%s' успешно добавлена в таблицу '%s'.",
                COLUMN, TABLE,
            )

    except Exception:
        logger.exception("Ошибка при выполнении миграции")
        raise


if __name__ == "__main__":
    run()
