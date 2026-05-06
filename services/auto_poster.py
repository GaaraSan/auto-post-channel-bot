import asyncio
import logging
import random

from app.runtime_state import STATE
from services.post_cycle import run_post_cycle
from telegram.error import NetworkError

logger = logging.getLogger(__name__)


async def auto_poster_loop() -> None:
    """
    Background auto-posting loop with a randomised interval.

    Reads posting_enabled and interval from RuntimeState on every iteration
    so they can be adjusted via Telegram commands without restarting the bot.
    Telegram send calls happen inside run_post_cycle (via executor),
    not directly in this coroutine.
    """
    logger.info("Auto-poster loop started")
    while True:
        try:
            if STATE.get_posting_enabled():
                dry_run = STATE.get_dry_run()
                result = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: run_post_cycle(dry_run=dry_run)
                )
                logger.info("Auto cycle result=%s", result.get("status") if isinstance(result, dict) else result)

            min_s, max_s = STATE.get_interval()
            interval = random.randint(min_s, max_s)
            logger.info("Auto next interval: %ss", interval)
            await asyncio.sleep(interval)
        except NetworkError as e:
            # Transient Telegram network error — log and retry after a short delay.
            logger.warning("Network error in auto-poster, continuing: %s", e)
            await asyncio.sleep(5)
        except Exception:
            logger.exception("Unexpected error in auto-poster loop, continuing")
            await asyncio.sleep(5)
