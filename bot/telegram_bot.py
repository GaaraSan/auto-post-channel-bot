import logging
import sys
import asyncio
import os
import traceback

from telegram import Update  # type: ignore[reportMissingImports]
from telegram.error import NetworkError  # type: ignore[reportMissingImports]
from telegram.ext import (  # type: ignore[reportMissingImports]
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from app.logging_config import setup_logging
from app.settings import (
    ADMIN_IDS,
    ALLOWED_CHAT_IDS,
    BOT_TOKEN,
)
import app.settings as settings
from app.lockfile import LockAlreadyHeldError, file_lock
from services.post_cycle import run_post_cycle
from services.auto_poster import auto_poster_loop

logger = logging.getLogger(__name__)

# Ссылка на фоновую задачу автопостинга (отмена при остановке Application).
_auto_poster_task_holder: list[asyncio.Task | None] = [None]


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat

    user_id = user.id if user else None
    chat_id = chat.id if chat else None

    logger.info(
        "Команда от user_id=%s username=%s chat_id=%s",
        user_id,
        getattr(user, "username", None),
        chat_id,
    )

    if ADMIN_IDS and (user_id not in ADMIN_IDS):
        logger.warning("Доступ запрещён: пользователь не в списке администраторов")
        return False

    if ALLOWED_CHAT_IDS and (chat_id not in ALLOWED_CHAT_IDS):
        logger.warning("Доступ запрещён: команда вызвана из неразрешённого чата")
        return False

    return True


async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return

    dry_run = settings.get_runtime_dry_run()
    await update.effective_chat.send_message(f"Запускаю постинг (dry_run={dry_run})...")
    try:
        # run_post_cycle синхронная; выполняем её в thread-пуле, чтобы не блокировать event loop.
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: run_post_cycle(dry_run=dry_run))

        if isinstance(result, dict) and result.get("status") == "error":
            await update.effective_chat.send_message(
                "Ошибка выбора аниме (см. logs/bot.log). Публикация пропущена."
            )
            return

        if dry_run:
            title = result.get("title") if isinstance(result, dict) else None
            lines = [
                "Старт публикации (dry_run=True)",
            ]
            if title:
                lines.append(f"Попытка публикации: {title}")
            lines.append("DRY RUN: сообщение НЕ отправлено и НЕ записано в БД")
            await update.effective_chat.send_message("\n".join(lines))

        await update.effective_chat.send_message("Цикл публикации завершён.")
    except Exception as e:
        logger.exception("Ошибка при выполнении /post_now: %s", e)
        await update.effective_chat.send_message("Ошибка при выполнении команды. Подробности в логах.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return

    dry_run = settings.get_runtime_dry_run()
    posting_enabled = settings.get_posting_enabled()
    min_s, max_s = settings.get_post_interval()
    is_posting_now = settings.get_is_posting_now()
    text = (
        "Бот работает.\n"
        f"DRY_RUN: {'ON' if dry_run else 'OFF'}\n"
        f"AUTO posting: {'ON' if posting_enabled else 'OFF'}\n"
        f"Interval: {min_s}..{max_s} sec\n"
        f"Posting now: {'YES' if is_posting_now else 'NO'}"
    )
    await update.effective_chat.send_message(text)


async def dry_run_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return

    settings.set_runtime_dry_run(True)
    logger.info("DRY_RUN включён через Telegram")
    await update.effective_chat.send_message("DRY RUN: ON")


async def dry_run_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return

    settings.set_runtime_dry_run(False)
    logger.info("DRY_RUN выключен через Telegram")
    await update.effective_chat.send_message("DRY RUN: OFF")


async def posting_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return
    settings.set_posting_enabled(True)
    logger.info("AUTO posting включён через Telegram")
    await update.effective_chat.send_message("AUTO posting: ON")


async def posting_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return
    settings.set_posting_enabled(False)
    logger.info("AUTO posting выключен через Telegram")
    await update.effective_chat.send_message("AUTO posting: OFF")


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return
    try:
        if not context.args or len(context.args) != 2:
            await update.effective_chat.send_message("Использование: /interval <min_sec> <max_sec>")
            return
        min_s = int(context.args[0])
        max_s = int(context.args[1])
        settings.set_post_interval(min_s, max_s)
        logger.info("Interval изменён через Telegram: %s..%s", min_s, max_s)
        await update.effective_chat.send_message(f"Interval set: {min_s}..{max_s} sec")
    except Exception:
        logger.exception("Ошибка при установке интервала")
        await update.effective_chat.send_message("Ошибка: не удалось установить интервал.")


async def post_anime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /post_anime <shikimori_id|название>
    Публикует конкретное аниме по shikimori_id (число) или по названию (поиск по title_ru / title_en).
    """
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return

    if not context.args:
        await update.effective_chat.send_message(
            "Использование:\n"
            "  /post_anime 5114          — по shikimori_id\n"
            "  /post_anime Наруто       — по названию (title_ru / title_en)"
        )
        return

    query = " ".join(context.args).strip()
    dry_run = settings.get_runtime_dry_run()

    def _find_and_publish() -> str:
        from db.database import SessionLocal
        from db.models import Anime
        from services.publisher import publish_anime
        from sqlalchemy import func

        session = SessionLocal()
        try:
            anime = None

            # Поиск по shikimori_id если аргумент — число
            if query.lstrip("-").isdigit():
                anime = session.query(Anime).filter(
                    Anime.shikimori_id == int(query)
                ).first()
            else:
                # Поиск по title_ru или title_en (без учёта регистра, частичное совпадение)
                pattern = f"%{query}%"
                anime = session.query(Anime).filter(
                    (func.lower(Anime.title_ru).like(func.lower(pattern)))
                    | (func.lower(Anime.title_en).like(func.lower(pattern)))
                ).first()

            if not anime:
                return f"Аниме не найдено: {query!r}"

            title = anime.title_ru or anime.title_en or "Без названия"
            publish_anime(anime, dry_run=dry_run)
            prefix = "[DRY RUN] " if dry_run else ""
            return f"{prefix}Опубликовано: {title}"

        finally:
            session.close()

    await update.effective_chat.send_message(f"Ищу: {query!r} (dry_run={dry_run})...")
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _find_and_publish)
        await update.effective_chat.send_message(result)
    except Exception as e:
        logger.exception("Ошибка при выполнении /post_anime: %s", e)
        await update.effective_chat.send_message("Ошибка при публикации. Подробности в логах.")


def main() -> None:
    setup_logging()

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан в окружении или settings")
        sys.exit(1)

    try:
        lock_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "logs", "telegram_bot.lock"
        )

        with file_lock(lock_path):
            async def _post_init(application) -> None:
                # Запускаем авто-постинг в рамках того же process и event loop.
                _auto_poster_task_holder[0] = asyncio.create_task(auto_poster_loop())

            async def _post_shutdown(application) -> None:
                task = _auto_poster_task_holder[0]
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                _auto_poster_task_holder[0] = None

            async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
                err = getattr(context, "error", None)
                if err is None:
                    return
                # Сетевые сбои при long polling часты (Wi‑Fi, DNS, офлайн); PTB сам повторяет запросы.
                # Полный traceback на каждую попытку раздувает лог без пользы.
                if isinstance(err, NetworkError):
                    logger.warning(
                        "Telegram: сеть/тайм-аут, будет повтор (get_updates): %s",
                        err,
                    )
                    return
                logger.error("Telegram handler error: %s", err)
                logger.error(
                    "Telegram error traceback:\n%s",
                    "".join(traceback.format_exception(err)),
                )

            application = (
                ApplicationBuilder()
                .token(BOT_TOKEN)
                .post_init(_post_init)
                .post_shutdown(_post_shutdown)
                .build()
            )

            application.add_error_handler(_on_error)
            application.add_handler(CommandHandler("post_now", post_now))
            application.add_handler(CommandHandler("post_anime", post_anime))
            application.add_handler(CommandHandler("status", status))
            application.add_handler(CommandHandler("dry_run_on", dry_run_on))
            application.add_handler(CommandHandler("dry_run_off", dry_run_off))
            application.add_handler(CommandHandler("posting_on", posting_on))
            application.add_handler(CommandHandler("posting_off", posting_off))
            application.add_handler(CommandHandler("interval", set_interval))

            logger.info("Telegram bot started")
            application.run_polling()
    except LockAlreadyHeldError:
        logger.warning("Telegram bot already running (lock=%s)", lock_path)
        return
    except Exception:
        logger.exception("Критическая ошибка Telegram-бота")
        sys.exit(1)


if __name__ == "__main__":
    main()

