import asyncio
import logging

import httpx
from telegram import Bot
from telegram.error import BadRequest

from app.settings import BOT_TOKEN, CHANNEL_USERNAME

logger = logging.getLogger(__name__)

# Telegram's hard limit for send_photo uploads.
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB


async def send_post_async(text: str, image_url: str | None = None) -> None:
    """Send a post to the Telegram channel (no file_id caching)."""
    if not BOT_TOKEN or not CHANNEL_USERNAME:
        logger.error("BOT_TOKEN or CHANNEL_USERNAME is not set")
        raise RuntimeError("BOT_TOKEN or CHANNEL_USERNAME is not set")

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
        logger.debug("Post sent to channel %s", CHANNEL_USERNAME)
    except Exception as e:
        logger.exception("Failed to send post to Telegram: %s", e)
        raise


def send_post(text: str, image_url: str | None = None) -> None:
    """Synchronous wrapper around send_post_async."""
    asyncio.run(send_post_async(text, image_url))


async def send_post_with_cache_async(text: str, anime, session) -> None:
    """
    Send a post with Telegram file_id caching to avoid redundant image downloads.

    Scenario A — cached file_id exists:
        Send photo=tg_file_id.
        On BadRequest (stale file_id) → clear the cache and fall through to B.
        Other errors (network, rate limit) → re-raise without touching the cache.

    Scenario B — no cached file_id:
        Download via httpx (2 attempts) → send_photo(bytes) →
        store the returned file_id in the session without committing
        (publisher performs a single commit together with PublishedAnime).

    Fallback: download failed or file > 10 MB → send by image_url (no cache update).

    Note: session.get(AnimeModel, anime.id) is required because anime is a
    detached object from the already-closed get_random_anime session.
    Direct assignment to anime.tg_file_id would not be tracked by this session.
    """
    from db.models import Anime as AnimeModel  # local import to avoid circular imports

    if not BOT_TOKEN or not CHANNEL_USERNAME:
        logger.error("BOT_TOKEN or CHANNEL_USERNAME is not set")
        raise RuntimeError("BOT_TOKEN or CHANNEL_USERNAME is not set")

    bot = Bot(token=BOT_TOKEN)
    image_url = getattr(anime, "image_url", None)

    # ── Scenario A: use cached file_id ────────────────────────────────────────
    if anime.tg_file_id:
        try:
            await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=anime.tg_file_id, caption=text)
            logger.debug("Photo sent via cached file_id: %s", anime.tg_file_id)
            return
        except BadRequest as e:
            # BadRequest means the file_id is invalid — clear it and re-upload.
            # NetworkError / RateLimited are re-raised unchanged (no cache reset).
            logger.warning("file_id is invalid (BadRequest), clearing cache: %s", e)
            db_anime = session.get(AnimeModel, anime.id)
            if db_anime:
                db_anime.tg_file_id = None

    # ── Scenario B: download and send ─────────────────────────────────────────
    if not image_url:
        # No poster URL in DB at all — skip rather than post without image.
        logger.warning("image_url is missing for anime id=%s — skipping post", getattr(anime, "id", "?"))
        raise RuntimeError(f"No image_url for anime id={getattr(anime, 'id', '?')} — post skipped")

    # Download via httpx (async — does not block the event loop), up to 2 attempts.
    img_bytes: bytes | None = None
    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                img_bytes = resp.content
            logger.debug("Image downloaded (attempt %d): %d bytes", attempt, len(img_bytes))
            break
        except Exception as e:
            logger.warning("Attempt %d to download image failed: %s", attempt, e)

    if img_bytes:
        if len(img_bytes) > MAX_PHOTO_BYTES:
            # File too large for a direct upload — fall back to sending by URL.
            logger.warning(
                "Image too large (%d bytes > 10 MB) — falling back to URL",
                len(img_bytes),
            )
            try:
                await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=image_url, caption=text)
            except BadRequest as e:
                # Telegram couldn't fetch the URL from Shikimori either.
                # Re-raise so the publish cycle rolls back and skips this anime.
                logger.warning(
                    "URL fallback also failed (Telegram can't fetch image): %s", e
                )
                raise
            return

        # Send raw bytes — no temp file needed.
        message = await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=img_bytes, caption=text)

        file_id = message.photo[-1].file_id
        logger.debug("Photo sent, file_id received: %s", file_id)

        # Must re-fetch from session: anime is a detached object from another session.
        db_anime = session.get(AnimeModel, anime.id)
        if db_anime:
            db_anime.tg_file_id = file_id
        else:
            logger.warning(
                "Anime id=%s not found in session — file_id not cached", anime.id
            )
    else:
        # Download failed after all attempts — send by URL as a last resort.
        logger.warning(
            "Could not download image — falling back to URL: %s", image_url
        )
        try:
            await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=image_url, caption=text)
        except BadRequest as e:
            # Telegram couldn't fetch the URL from Shikimori either.
            # Re-raise so the publish cycle rolls back and skips this anime.
            logger.warning(
                "URL fallback also failed (Telegram can't fetch image): %s", e
            )
            raise


def send_post_with_cache(text: str, anime, session) -> None:
    """Synchronous wrapper around send_post_with_cache_async."""
    asyncio.run(send_post_with_cache_async(text, anime, session))
