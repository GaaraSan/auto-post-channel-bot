import asyncio
import logging

import httpx
from telegram import Bot
from telegram.error import BadRequest

from app.settings import BOT_TOKEN, CHANNEL_USERNAME

logger = logging.getLogger(__name__)

# Лимит Telegram для send_photo (bytes)
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB


async def send_post_async(text: str, image_url: str | None = None) -> None:
    """
    Асинхронная отправка поста в Telegram-канал (без кэширования).
    """
    if not BOT_TOKEN or not CHANNEL_USERNAME:
        logger.error("BOT_TOKEN или CHANNEL_USERNAME не заданы")
        raise RuntimeError("BOT_TOKEN или CHANNEL_USERNAME не заданы")

    bot = Bot(token=BOT_TOKEN)

    try:
        if image_url:
            await bot.send_photo(
                chat_id=CHANNEL_USERNAME,
                photo=image_url,
                caption=text
            )
        else:
            await bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=text
            )
        logger.debug("Пост успешно отправлен в канал %s", CHANNEL_USERNAME)
    except Exception as e:
        logger.exception("Ошибка отправки поста в Telegram: %s", e)
        raise


def send_post(text: str, image_url: str | None = None) -> None:
    """
    Синхронная обёртка, чтобы остальной код
    не переписывать под async.
    """
    asyncio.run(send_post_async(text, image_url))


async def send_post_with_cache_async(text: str, anime, session) -> None:
    """
    Отправка поста с кэшированием изображения через Telegram file_id.

    Сценарий A — file_id уже есть:
        Отправляем photo=tg_file_id.
        При BadRequest (невалидный file_id) — сбрасываем и переходим к B.
        Прочие ошибки (сеть, rate limit) — пробрасываем без сброса.

    Сценарий B — file_id нет:
        Скачиваем через httpx (2 попытки, async) → send_photo(bytes) →
        сохраняем file_id в session без commit
        (publisher делает единый commit вместе с PublishedAnime).

    Fallback: скачать не удалось или файл >10 MB → отправка по image_url.

    Примечание: session.get(AnimeModel, anime.id) необходим, потому что
    anime — detached объект из закрытой сессии get_random_anime.
    Прямое присвоение anime.tg_file_id не будет отслеживаться текущей сессией.
    """
    from db.models import Anime as AnimeModel  # локальный импорт — избегаем циклов

    if not BOT_TOKEN or not CHANNEL_USERNAME:
        logger.error("BOT_TOKEN или CHANNEL_USERNAME не заданы")
        raise RuntimeError("BOT_TOKEN или CHANNEL_USERNAME не заданы")

    bot = Bot(token=BOT_TOKEN)
    image_url = getattr(anime, "image_url", None)

    # ── Сценарий A: есть закэшированный file_id ──────────────────────────────
    if anime.tg_file_id:
        try:
            await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=anime.tg_file_id, caption=text)
            logger.debug("Фото отправлено по file_id (кэш): %s", anime.tg_file_id)
            return
        except BadRequest as e:
            # Только BadRequest = невалидный file_id → сбрасываем
            # NetworkError / RateLimited — пробрасываются без сброса
            logger.warning("file_id недействителен (BadRequest), сбрасываю: %s", e)
            db_anime = session.get(AnimeModel, anime.id)
            if db_anime:
                db_anime.tg_file_id = None

    # ── Сценарий B: скачиваем и отправляем ───────────────────────────────────
    if not image_url:
        logger.warning("image_url отсутствует — отправляем только текст")
        await bot.send_message(chat_id=CHANNEL_USERNAME, text=text)
        return

    # Скачиваем через httpx (async — не блокирует event loop), 2 попытки
    img_bytes: bytes | None = None
    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                img_bytes = resp.content
            logger.debug(
                "Изображение скачано (попытка %d): %d байт", attempt, len(img_bytes)
            )
            break
        except Exception as e:
            logger.warning("Попытка %d скачать изображение не удалась: %s", attempt, e)

    if img_bytes:
        # Telegram ограничивает send_photo до 10 MB
        if len(img_bytes) > MAX_PHOTO_BYTES:
            logger.warning(
                "Изображение слишком большое (%d байт > 10 MB) — fallback: URL",
                len(img_bytes),
            )
            await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=image_url, caption=text)
            return

        # Отправляем bytes напрямую — tempfile не нужен
        message = await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=img_bytes, caption=text)

        file_id = message.photo[-1].file_id
        logger.debug("Фото отправлено, file_id получен: %s", file_id)

        # session.get() необходим: anime — detached объект из другой сессии
        db_anime = session.get(AnimeModel, anime.id)
        if db_anime:
            db_anime.tg_file_id = file_id
        else:
            logger.warning(
                "Аниме id=%s не найдено в сессии — file_id не сохранён", anime.id
            )
    else:
        # Fallback: скачать не удалось → отправляем по URL (кэш не обновится)
        logger.warning(
            "Не удалось скачать изображение — fallback: отправка по URL: %s", image_url
        )
        await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=image_url, caption=text)


def send_post_with_cache(text: str, anime, session) -> None:
    """
    Синхронная обёртка send_post_with_cache_async.
    """
    asyncio.run(send_post_with_cache_async(text, anime, session))
