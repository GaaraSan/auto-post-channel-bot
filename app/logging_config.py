# -*- coding: utf-8 -*-
"""
Centralized logging configuration for all project scripts.
Uses only the standard library — no third-party dependencies.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from app.settings import BASE_DIR


def setup_logging(
    level: int = logging.INFO,
    stream: Optional[object] = None,
) -> None:
    """
    Configure the root logger with a unified format.

    Outputs to stderr by default; also writes to a rotating log file
    (logs/bot.log) unless LOG_FILE env var overrides the path.

    Call once at script startup (e.g. in __main__).
    """
    if stream is None:
        stream = sys.stderr

    # Allow overriding the log level at runtime via LOG_LEVEL env var.
    env_level = os.getenv("LOG_LEVEL")
    if env_level:
        level = getattr(logging, env_level.upper(), level)

    # Default log file: <project_root>/logs/bot.log
    log_file = os.getenv("LOG_FILE")
    if not log_file:
        log_dir = os.path.join(BASE_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "bot.log")

    handlers = [logging.StreamHandler(stream)]
    if log_file:
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=5 * 1024 * 1024,  # 5 MB per file
                backupCount=5,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )

    # Suppress verbose DEBUG/INFO from the Telegram/httpx HTTP layer.
    # WARNING+ is enough — we don't need a log line per getUpdates poll.
    logging.getLogger("httpx").setLevel(logging.WARNING)
