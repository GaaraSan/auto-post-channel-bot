import os
from dotenv import load_dotenv  # type: ignore[reportMissingImports]

# .env должен быть загружен до создания STATE (см. app.runtime_state).
load_dotenv()

from app.runtime_state import STATE

# Путь к файлу базы данных SQLite
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_PATH = os.path.join(BASE_DIR, "anime.db")

# Строка подключения SQLAlchemy
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# Telegram Bot API token (обязателен для запуска telegram_bot).
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegram-канал для публикаций (username или числовой chat_id).
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")

# Dry-run режим публикаций (конфигурация по умолчанию из окружения):
# - полная логика выбора и форматирования;
# - НЕТ отправки в Telegram;
# - НЕТ записи в published_anime.
DRY_RUN = STATE.get_dry_run()

# Runtime-флаг dry-run, который можно менять командами Telegram-бота,
# без перезапуска процесса.
def get_runtime_dry_run() -> bool:
    return STATE.get_dry_run()


def set_runtime_dry_run(enabled: bool) -> None:
    STATE.set_dry_run(enabled)


def get_posting_enabled() -> bool:
    return STATE.get_posting_enabled()


def set_posting_enabled(enabled: bool) -> None:
    STATE.set_posting_enabled(enabled)


def get_post_interval() -> tuple[int, int]:
    return STATE.get_interval()


def set_post_interval(min_seconds: int, max_seconds: int) -> None:
    STATE.set_interval(min_seconds, max_seconds)


def get_is_posting_now() -> bool:
    return STATE.get_is_posting_now()


# Администраторы и разрешённые чаты для управления ботом.
# Ожидаются как списки id через запятую в окружении.
def _parse_id_list(value: str | None) -> list[int]:
    if not value:
        return []
    ids: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids

ADMIN_IDS: list[int] = _parse_id_list(os.getenv("ADMIN_IDS"))
ALLOWED_CHAT_IDS: list[int] = _parse_id_list(os.getenv("ALLOWED_CHAT_IDS"))
