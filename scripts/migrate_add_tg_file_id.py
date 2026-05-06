# -*- coding: utf-8 -*-
"""
Migration: add 'tg_file_id' column to the anime table.

Idempotent — safe to run multiple times:
if the column already exists the script exits without errors.

Usage: python -m scripts.migrate_add_tg_file_id
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
        with engine.begin() as conn:  # auto-commit on exit, rollback on exception
            # Check for the column via PRAGMA before altering.
            result = conn.execute(text(f"PRAGMA table_info({TABLE})"))
            existing_columns = [row[1] for row in result.fetchall()]

            if COLUMN in existing_columns:
                logger.info(
                    "Column '%s' already exists in table '%s' — skipping.",
                    COLUMN, TABLE,
                )
                return

            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} TEXT"))
            logger.info(
                "Column '%s' successfully added to table '%s'.",
                COLUMN, TABLE,
            )

    except Exception:
        logger.exception("Error running migration")
        raise


if __name__ == "__main__":
    run()
