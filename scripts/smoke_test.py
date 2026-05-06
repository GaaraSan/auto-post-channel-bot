import logging
import os
import sys
import asyncio

from sqlalchemy import text  # type: ignore[reportMissingImports]
from dotenv import load_dotenv # type: ignore[reportMissingImports]

from app.logging_config import setup_logging
from db.database import SessionLocal, engine
from db.models import Anime

logger = logging.getLogger(__name__)

# Load .env so BOT_TOKEN and other settings are available during the smoke test.
load_dotenv()


def check_db() -> bool:
    """Verify DB connectivity and basic ORM functionality."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        session = SessionLocal()
        try:
            session.query(Anime).limit(1).all()
        finally:
            session.close()
        logger.info("SMOKE DB: OK")
        return True
    except Exception:
        logger.exception("SMOKE DB: ERROR")
        return False


async def check_telegram_async() -> bool:
    """Verify the Telegram bot token without sending any messages."""
    from telegram import Bot as TgBot  # type: ignore[reportMissingImports]  # lazy import

    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("SMOKE TELEGRAM: BOT_TOKEN is not set")
        return False

    try:
        bot = TgBot(token=token)
        me = await bot.get_me()
        logger.info("SMOKE TELEGRAM: OK (bot=%s)", me.username)
        return True
    except Exception:
        logger.exception("SMOKE TELEGRAM: ERROR")
        return False


def main() -> None:
    setup_logging()
    ok_db = check_db()
    ok_tg = asyncio.run(check_telegram_async())

    if ok_db and ok_tg:
        logger.info("SMOKE TEST: OK")
        sys.exit(0)
    else:
        logger.error("SMOKE TEST: ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
