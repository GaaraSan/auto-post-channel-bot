import asyncio
import logging
import random

from app.runtime_state import STATE
from services.post_cycle import run_post_cycle

logger = logging.getLogger(__name__)


async def auto_poster_loop() -> None:
    """
    Автопостинг с случайным интервалом.

    Не отправляет сообщения в Telegram напрямую.
    Управляется через RuntimeState (enabled + interval).
    """
    logger.info("AUTO poster loop started")
    while True:
        try:
            if STATE.get_posting_enabled():
                dry_run = STATE.get_dry_run()
                result = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: run_post_cycle(dry_run=dry_run)
                )
                logger.info("AUTO cycle result=%s", result.get("status") if isinstance(result, dict) else result)

            min_s, max_s = STATE.get_interval()
            interval = random.randint(min_s, max_s)
            logger.info("AUTO next interval: %ss", interval)
            await asyncio.sleep(interval)
        except Exception:
            logger.exception("Ошибка в AUTO loop; продолжаю работу")
            await asyncio.sleep(5)

