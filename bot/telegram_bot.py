import logging
import sys
import asyncio
import os
import time
import random
import traceback

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
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

PAGE_SIZE = 5  # Аниме на одной странице при поиске по названию


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    user_id = user.id if user else None
    chat_id = chat.id if chat else None
    if ADMIN_IDS and (user_id not in ADMIN_IDS):
        logger.warning("Доступ запрещён: user_id=%s не в ADMIN_IDS", user_id)
        return False
    if ALLOWED_CHAT_IDS and (chat_id not in ALLOWED_CHAT_IDS):
        logger.warning("Доступ запрещён: chat_id=%s не в ALLOWED_CHAT_IDS", chat_id)
        return False
    return True


def _build_search_keyboard(results: list, page: int, session_id: str) -> InlineKeyboardMarkup:
    """
    Строит inline-клавиатуру для страницы результатов поиска.
    results:    [(anime_id, title_ru, title_en, year, kind, shikimori_id), ...]
    session_id: уникальный ключ сессии — вшивается в callback_data
    """
    max_page = max(0, (len(results) - 1) // PAGE_SIZE)
    page = max(0, min(page, max_page))
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE

    buttons = []
    for anime_id, title_ru, title_en, year, kind, shikimori_id in results[start:end]:
        title = title_ru or title_en or "Без названия"
        meta_parts = []
        if kind:
            meta_parts.append(kind.upper())
        if year:
            meta_parts.append(str(year))
        meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
        label = f"{title}{meta} • ID {shikimori_id}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"pick:{anime_id}:{session_id}")])

    # Индикатор страницы
    buttons.append([InlineKeyboardButton(
        f"📄 Стр. {page + 1} / {max_page + 1}",
        callback_data=f"noop:{session_id}",
    )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀◀ Назад", callback_data=f"page:{page - 1}:{session_id}"))
    if end < len(results):
        nav.append(InlineKeyboardButton("Вперёд ▶▶", callback_data=f"page:{page + 1}:{session_id}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cancel:{session_id}")])
    return InlineKeyboardMarkup(buttons)


def _publish_anime_by_db_id(anime_id: int, dry_run: bool) -> str:
    """Общий хелпер: открывает сессию, ищет Anime по db-id, вызывает publish_anime."""
    from db.database import SessionLocal
    from db.models import Anime
    from services.publisher import publish_anime
    session = SessionLocal()
    try:
        anime = session.query(Anime).filter(Anime.id == anime_id).first()
        if not anime:
            return "Аниме не найдено в базе данных."
        title = anime.title_ru or anime.title_en or "Без названия"
        publish_anime(anime, dry_run=dry_run)
        prefix = "[DRY RUN] " if dry_run else ""
        return f"{prefix}Опубликовано: {title}"
    finally:
        session.close()


_SEARCH_TTL = 15 * 60  # Сессии поиска живут 15 минут


def _cleanup_stale_searches(bot_data: dict) -> None:
    """Удаляет устаревшие сессии поиска (старше _SEARCH_TTL секунд)."""
    searches = bot_data.get("anime_searches", {})
    now = time.time()
    stale = [k for k, v in searches.items() if now - v.get("created_at", 0) > _SEARCH_TTL]
    for k in stale:
        del searches[k]


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return

    dry_run = settings.get_runtime_dry_run()
    await update.effective_chat.send_message(f"Запускаю постинг (dry_run={dry_run})...")
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: run_post_cycle(dry_run=dry_run))

        if isinstance(result, dict) and result.get("status") == "error":
            await update.effective_chat.send_message(
                "Ошибка выбора аниме (см. logs/bot.log). Публикация пропущена."
            )
            return

        if dry_run:
            title = result.get("title") if isinstance(result, dict) else None
            lines = ["Старт публикации (dry_run=True)"]
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

    По числу — прямой поиск по shikimori_id, публикация без кнопок.
    По тексту — AND-поиск по словам в title_ru/title_en:
      0 результатов → сообщение "Ничего не найдено"
      1 результат   → сразу publish_anime
      >1 результата → inline-кнопки с пагинацией
    """
    if not _is_admin(update):
        await update.effective_chat.send_message("Нет доступа.")
        return

    if not context.args:
        await update.effective_chat.send_message(
            "Использование:\n"
            "  /post_anime 5114        — по shikimori_id\n"
            "  /post_anime Наруто     — по названию (title_ru / title_en)"
        )
        return

    query = " ".join(context.args).strip()
    dry_run = settings.get_runtime_dry_run()
    loop = asyncio.get_running_loop()

    # ── По shikimori_id ───────────────────────────────────────────────────────
    if query.lstrip("-").isdigit():
        def _find_by_id() -> str:
            from db.database import SessionLocal
            from db.models import Anime
            session = SessionLocal()
            try:
                row = session.query(Anime.id).filter(
                    Anime.shikimori_id == int(query)
                ).first()
                if not row:
                    return f"Аниме не найдено: shikimori_id={query}"
                return _publish_anime_by_db_id(row.id, dry_run)
            finally:
                session.close()

        await update.effective_chat.send_message(f"Ищу shikimori_id={query}...")
        try:
            result = await loop.run_in_executor(None, _find_by_id)
            await update.effective_chat.send_message(result)
        except Exception as e:
            logger.exception("Ошибка /post_anime по id: %s", e)
            await update.effective_chat.send_message("Ошибка при публикации. Подробности в логах.")
        return

    # ── По названию: AND-поиск ────────────────────────────────────────────────
    def _search_by_title() -> list:
        from db.database import SessionLocal
        from db.models import Anime
        from sqlalchemy import func
        session = SessionLocal()
        try:
            words = query.lower().split()
            q = session.query(
                Anime.id, Anime.title_ru, Anime.title_en,
                Anime.year, Anime.kind, Anime.shikimori_id,
            )
            for word in words:
                pattern = f"%{word}%"
                q = q.filter(
                    func.lower(Anime.title_ru).like(pattern)
                    | func.lower(Anime.title_en).like(pattern)
                )
            rows = q.order_by(Anime.members.desc(), Anime.year.desc()).limit(50).all()
            return [(r.id, r.title_ru, r.title_en, r.year, r.kind, r.shikimori_id) for r in rows]
        finally:
            session.close()

    try:
        results = await loop.run_in_executor(None, _search_by_title)
    except Exception as e:
        logger.exception("Ошибка при поиске аниме: %s", e)
        await update.effective_chat.send_message("Ошибка при поиске. Подробности в логах.")
        return

    if not results:
        await update.effective_chat.send_message(f"Ничего не найдено по запросу: {query!r}")
        return

    # 1 результат — публикуем сразу без кнопок
    if len(results) == 1:
        try:
            result = await loop.run_in_executor(
                None, lambda: _publish_anime_by_db_id(results[0][0], dry_run)
            )
            await update.effective_chat.send_message(result)
        except Exception as e:
            logger.exception("Ошибка при публикации единственного результата: %s", e)
            await update.effective_chat.send_message("Ошибка при публикации. Подробности в логах.")
        return

    # >1 результата — генерируем session_id, строим клавиатуру, отправляем в один вызов
    total = len(results)
    suffix = " (показаны первые 50)" if total == 50 else ""
    session_id = f"{int(time.time())}{random.randint(1000, 9999)}"
    _cleanup_stale_searches(context.bot_data)
    context.bot_data.setdefault("anime_searches", {})
    context.bot_data["anime_searches"][session_id] = {
        "results": results,
        "page": 0,
        "created_at": time.time(),
    }
    keyboard = _build_search_keyboard(results, 0, session_id)
    sent = await update.effective_chat.send_message(
        f"🔍 Найдено: {total}{suffix}. Выберите аниме:",
        reply_markup=keyboard,
    )
    context.bot_data["anime_searches"][session_id]["message_id"] = sent.message_id


# ---------------------------------------------------------------------------
# Callback handlers (inline-кнопки для /post_anime)
# ---------------------------------------------------------------------------

async def _cb_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор аниме → публикация → отправить результат → удалить кнопки."""
    query = update.callback_query
    if not _is_admin(update):
        await query.answer("Нет доступа.", show_alert=True)
        return
    if not query.message:
        await query.answer()
        return
    try:
        parts      = query.data.split(":")           # pick:{anime_id}:{session_id}
        anime_id   = int(parts[1])
        session_id = parts[2]
    except (IndexError, ValueError):
        await query.answer("Некорректные данные кнопки", show_alert=True)
        return
    await query.answer()  # единственный вызов в пути успеха
    _cleanup_stale_searches(context.bot_data)
    search_data = context.bot_data.get("anime_searches", {}).get(session_id)
    if search_data and search_data.get("message_id") != query.message.message_id:
        return
    dry_run = settings.get_runtime_dry_run()
    chat_id = query.message.chat_id
    try:
        loop = asyncio.get_running_loop()
        result_text = await loop.run_in_executor(
            None, lambda: _publish_anime_by_db_id(anime_id, dry_run)
        )
    except Exception as e:
        logger.exception("Ошибка при публикации через кнопку pick: %s", e)
        result_text = "Ошибка при публикации. Подробности в логах."
    context.bot_data.get("anime_searches", {}).pop(session_id, None)
    await context.bot.send_message(chat_id, result_text)   # сначала результат
    try:
        await query.message.delete()                        # потом удаляем кнопки
    except Exception:
        pass


async def _cb_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Перелистывание — обновляет клавиатуру без новых сообщений."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(update):
        return
    if not query.message:
        return
    try:
        parts      = query.data.split(":")           # page:{page}:{session_id}
        page       = int(parts[1])
        session_id = parts[2]
    except (IndexError, ValueError):
        await query.answer("Некорректные данные кнопки", show_alert=True)
        return
    _cleanup_stale_searches(context.bot_data)
    search_data = context.bot_data.get("anime_searches", {}).get(session_id)
    if not search_data:
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    if search_data.get("message_id") != query.message.message_id:
        return
    results  = search_data["results"]
    max_page = max(0, (len(results) - 1) // PAGE_SIZE)
    page     = max(0, min(page, max_page))
    search_data["page"] = page
    keyboard = _build_search_keyboard(results, page, session_id)
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except BadRequest:
        pass  # Message is not modified — нормально


async def _cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отмена — удаляет сообщение с кнопками, ничего не отправляет."""
    query = update.callback_query
    await query.answer()
    if not query.message:
        return
    try:
        parts      = query.data.split(":")           # cancel:{session_id}
        session_id = parts[1]
    except IndexError:
        return
    _cleanup_stale_searches(context.bot_data)
    search_data = context.bot_data.get("anime_searches", {}).get(session_id)
    if search_data and search_data.get("message_id") != query.message.message_id:
        return
    context.bot_data.get("anime_searches", {}).pop(session_id, None)
    try:
        await query.message.delete()
    except Exception:
        pass


async def _cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка без действия (индикатор страницы)."""
    await update.callback_query.answer()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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

            # Команды
            application.add_handler(CommandHandler("post_now", post_now))
            application.add_handler(CommandHandler("post_anime", post_anime))
            application.add_handler(CommandHandler("status", status))
            application.add_handler(CommandHandler("dry_run_on", dry_run_on))
            application.add_handler(CommandHandler("dry_run_off", dry_run_off))
            application.add_handler(CommandHandler("posting_on", posting_on))
            application.add_handler(CommandHandler("posting_off", posting_off))
            application.add_handler(CommandHandler("interval", set_interval))

            # Callback-кнопки для /post_anime
            application.add_handler(CallbackQueryHandler(_cb_pick,   pattern=r"^pick:\d+:[\w\-]+$"))
            application.add_handler(CallbackQueryHandler(_cb_page,   pattern=r"^page:\d+:[\w\-]+$"))
            application.add_handler(CallbackQueryHandler(_cb_cancel, pattern=r"^cancel:[\w\-]+$"))
            application.add_handler(CallbackQueryHandler(_cb_noop,   pattern=r"^noop:"))

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
