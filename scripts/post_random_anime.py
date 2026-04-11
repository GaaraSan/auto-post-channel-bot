import logging
import sys

from app.logging_config import setup_logging
from app.settings import DRY_RUN
from services.post_cycle import run_post_cycle

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    setup_logging()
    try:
        run_post_cycle(dry_run=DRY_RUN)
    except Exception:
        logger.exception("Критическая ошибка при запуске публикации")
        sys.exit(1)
