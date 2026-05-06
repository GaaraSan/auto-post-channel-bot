# -*- coding: utf-8 -*-
"""
Migration: add 'kind' and 'members' columns to the anime table.

Idempotent — safe to run multiple times:
if the columns already exist the script exits without errors.

Usage: python -m scripts.migrate_add_kind_members
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
                        "Column '%s' already exists in table '%s' — skipping.",
                        col, TABLE,
                    )
                else:
                    conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {col} {col_type}"))
                    logger.info(
                        "Column '%s' successfully added to table '%s'.",
                        col, TABLE,
                    )
    except Exception:
        logger.exception("Error running migration")
        raise


if __name__ == "__main__":
    run()
