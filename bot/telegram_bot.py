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

# Holds a reference to the auto-poster background task so it can be cancelled on shutdown.
_auto_poster_task_holder: list[asyncio.Task | None] = [None]

PAGE_SIZE = 5  # Anime entries shown per page in search results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    user_id = user.id if user else None
    chat_id = chat.id if chat else None
    if ADMIN_IDS and (user_id not in ADMIN_IDS):
        logger.warning("Access denied: user_id=%s not in ADMIN_IDS", user_id)
        return False
    if ALLOWED_CHAT_IDS and (chat_id not in ALLOWED_CHAT_IDS):
        logger.warning("Access denied: chat_id=%s not in ALLOWED_CHAT_IDS", chat_id)
        return False
    return True


def _build_search_keyboard(results: list, page: int, session_id: str) -> InlineKeyboardMarkup:
    """
    Build an inline keyboard for one page of search results.

    results:    [(anime_id, title_ru, title_en, year, kind, shikimori_id), ...]
    session_id: unique session key embedded in every callback_data string
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

    # Page indicator button (non-interactive).
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
    """Fetch anime by internal DB id and publish it. Returns a status string."""
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


_SEARCH_TTL = 15 * 60  # Search sessions expire after 15 minutes


def _cleanup_stale_searches(bot_data: dict) -> None:
    """Remove expired search sessions (older than _SEARCH_TTL seconds)."""
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
        await update.effective_chat.send_message("Access denied.")
        return

    dry_run = settings.get_runtime_dry_run()
    await update.effective_chat.send_message(f"Starting post cycle (dry_run={dry_run})...")
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: run_post_cycle(dry_run=dry_run))

        if isinstance(result, dict) and result.get("status") == "error":
            await update.effective_chat.send_message(
                "Anime selection failed (see logs/bot.log). Post skipped."
            )
            return

        if dry_run:
            title = result.get("title") if isinstance(result, dict) else None
            lines = ["Post cycle started (dry_run=True)"]
            if title:
                lines.append(f"Would publish: {title}")
            lines.append("DRY RUN: message NOT sent, DB NOT updated")
            await update.effective_chat.send_message("\n".join(lines))

        await update.effective_chat.send_message("Post cycle complete.")
    except Exception as e:
        logger.exception("Error in /post_now: %s", e)
        await update.effective_chat.send_message("Command failed. Check logs for details.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Access denied.")
        return

    dry_run = settings.get_runtime_dry_run()
    posting_enabled = settings.get_posting_enabled()
    min_s, max_s = settings.get_post_interval()
    is_posting_now = settings.get_is_posting_now()
    text = (
        "Bot is running.\n"
        f"DRY_RUN: {'ON' if dry_run else 'OFF'}\n"
        f"AUTO posting: {'ON' if posting_enabled else 'OFF'}\n"
        f"Interval: {min_s}..{max_s} sec\n"
        f"Posting now: {'YES' if is_posting_now else 'NO'}"
    )
    await update.effective_chat.send_message(text)


async def dry_run_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Access denied.")
        return
    settings.set_runtime_dry_run(True)
    logger.info("DRY_RUN enabled via Telegram")
    await update.effective_chat.send_message("DRY RUN: ON")


async def dry_run_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Access denied.")
        return
    settings.set_runtime_dry_run(False)
    logger.info("DRY_RUN disabled via Telegram")
    await update.effective_chat.send_message("DRY RUN: OFF")


async def posting_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Access denied.")
        return
    settings.set_posting_enabled(True)
    logger.info("AUTO posting enabled via Telegram")
    await update.effective_chat.send_message("AUTO posting: ON")


async def posting_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Access denied.")
        return
    settings.set_posting_enabled(False)
    logger.info("AUTO posting disabled via Telegram")
    await update.effective_chat.send_message("AUTO posting: OFF")


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.effective_chat.send_message("Access denied.")
        return
    try:
        if not context.args or len(context.args) != 2:
            await update.effective_chat.send_message("Usage: /interval <min_sec> <max_sec>")
            return
        min_s = int(context.args[0])
        max_s = int(context.args[1])
        settings.set_post_interval(min_s, max_s)
        logger.info("Interval changed via Telegram: %s..%s", min_s, max_s)
        await update.effective_chat.send_message(f"Interval set: {min_s}..{max_s} sec")
    except Exception:
        logger.exception("Error setting interval")
        await update.effective_chat.send_message("Error: could not set interval.")


async def post_anime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /post_anime <shikimori_id|title>

    Numeric input: direct lookup by shikimori_id, publishes immediately.
    Text input: AND-search across title_ru / title_en:
      0 results  → "Nothing found" message
      1 result   → publish immediately
      >1 results → inline keyboard with pagination
    """
    if not _is_admin(update):
        await update.effective_chat.send_message("Access denied.")
        return

    if not context.args:
        await update.effective_chat.send_message(
            "Usage:\n"
            "  /post_anime 5114       — by shikimori_id\n"
            "  /post_anime Naruto     — by title (title_ru / title_en)"
        )
        return

    query = " ".join(context.args).strip()
    if len(query) > 100:
        await update.effective_chat.send_message("Query too long. Maximum 100 characters.")
        return
    dry_run = settings.get_runtime_dry_run()
    loop = asyncio.get_running_loop()

    # ── Lookup by shikimori_id ────────────────────────────────────────────────
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
                    return f"Anime not found: shikimori_id={query}"
                return _publish_anime_by_db_id(row.id, dry_run)
            finally:
                session.close()

        await update.effective_chat.send_message(f"Looking up shikimori_id={query}...")
        try:
            result = await loop.run_in_executor(None, _find_by_id)
            await update.effective_chat.send_message(result)
        except Exception as e:
            logger.exception("Error in /post_anime by id: %s", e)
            await update.effective_chat.send_message("Publish failed. Check logs for details.")
        return

    # ── AND-search by title ───────────────────────────────────────────────────
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

    def _search_fuzzy() -> list:
        """Fuzzy search via rapidfuzz. Searches top-3000 by popularity with normalised strings."""
        from rapidfuzz import fuzz, process
        from db.database import SessionLocal
        from db.models import Anime
        session = SessionLocal()
        try:
            rows = session.query(
                Anime.id, Anime.title_ru, Anime.title_en,
                Anime.year, Anime.kind, Anime.shikimori_id,
            ).order_by(Anime.members.desc()).limit(3000).all()

            keys: list[str] = []
            data: list[tuple] = []
            for r in rows:
                combined = f"{r.title_ru or ''} {r.title_en or ''}".strip().lower()
                if combined:
                    keys.append(combined)
                    data.append((r.id, r.title_ru, r.title_en, r.year, r.kind, r.shikimori_id))

            matches = process.extract(
                query.lower().strip(), keys,
                scorer=fuzz.WRatio, limit=50, score_cutoff=65,
            )
            # m[2] is the index into data (stable even when multiple titles share the same string)
            return [data[m[2]] for m in matches]
        finally:
            session.close()


    try:
        results = await loop.run_in_executor(None, _search_by_title)
    except Exception as e:
        logger.exception("Error searching anime: %s", e)
        await update.effective_chat.send_message("Search failed. Check logs for details.")
        return

    # Fuzzy fallback: only triggered when AND-search returns nothing
    # and at least one query word is longer than 2 characters.
    fuzzy_used = False
    if not results and any(len(w) >= 3 for w in query.strip().split()):
        try:
            results = await loop.run_in_executor(None, _search_fuzzy)
            if results:
                fuzzy_used = True
                logger.info("Fuzzy search for %r: found %d results", query, len(results))
        except Exception as e:
            logger.exception("Error in fuzzy search: %s", e)

    if not results:
        await update.effective_chat.send_message(f"Nothing found for: {query!r}")
        return

    # Single result — publish immediately without showing buttons.
    if len(results) == 1:
        try:
            result = await loop.run_in_executor(
                None, lambda: _publish_anime_by_db_id(results[0][0], dry_run)
            )
            await update.effective_chat.send_message(result)
        except Exception as e:
            logger.exception("Error publishing single result: %s", e)
            await update.effective_chat.send_message("Publish failed. Check logs for details.")
        return

    # Multiple results — generate a session_id and send the keyboard in one message.
    total = len(results)
    suffix = " (first 50 shown)" if total == 50 else ""
    header = (
        f"🔎 Fuzzy search for {query!r}: {total}{suffix}"
        if fuzzy_used else
        f"🔍 Found: {total}{suffix}"
    )
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
        f"{header}. Choose an anime:",
        reply_markup=keyboard,
    )
    context.bot_data["anime_searches"][session_id]["message_id"] = sent.message_id


# ---------------------------------------------------------------------------
# Callback handlers (inline buttons for /post_anime)
# ---------------------------------------------------------------------------

async def _cb_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User picks an anime → publish → send result → delete the keyboard message."""
    query = update.callback_query
    if not _is_admin(update):
        await query.answer("Access denied.", show_alert=True)
        return
    if not query.message:
        await query.answer()
        return
    try:
        parts      = query.data.split(":")  # pick:{anime_id}:{session_id}
        anime_id   = int(parts[1])
        session_id = parts[2]
    except (IndexError, ValueError):
        await query.answer("Invalid button data", show_alert=True)
        return
    await query.answer()  # must be called exactly once on the success path
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
        logger.exception("Error publishing via pick button: %s", e)
        result_text = "Publish failed. Check logs for details."
    context.bot_data.get("anime_searches", {}).pop(session_id, None)
    await context.bot.send_message(chat_id, result_text)  # send result first
    try:
        await query.message.delete()                       # then remove the keyboard
    except Exception:
        pass


async def _cb_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pagination — updates the keyboard in-place without sending a new message."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(update):
        return
    if not query.message:
        return
    try:
        parts      = query.data.split(":")  # page:{page}:{session_id}
        page       = int(parts[1])
        session_id = parts[2]
    except (IndexError, ValueError):
        await query.answer("Invalid button data", show_alert=True)
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
        pass  # "Message is not modified" — harmless, ignore


async def _cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel — deletes the keyboard message, sends nothing."""
    query = update.callback_query
    await query.answer()
    if not query.message:
        return
    try:
        parts      = query.data.split(":")  # cancel:{session_id}
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
    """No-op button (page indicator label)."""
    await update.callback_query.answer()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in environment or settings")
        sys.exit(1)

    if not ADMIN_IDS:
        logger.warning(
            "⚠️  ADMIN_IDS is not set — ALL Telegram users have access to bot "
            "management commands (/post_now, /posting_on, etc.). "
            "Set ADMIN_IDS in .env to restrict access."
        )

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
                        "Telegram: network/timeout error, will retry (get_updates): %s",
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

            # Command handlers
            application.add_handler(CommandHandler("post_now", post_now))
            application.add_handler(CommandHandler("post_anime", post_anime))
            application.add_handler(CommandHandler("status", status))
            application.add_handler(CommandHandler("dry_run_on", dry_run_on))
            application.add_handler(CommandHandler("dry_run_off", dry_run_off))
            application.add_handler(CommandHandler("posting_on", posting_on))
            application.add_handler(CommandHandler("posting_off", posting_off))
            application.add_handler(CommandHandler("interval", set_interval))

            # Callback handlers for /post_anime inline buttons
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
        logger.exception("Fatal error in Telegram bot")
        sys.exit(1)


if __name__ == "__main__":
    main()
