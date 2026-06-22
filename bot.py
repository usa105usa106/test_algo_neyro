from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

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

from archive_builder import build_aplus_hunter_archive, build_logs_archive, build_scan_archive
from config import SCAN_PRESETS, SYMBOL_CANDIDATES, ScanPreset, Settings, load_settings
from file_utils import human_bytes, safe_rmtree, split_file
from logging_setup import setup_logging
from mexc import MexcSpotClient
from security import SecretStore

BTN_API = "api"
BTN_LOG_FULL = "log_full"
BTN_RESET = "reset"
BTN_PING = "ping"
BTN_SYMBOLS_CHECK = "symbols_check"
BTN_MONTAGE = "montage_toggle"
BTN_APLUS_HUNTER = "aplus_hunter_toggle"
BTN_SCAN_PREFIX = "scan:"


class BotRuntime:
    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.secret_store = SecretStore(settings.secrets_dir, settings.state_dir, settings.secret_encryption_key)
        self.active_task: asyncio.Task | None = None
        self.active_task_name: str | None = None
        self.awaiting_api_step: dict[int, dict[str, Any]] = {}
        self.last_export: Path | None = None
        self.started_at_monotonic = time.monotonic()
        self.started_at_utc = datetime.now(timezone.utc)
        self.montage_enabled = False
        self.aplus_hunter_enabled = False
        self.aplus_hunter_task: asyncio.Task | None = None
        self.aplus_hunter_busy = False
        self.aplus_status_message_id: int | None = None

    def is_admin(self, update: Update) -> bool:
        if self.settings.admin_telegram_id is None:
            return True
        user = update.effective_user
        return bool(user and user.id == self.settings.admin_telegram_id)

    def active_summary(self) -> str:
        if self.active_task and not self.active_task.done():
            return f"идёт задача: {self.active_task_name}"
        if self.aplus_hunter_busy:
            return "идёт A+ Hunter круг"
        if self.aplus_hunter_task and not self.aplus_hunter_task.done() and self.aplus_hunter_enabled:
            return "A+ Hunter включён, ожидание следующего круга"
        return "фоновых задач нет"

    def reset(self) -> None:
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
        if self.aplus_hunter_task and not self.aplus_hunter_task.done():
            self.aplus_hunter_task.cancel()
        self.active_task = None
        self.active_task_name = None
        self.aplus_hunter_enabled = False
        self.aplus_hunter_task = None
        self.aplus_hunter_busy = False
        self.aplus_status_message_id = None
        self.awaiting_api_step.clear()
        self.secret_store.clear()
        self.montage_enabled = False
        safe_rmtree(self.settings.work_dir)



_CUSTOM_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,24}$")

# User-friendly aliases for text-triggered scans. These are exact mappings, not fallbacks:
# writing "gold" must mean the same exact tradable contract as the Gold button.
_CUSTOM_SYMBOL_ALIASES = {
    "btc": "BTC_USDT",
    "bitcoin": "BTC_USDT",
    "eth": "ETH_USDT",
    "ethereum": "ETH_USDT",
    "xau": "XAU_USDT",
    "gold": "XAU_USDT",
    "xag": "SILVER_USDT",
    "silver": "SILVER_USDT",
    "oil": "USOIL_USDT",
    "wti": "USOIL_USDT",
    "usoil": "USOIL_USDT",
}

_CUSTOM_PRESET_KEYS = {
    "XAU_USDT": ("gold", "Gold 30d"),
    "BTC_USDT": ("btc", "BTC 30d"),
    "ETH_USDT": ("eth", "ETH 30d"),
    "SILVER_USDT": ("silver", "Silver 30d"),
    "USOIL_USDT": ("oil", "Oil 30d"),
}


def _normalize_custom_symbol(text: str) -> str | None:
    """Convert a short Telegram text like 'eth', 'gold', 'oil' or 'xrp' to an exact MEXC Futures symbol.

    Exact-only policy: aliases map only to the intended exact contract. There is no fallback
    or automatic substitution between different priced instruments such as XAU/XAUT or WTI/Brent.
    It does not accept sentences, spaces, URLs, or command-like text.
    """
    raw = (text or "").strip()
    if not raw or raw.startswith("/") or " " in raw or "\n" in raw:
        return None
    if not _CUSTOM_SYMBOL_RE.fullmatch(raw):
        return None

    alias_key = raw.lower().replace("-", "_").replace("_", "")
    if alias_key in _CUSTOM_SYMBOL_ALIASES:
        return _CUSTOM_SYMBOL_ALIASES[alias_key]

    symbol = raw.upper().replace("-", "_")
    if "_" not in symbol:
        if symbol.endswith("USDT") and len(symbol) > 4:
            symbol = symbol[:-4] + "_USDT"
        else:
            symbol = f"{symbol}_USDT"
    elif symbol.endswith("USDT") and not symbol.endswith("_USDT"):
        symbol = symbol[:-4].rstrip("_") + "_USDT"
    if not symbol.endswith("_USDT"):
        return None
    if len(symbol) > 32:
        return None
    return symbol


def _custom_preset_from_text(text: str) -> ScanPreset | None:
    symbol = _normalize_custom_symbol(text)
    if not symbol:
        return None
    if symbol in _CUSTOM_PRESET_KEYS:
        key, title = _CUSTOM_PRESET_KEYS[symbol]
        return ScanPreset(key, title, [symbol])
    base = symbol[:-5] if symbol.endswith("_USDT") else symbol
    key = base.lower().replace("_", "")
    title = f"{base} 30d"
    return ScanPreset(key, title, [symbol])

def main_menu(runtime: BotRuntime | None = None) -> InlineKeyboardMarkup:
    montage_label = f"🧩 Montage: {'ON' if runtime and runtime.montage_enabled else 'OFF'}"
    aplus_label = f"🎯 A+ Hunter: {'ON' if runtime and runtime.aplus_hunter_enabled else 'OFF'}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Gold 30d", callback_data=f"{BTN_SCAN_PREFIX}gold"),
            InlineKeyboardButton("₿ BTC 30d", callback_data=f"{BTN_SCAN_PREFIX}btc"),
        ],
        [
            InlineKeyboardButton("Ξ ETH 30d", callback_data=f"{BTN_SCAN_PREFIX}eth"),
            InlineKeyboardButton("🥈 Silver 30d", callback_data=f"{BTN_SCAN_PREFIX}silver"),
        ],
        [
            InlineKeyboardButton("🛢 Oil 30d", callback_data=f"{BTN_SCAN_PREFIX}oil"),
            InlineKeyboardButton("🔥 Multi 5 assets 30d", callback_data=f"{BTN_SCAN_PREFIX}multi"),
        ],
        [
            InlineKeyboardButton(montage_label, callback_data=BTN_MONTAGE),
            InlineKeyboardButton(aplus_label, callback_data=BTN_APLUS_HUNTER),
        ],
        [
            InlineKeyboardButton("⚙️ Symbols check", callback_data=BTN_SYMBOLS_CHECK),
        ],
        [
            InlineKeyboardButton("/api", callback_data=BTN_API),
            InlineKeyboardButton("/log_full", callback_data=BTN_LOG_FULL),
            InlineKeyboardButton("/ping", callback_data=BTN_PING),
            InlineKeyboardButton("/reset", callback_data=BTN_RESET),
        ],
    ])

async def reply_with_menu(update: Update, text: str, runtime: BotRuntime) -> None:
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, reply_markup=main_menu(runtime))
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=main_menu(runtime))


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
    api_text = f"API сохранён: {api_mask['api_key']}" if api_mask else "API не задан; свечи качаются через public MEXC Futures endpoints."
    await reply_with_menu(
        update,
        f"ChatGPT Scan Bot 30d — {runtime.settings.app_version}\n\n"
        "Стандартный режим: как v17 — 5 графиков + task/setup_format + parquet.\n"
        "Montage OFF по умолчанию. Montage ON: один montage-график на актив без parquet + swing task LONG/SHORT.\n\n"
        "Старые тяжёлые research-кнопки убраны.\n"
        "Служебные: /api, /log_full, /ping, /reset.\n\n"
        f"{api_text}\n"
        f"Montage: {'ON' if runtime.montage_enabled else 'OFF'}\n"
        "В коде нет place_order/cancel_order, бот не открывает сделки.",
        runtime,
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    exports = sorted(runtime.settings.exports_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    api_mask = runtime.secret_store.load_mexc_api_mask()
    lines = [
        f"Status ({runtime.settings.app_version}):",
        f"- {runtime.active_summary()}",
        f"- market_type: {runtime.settings.mexc_market_type}",
        f"- base_url: {runtime.settings.mexc_base_url}",
        f"- days_back: {runtime.settings.days_back}",
        f"- base_interval: {runtime.settings.base_interval}",
        f"- montage_mode: {'ON' if runtime.montage_enabled else 'OFF'}",
        f"- aplus_hunter: {'ON' if runtime.aplus_hunter_enabled else 'OFF'}",
        f"- data_root: {runtime.settings.data_root}",
        f"- API: {api_mask['api_key'] if api_mask else 'not set'}",
        "- last exports:",
    ]
    if exports:
        for p in exports:
            lines.append(f"  • {p.name} — {human_bytes(p.stat().st_size)}")
    else:
        lines.append("  • нет")
    await reply_with_menu(update, "\n".join(lines), runtime)


def _format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}д {hours}ч {minutes}м {secs}с"
    if hours:
        return f"{hours}ч {minutes}м {secs}с"
    if minutes:
        return f"{minutes}м {secs}с"
    return f"{secs}с"


async def handle_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    started = time.perf_counter()
    process = psutil.Process(os.getpid())
    process_cpu = process.cpu_percent(interval=None)
    proc_mem = process.memory_info().rss
    system_mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(runtime.settings.data_root))
    uptime = _format_duration(time.monotonic() - runtime.started_at_monotonic)
    response_ms = (time.perf_counter() - started) * 1000
    text = (
        f"Ping: OK\n"
        f"- version: {runtime.settings.app_version}\n"
        f"- response: {response_ms:.1f} ms\n"
        f"- uptime: {uptime}\n"
        f"- started UTC: {runtime.started_at_utc.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- task: {runtime.active_summary()}\n"
        f"- MEXC base_url: {runtime.settings.mexc_base_url}\n"
        f"- process RAM: {human_bytes(proc_mem)}\n"
        f"- system RAM: {system_mem.percent:.1f}% used ({human_bytes(system_mem.used)} / {human_bytes(system_mem.total)})\n"
        f"- process CPU: {process_cpu:.1f}%\n"
        f"- disk storage: {disk.percent:.1f}% used ({human_bytes(disk.used)} / {human_bytes(disk.total)})"
    )
    await reply_with_menu(update, text, runtime)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == BTN_MONTAGE:
        runtime.montage_enabled = not runtime.montage_enabled
        await query.message.reply_text(f"Montage переключен: {'ON' if runtime.montage_enabled else 'OFF'}", reply_markup=main_menu(runtime))
    elif data == BTN_APLUS_HUNTER:
        await toggle_aplus_hunter(update, context)
    elif data.startswith(BTN_SCAN_PREFIX):
        key = data.removeprefix(BTN_SCAN_PREFIX)
        preset = SCAN_PRESETS.get(key)
        if not preset:
            await query.message.reply_text("Неизвестная scan-кнопка.", reply_markup=main_menu(runtime))
            return
        await start_scan_job(update, context, preset)
    elif data == BTN_SYMBOLS_CHECK:
        await handle_symbols_check(update, context)
    elif data == BTN_API:
        await start_api_flow(update, context)
    elif data == BTN_LOG_FULL:
        await handle_log_full(update, context)
    elif data == BTN_RESET:
        await handle_reset(update, context)
    elif data == BTN_PING:
        await handle_ping(update, context)


async def start_api_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    user_id = update.effective_user.id
    runtime.awaiting_api_step[user_id] = {"step": "api_key"}
    await reply_with_menu(
        update,
        "Отправь MEXC API KEY одним сообщением, если хочешь сохранить его для meta/status.\n\n"
        "Для свечей ключ не нужен: используются public MEXC Futures endpoints.\n"
        "Ключ лучше read-only, без trade/withdraw permissions.\n"
        "Напиши /cancel, чтобы отменить.",
        runtime,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if update.effective_user:
        runtime.awaiting_api_step.pop(update.effective_user.id, None)
    await reply_with_menu(update, "Ок, отменено.", runtime)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    user = update.effective_user
    if not user:
        return
    state = runtime.awaiting_api_step.get(user.id)
    text = (update.effective_message.text or "").strip()
    if not state:
        preset = _custom_preset_from_text(text)
        if preset:
            await start_scan_job(update, context, preset)
            return
        await reply_with_menu(
            update,
            "Выбери действие кнопкой, отправь /start, или напиши symbol для кастомного архива, например: xrp / sol / XRP_USDT.",
            runtime,
        )
        return

    if text.lower() in {"/cancel", "cancel", "отмена"}:
        runtime.awaiting_api_step.pop(user.id, None)
        await reply_with_menu(update, "Ок, отменено.", runtime)
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
        runtime.logger.info("API saved via Telegram, key mask=%s", mask.get("api_key"))
        await reply_with_menu(
            update,
            f"API сохранён зашифрованно. Key: {mask['api_key']}\n"
            "Бот всё равно не умеет открывать сделки: торговых endpoints в коде нет.",
            runtime,
        )


async def start_scan_job(update: Update, context: ContextTypes.DEFAULT_TYPE, preset: ScanPreset) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if runtime.active_task and not runtime.active_task.done():
        await reply_with_menu(update, f"Уже {runtime.active_summary()}. Дождись окончания или нажми /reset.", runtime)
        return
    if runtime.aplus_hunter_busy:
        await reply_with_menu(update, "Сейчас идёт A+ Hunter круг. Дождись завершения или выключи A+ Hunter.", runtime)
        return
    chat_id = update.effective_chat.id
    montage_mode = runtime.montage_enabled
    runtime.active_task_name = f"Scan {preset.title}"
    runtime.active_task = asyncio.create_task(build_scan_job(context, chat_id, preset, montage_mode))
    await reply_with_menu(update, f"Scan {preset.title}: запущено. Режим={'montage' if montage_mode else 'standard/v17'}. Собираю exact symbol, 1m за 30 дней и графики.", runtime)


async def build_scan_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, preset: ScanPreset, montage_mode: bool) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    try:
        async def progress(msg: str) -> None:
            runtime.logger.info(msg)
            await context.bot.send_message(chat_id=chat_id, text=msg[:3900])

        zip_path = await build_scan_archive(runtime.settings, runtime.logger, runtime.secret_store, preset, progress, montage_mode=montage_mode)
        runtime.last_export = zip_path
        await send_archive_or_parts(context, chat_id, zip_path, runtime)
    except asyncio.CancelledError:
        runtime.logger.warning("Scan job cancelled")
        await context.bot.send_message(chat_id=chat_id, text="Scan: задача отменена /reset.")
    except Exception as exc:  # noqa: BLE001
        runtime.logger.exception("Scan job failed: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text=f"Scan: ошибка: {exc}\nНажми /log_full, чтобы забрать полный лог.")
    finally:
        runtime.active_task = None
        runtime.active_task_name = None


def _format_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


async def _replace_aplus_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int, runtime: BotRuntime, text: str) -> None:
    if runtime.aplus_status_message_id is not None:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=runtime.aplus_status_message_id)
        except Exception as exc:  # noqa: BLE001
            runtime.logger.debug("Could not delete A+ Hunter status message: %s", exc)
    msg = await context.bot.send_message(chat_id=chat_id, text=text[:3900], reply_markup=main_menu(runtime))
    runtime.aplus_status_message_id = msg.message_id


async def _edit_aplus_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int, runtime: BotRuntime, text: str) -> None:
    if runtime.aplus_status_message_id is None:
        await _replace_aplus_status(context, chat_id, runtime, text)
        return
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=runtime.aplus_status_message_id, text=text[:3900], reply_markup=main_menu(runtime))
    except Exception as exc:  # noqa: BLE001
        runtime.logger.debug("Could not edit A+ Hunter status message: %s", exc)


async def _aplus_countdown(context: ContextTypes.DEFAULT_TYPE, chat_id: int, runtime: BotRuntime, base_text: str, seconds: int = 300) -> None:
    remaining = int(seconds)
    while remaining > 0 and runtime.aplus_hunter_enabled:
        await _edit_aplus_status(context, chat_id, runtime, f"{base_text}\n\n⏳ Следующий scan через: {_format_mmss(remaining)}")
        sleep_for = 15 if remaining > 20 else 5
        await asyncio.sleep(min(sleep_for, remaining))
        remaining -= sleep_for


async def send_aplus_archive_only(context: ContextTypes.DEFAULT_TYPE, chat_id: int, zip_path: Path, runtime: BotRuntime) -> None:
    limit_bytes = runtime.settings.telegram_send_limit_mb * 1024 * 1024
    size = zip_path.stat().st_size
    if size > limit_bytes:
        await send_archive_or_parts(context, chat_id, zip_path, runtime)
        return
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    with zip_path.open("rb") as f:
        await context.bot.send_document(
            chat_id=chat_id,
            document=f,
            filename=zip_path.name,
            caption=f"🎯 A+ Hunter archive: {zip_path.name}\nРазмер: {human_bytes(size)}",
        )


async def toggle_aplus_hunter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    query = update.callback_query
    chat_id = update.effective_chat.id
    if runtime.aplus_hunter_enabled:
        runtime.aplus_hunter_enabled = False
        await _replace_aplus_status(
            context,
            chat_id,
            runtime,
            "🛑 A+ Hunter: OFF\n\nНовые сканы остановлены. Если текущий montage уже строится, он завершится, но следующий круг не запустится.",
        )
        return

    runtime.aplus_hunter_enabled = True
    await _replace_aplus_status(context, chat_id, runtime, "🎯 A+ Hunter: ON\n\nЗапускаю первый top-200 + forced scan.")
    if not runtime.aplus_hunter_task or runtime.aplus_hunter_task.done():
        runtime.aplus_hunter_task = asyncio.create_task(aplus_hunter_loop(context, chat_id))


async def aplus_hunter_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    try:
        while runtime.aplus_hunter_enabled:
            if runtime.active_task and not runtime.active_task.done():
                await _replace_aplus_status(
                    context,
                    chat_id,
                    runtime,
                    f"🎯 A+ Hunter: ON\n\nПауза: сейчас {runtime.active_summary()}.\nA+ Hunter продолжит после освобождения бота.",
                )
                await _aplus_countdown(context, chat_id, runtime, "🎯 A+ Hunter: ожидание свободного слота", 60)
                continue

            runtime.aplus_hunter_busy = True
            await _replace_aplus_status(
                context,
                chat_id,
                runtime,
                "🔎 A+ Hunter scan...\n\nTop-200 + forced symbols\nСтатус: ищу A+ candidates",
            )

            async def progress(msg: str) -> None:
                runtime.logger.info(msg)
                await _edit_aplus_status(context, chat_id, runtime, f"🔎 A+ Hunter scan...\n\n{msg}")

            zip_path: Path | None = None
            try:
                zip_path = await build_aplus_hunter_archive(runtime.settings, runtime.logger, runtime.secret_store, progress)
                runtime.last_export = zip_path or runtime.last_export
            except Exception as exc:  # noqa: BLE001
                runtime.logger.exception("A+ Hunter failed: %s", exc)
                runtime.aplus_hunter_busy = False
                await _replace_aplus_status(
                    context,
                    chat_id,
                    runtime,
                    f"⚠️ A+ Hunter error.\n\nПричина: {exc}\n\nПовтор будет после паузы, если A+ Hunter ON.",
                )
                await _aplus_countdown(context, chat_id, runtime, "⚠️ A+ Hunter error. Жду перед повтором.", 300)
                continue
            finally:
                runtime.aplus_hunter_busy = False

            if not runtime.aplus_hunter_enabled:
                break

            if zip_path is None:
                base = "✅ Scan завершён.\n\nA+ candidates: 0\nЛучше подождать."
                await _replace_aplus_status(context, chat_id, runtime, base)
                await _aplus_countdown(context, chat_id, runtime, base, 300)
                continue

            await _replace_aplus_status(
                context,
                chat_id,
                runtime,
                f"✅ A+ Hunter archive готов.\n\nФайл: {zip_path.name}\nОтправляю архив в чат.",
            )
            await send_aplus_archive_only(context, chat_id, zip_path, runtime)
            if not runtime.aplus_hunter_enabled:
                break
            base = (
                "✅ Архив отправлен.\n\n"
                "Закинь этот архив в ChatGPT.\n"
                "Если A+ подтвердится — получишь setup.\n"
                "Если нет — A+ нет, лучше ещё подождать."
            )
            await _replace_aplus_status(context, chat_id, runtime, base)
            await _aplus_countdown(context, chat_id, runtime, base, 300)
    except asyncio.CancelledError:
        runtime.logger.warning("A+ Hunter loop cancelled")
    finally:
        runtime.aplus_hunter_busy = False
        if not runtime.aplus_hunter_enabled:
            runtime.aplus_hunter_task = None



async def handle_symbols_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    client = MexcSpotClient(runtime.settings.mexc_base_url, runtime.logger, runtime.settings.mexc_market_type)
    try:
        lines = ["Symbols check MEXC Futures exact symbols only:"]
        for asset, candidates in SYMBOL_CANDIDATES.items():
            info = await client.exchange_info(candidates)
            found = []
            for item in info.get("symbols", []):
                symbol = item.get("symbol") or item.get("requestedSymbol")
                warning = item.get("warning")
                if symbol and not warning:
                    found.append(symbol)
            if found:
                lines.append(f"- {asset}: {', '.join(found)}")
            else:
                lines.append(f"- {asset}: НЕ НАЙДЕН exact symbol: {', '.join(candidates)}")
        await reply_with_menu(update, "\n".join(lines), runtime)
    except Exception as exc:  # noqa: BLE001
        runtime.logger.exception("Symbols check failed: %s", exc)
        await reply_with_menu(update, f"Symbols check ошибка: {exc}", runtime)
    finally:
        await client.close()


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    runtime.reset()
    await reply_with_menu(update, "Reset выполнен: фоновые задачи остановлены, runtime/API state очищен, temp work очищен. Архивы exports и logs сохранены.", runtime)


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
    if not await guarded(update, runtime):
        return
    try:
        zip_path = build_logs_archive(runtime.settings, runtime.logger)
        await reply_with_menu(update, f"Log_full готов: {zip_path.name}, размер={human_bytes(zip_path.stat().st_size)}", runtime)
        await send_archive_or_parts(context, update.effective_chat.id, zip_path, runtime)
    except Exception as exc:  # noqa: BLE001
        runtime.logger.exception("Log_full failed: %s", exc)
        await reply_with_menu(update, f"Log_full ошибка: {exc}", runtime)


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
    application.add_handler(CommandHandler("api", start_api_flow))
    application.add_handler(CommandHandler("log_full", handle_log_full))
    application.add_handler(CommandHandler("ping", handle_ping))
    application.add_handler(CommandHandler("reset", handle_reset))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("Bot started. Version: %s. Data root: %s", settings.app_version, settings.data_root)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
