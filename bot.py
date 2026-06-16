from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from archive_builder import build_charts_archive, build_data_archive, build_logs_archive
from config import Settings, load_settings
from file_utils import split_file, human_bytes, safe_rmtree
from logging_setup import setup_logging
from security import SecretStore

BTN_API = "api"
BTN_PARQUET = "parquet"
BTN_CHARTS = "charts"
BTN_LOG_FULL = "log_full"
BTN_RESET = "reset"
BTN_STATUS = "status"


class BotRuntime:
    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.secret_store = SecretStore(settings.secrets_dir, settings.state_dir, settings.secret_encryption_key)
        self.active_task: asyncio.Task | None = None
        self.active_task_name: str | None = None
        self.awaiting_api_step: dict[int, dict[str, Any]] = {}
        self.last_export: Path | None = None

    def is_admin(self, update: Update) -> bool:
        if self.settings.admin_telegram_id is None:
            return True
        user = update.effective_user
        return bool(user and user.id == self.settings.admin_telegram_id)

    def active_summary(self) -> str:
        if self.active_task and not self.active_task.done():
            return f"идёт задача: {self.active_task_name}"
        return "фоновых задач нет"

    def reset(self) -> None:
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
        self.active_task = None
        self.active_task_name = None
        self.awaiting_api_step.clear()
        self.secret_store.clear()
        safe_rmtree(self.settings.work_dir)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Api", callback_data=BTN_API), InlineKeyboardButton("Parquet", callback_data=BTN_PARQUET)],
        [InlineKeyboardButton("Charts", callback_data=BTN_CHARTS), InlineKeyboardButton("Log_full", callback_data=BTN_LOG_FULL)],
        [InlineKeyboardButton("Status", callback_data=BTN_STATUS), InlineKeyboardButton("Reset", callback_data=BTN_RESET)],
    ])


async def send_menu(update: Update, text: str) -> None:
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=main_menu())
    else:
        await update.effective_message.reply_text(text, reply_markup=main_menu())


async def guarded(update: Update, runtime: BotRuntime) -> bool:
    if not runtime.is_admin(update):
        runtime.logger.warning("Unauthorized user tried access: %s", update.effective_user)
        if update.effective_message:
            await update.effective_message.reply_text("Доступ запрещён.")
        elif update.callback_query:
            await update.callback_query.answer("Доступ запрещён", show_alert=True)
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    api_mask = runtime.secret_store.load_mexc_api_mask()
    api_text = f"API сохранён: {api_mask['api_key']}" if api_mask else "API не задан; market data всё равно качается через public MEXC endpoints."
    await update.effective_message.reply_text(
        "MEXC BTC/ETH Research Collector\n\n"
        "Кнопки:\n"
        "Api — сохранить read-only MEXC API key/secret в зашифрованном виде.\n"
        "Parquet — создать архив со свечами BTC/ETH 1m за 365 дней + meta. По умолчанию MEXC futures.\n"
        "Charts — создать архив с читаемыми графиками из Parquet.\n"
        "Log_full — отправить полный лог и индекс архивов.\n"
        "Reset — остановить фоновые задачи и очистить runtime/API state.\n\n"
        f"{api_text}\n"
        "Важно: в коде нет place_order/cancel_order, бот не умеет открывать сделки.",
        reply_markup=main_menu(),
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    exports = sorted(runtime.settings.exports_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    api_mask = runtime.secret_store.load_mexc_api_mask()
    lines = [
        "Status:",
        f"- {runtime.active_summary()}",
        f"- symbols: {', '.join(runtime.settings.symbols)}",
        f"- market_type: {runtime.settings.mexc_market_type}",
        f"- days_back: {runtime.settings.days_back}",
        f"- data_root: {runtime.settings.data_root}",
        f"- API: {api_mask['api_key'] if api_mask else 'not set'}",
        "- last exports:",
    ]
    if exports:
        for p in exports:
            lines.append(f"  • {p.name} — {human_bytes(p.stat().st_size)}")
    else:
        lines.append("  • нет")
    await update.effective_message.reply_text("\n".join(lines), reply_markup=main_menu())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == BTN_API:
        await start_api_flow(update, context)
    elif data == BTN_PARQUET:
        await start_background_job(update, context, "Parquet", build_data_job)
    elif data == BTN_CHARTS:
        await start_background_job(update, context, "Charts", build_charts_job)
    elif data == BTN_LOG_FULL:
        await handle_log_full(update, context)
    elif data == BTN_RESET:
        runtime.reset()
        await query.message.reply_text("Reset выполнен: фоновые задачи остановлены, runtime/API state очищен, temp work очищен. Архивы exports и logs сохранены.", reply_markup=main_menu())
    elif data == BTN_STATUS:
        # Reuse status logic, but callback_query has no effective_message command; it still has message.
        await status(update, context)


async def start_api_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    user_id = update.effective_user.id
    runtime.awaiting_api_step[user_id] = {"step": "api_key"}
    await update.callback_query.message.reply_text(
        "Отправь MEXC API KEY одним сообщением.\n\n"
        "Рекомендация: создай ключ только для чтения, без trade/withdraw permissions.\n"
        "Для свечей ключ не обязателен, но я сохраню его в encrypted storage для meta/status.\n"
        "Напиши /cancel, чтобы отменить."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if update.effective_user:
        runtime.awaiting_api_step.pop(update.effective_user.id, None)
    await update.effective_message.reply_text("Ок, отменено.", reply_markup=main_menu())


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    user = update.effective_user
    if not user:
        return
    state = runtime.awaiting_api_step.get(user.id)
    if not state:
        await update.effective_message.reply_text("Выбери действие кнопкой.", reply_markup=main_menu())
        return

    text = (update.effective_message.text or "").strip()
    if text.lower() in {"/cancel", "cancel", "отмена"}:
        runtime.awaiting_api_step.pop(user.id, None)
        await update.effective_message.reply_text("Ок, отменено.", reply_markup=main_menu())
        return

    if state["step"] == "api_key":
        state["api_key"] = text
        state["step"] = "api_secret"
        await update.effective_message.reply_text("Теперь отправь MEXC API SECRET одним сообщением.")
        return

    if state["step"] == "api_secret":
        api_key = state.get("api_key", "")
        api_secret = text
        mask = runtime.secret_store.save_mexc_api(api_key, api_secret)
        runtime.awaiting_api_step.pop(user.id, None)
        runtime.logger.info("MEXC API saved via Telegram, key mask=%s", mask.get("api_key"))
        await update.effective_message.reply_text(
            f"API сохранён зашифрованно. Key: {mask['api_key']}\n"
            "Бот всё равно не умеет открывать сделки: торговых endpoints в коде нет.",
            reply_markup=main_menu(),
        )


async def start_background_job(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str, coro_factory) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if runtime.active_task and not runtime.active_task.done():
        await update.callback_query.message.reply_text(f"Уже {runtime.active_summary()}. Дождись окончания или нажми Reset.")
        return
    chat_id = update.effective_chat.id
    runtime.active_task_name = name
    runtime.active_task = asyncio.create_task(coro_factory(context, chat_id))
    await update.callback_query.message.reply_text(f"{name}: задача запущена в фоне. Подробности пишутся в logs/full.log", reply_markup=main_menu())


async def build_data_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    try:
        async def progress(msg: str) -> None:
            runtime.logger.info(msg)
            # Do not spam too much; only important milestones are passed here.
            await context.bot.send_message(chat_id=chat_id, text=msg[:3900])

        zip_path = await build_data_archive(runtime.settings, runtime.logger, runtime.secret_store, progress)
        runtime.last_export = zip_path
        await send_archive_or_parts(context, chat_id, zip_path, runtime)
    except asyncio.CancelledError:
        runtime.logger.warning("Parquet job cancelled")
        await context.bot.send_message(chat_id=chat_id, text="Parquet: задача отменена Reset.")
    except Exception as exc:  # noqa: BLE001
        runtime.logger.exception("Parquet job failed: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text=f"Parquet: ошибка: {exc}\nНажми Log_full, чтобы забрать полный лог.")
    finally:
        runtime.active_task = None
        runtime.active_task_name = None


async def build_charts_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    try:
        async def progress(msg: str) -> None:
            runtime.logger.info(msg)
            await context.bot.send_message(chat_id=chat_id, text=msg[:3900])

        zip_path = await build_charts_archive(runtime.settings, runtime.logger, progress)
        runtime.last_export = zip_path
        await send_archive_or_parts(context, chat_id, zip_path, runtime)
    except asyncio.CancelledError:
        runtime.logger.warning("Charts job cancelled")
        await context.bot.send_message(chat_id=chat_id, text="Charts: задача отменена Reset.")
    except Exception as exc:  # noqa: BLE001
        runtime.logger.exception("Charts job failed: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text=f"Charts: ошибка: {exc}\nСкорее всего сначала нужно нажать Parquet. Нажми Log_full для полного лога.")
    finally:
        runtime.active_task = None
        runtime.active_task_name = None


async def send_archive_or_parts(context: ContextTypes.DEFAULT_TYPE, chat_id: int, zip_path: Path, runtime: BotRuntime) -> None:
    limit_bytes = runtime.settings.telegram_send_limit_mb * 1024 * 1024
    size = zip_path.stat().st_size
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"Архив создан: {zip_path.name}\n"
            f"Размер: {human_bytes(size)}\n"
            f"Путь на сервере: {zip_path}\n"
            f"Telegram send limit в боте: {runtime.settings.telegram_send_limit_mb} MB"
        ),
    )
    parts = split_file(zip_path, limit_bytes)
    if len(parts) == 1 and parts[0] == zip_path:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        with zip_path.open("rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f, filename=zip_path.name)
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"Архив больше лимита Telegram Bot API, отправляю частями: {len([p for p in parts if '.part' in p.name])} part-файлов.\n"
            "После скачивания склей части по README_REASSEMBLE. Если есть прямой доступ к серверу, лучше скачать оригинальный .zip по указанному пути."
        ),
    )
    for part in parts:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        with part.open("rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f, filename=part.name)
        await asyncio.sleep(0.2)


async def handle_log_full(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    try:
        zip_path = build_logs_archive(runtime.settings, runtime.logger)
        await update.callback_query.message.reply_text(f"Log_full готов: {zip_path.name}, размер={human_bytes(zip_path.stat().st_size)}")
        await send_archive_or_parts(context, update.effective_chat.id, zip_path, runtime)
    except Exception as exc:  # noqa: BLE001
        runtime.logger.exception("Log_full failed: %s", exc)
        await update.callback_query.message.reply_text(f"Log_full ошибка: {exc}")


def main() -> None:
    settings = load_settings()
    logger = setup_logging(settings.logs_dir)
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty. Edit .env first.")
    if settings.admin_telegram_id is None:
        logger.warning("ADMIN_TELEGRAM_ID is empty/invalid. Bot will allow all users who know the token/chat. Set ADMIN_TELEGRAM_ID.")

    runtime = BotRuntime(settings, logger)
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["runtime"] = runtime

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("Bot started. Data root: %s", settings.data_root)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
