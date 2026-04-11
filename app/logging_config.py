# -*- coding: utf-8 -*-
"""
Централизованная настройка логирования для скриптов проекта.
Только стандартный модуль logging, без сторонних библиотек.
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
    Настраивает корневой логгер:
    - единый формат сообщений;
    - вывод в консоль;
    - при наличии пути LOG_FILE — дублирование в файл.

    Вызывать при старте скрипта (например, в __main__).
    """
    if stream is None:
        stream = sys.stderr

    # Уровень логирования можно переопределить через переменную окружения LOG_LEVEL
    env_level = os.getenv("LOG_LEVEL")
    if env_level:
        level = getattr(logging, env_level.upper(), level)

    # Путь к файлу логов: по умолчанию logs/bot.log в корне проекта.
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
                maxBytes=5 * 1024 * 1024,  # ~5 MB
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

    # Убираем шумные DEBUG/INFO логи от HTTP клиента библиотеки Telegram.
    # По умолчанию оставляем WARNING+, чтобы логи бота не засорялись getUpdates/send*.
    logging.getLogger("httpx").setLevel(logging.WARNING)
