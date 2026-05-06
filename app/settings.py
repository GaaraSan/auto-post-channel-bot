import os
from dotenv import load_dotenv  # type: ignore[reportMissingImports]

# .env must be loaded before STATE is imported so env vars are visible to RuntimeState.
load_dotenv()

from app.runtime_state import STATE

# SQLite database path, resolved relative to the project root.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_PATH = os.path.join(BASE_DIR, "anime.db")

# SQLAlchemy connection string.
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# Telegram Bot API token (required to start the bot).
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegram channel for publishing posts (username or numeric chat_id).
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")

# Default dry-run flag, read once at startup from the environment.
# Can be toggled at runtime via Telegram commands without restarting.
DRY_RUN = STATE.get_dry_run()


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


# Admin and allowed-chat ID lists parsed from comma-separated env vars.
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
