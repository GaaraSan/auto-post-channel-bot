import os
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()

logger = logging.getLogger(__name__)


async def send_post_async(text: str, image_url: str | None = None) -> None:
    """
    Асинхронная отправка поста в Telegram-канал
    (совместимо с python-telegram-bot 22.x)
    """
    token = os.getenv("BOT_TOKEN")
    channel = os.getenv("CHANNEL_USERNAME")

    if not token or not channel:
        logger.error("BOT_TOKEN или CHANNEL_USERNAME не заданы")
        raise RuntimeError("BOT_TOKEN или CHANNEL_USERNAME не заданы")

    bot = Bot(token=token)

    try:
        if image_url:
            await bot.send_photo(
                chat_id=channel,
                photo=image_url,
                caption=text
            )
        else:
            await bot.send_message(
                chat_id=channel,
                text=text
            )
        logger.debug("Пост успешно отправлен в канал %s", channel)
    except Exception as e:
        logger.exception("Ошибка отправки поста в Telegram: %s", e)
        raise


def send_post(text: str, image_url: str | None = None) -> None:
    """
    Синхронная обёртка, чтобы остальной код
    не переписывать под async.
    """
    asyncio.run(send_post_async(text, image_url))
