from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import zipfile
from datetime import datetime, timezone, timedelta
from dataclasses import replace
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

from archive_builder import build_aplus_hunter_archive, build_logs_archive, build_scan_archive, build_stress_test_archive
from intraday_archive import build_intraday_candidates_archive
from intraday_engine import IntradayReport, analyze_intraday_symbol
from config import SCAN_PRESETS, SYMBOL_CANDIDATES, ScanPreset, Settings, load_settings
from file_utils import human_bytes, safe_rmtree, split_file
from logging_setup import setup_logging
from mexc import DownloadWindow, INTERVAL_MS, MexcSpotClient
from security import SecretStore

BTN_API = "api"
BTN_LOG_FULL = "log_full"
BTN_RESET = "reset"
BTN_PING = "ping"
BTN_SYMBOLS_CHECK = "symbols_check"
BTN_MONTAGE = "montage_toggle"
BTN_APLUS_HUNTER = "aplus_hunter_toggle"
BTN_INTRADAY = "intraday_toggle"
BTN_STRESS_TEST = "stress_test"
BTN_SCAN_PREFIX = "scan:"
APLUS_SYMBOL_COOLDOWN_SEC = 45 * 60
INTRADAY_DEFAULT_SYMBOLS = ["BTC_USDT", "ETH_USDT", "XAU_USDT", "SILVER_USDT", "USOIL_USDT"]


_INTRADAY_DECISION_ORDER = {
    "MANUAL_REVIEW": 0,
    "WAIT_CONFIRMATION": 1,
    "WAIT_PULLBACK": 2,
    "WAIT_SWEEP_CONFIRMATION": 3,
    "WAIT_EDGE": 4,
    "WAIT": 5,
    "NO_TRADE": 6,
}


def _intraday_sort_key(report: Any) -> tuple[int, int, str]:
    """Sort strongest Intraday candidates first.

    Green MANUAL_REVIEW reports are ordered by quality_score descending, then symbol.
    Non-green reports keep decision priority and also use score as a secondary key.
    This order is used both in the Telegram status and in the archive candidate list.
    """
    decision_order = _INTRADAY_DECISION_ORDER.get(getattr(report, "decision", ""), 9)
    quality = int(getattr(report, "quality_score", 0) or 0)
    symbol = str(getattr(report, "symbol", ""))
    return (decision_order, -quality, symbol)


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
        self.aplus_symbol_cooldown_until: dict[str, float] = {}
        self.intraday_enabled = False
        self.intraday_task: asyncio.Task | None = None
        self.intraday_busy = False
        self.intraday_status_message_id: int | None = None
        self.intraday_last_status_text: str | None = None
        self.intraday_last_signature: str | None = None
        self.intraday_last_archive_sent_at: float = 0.0
        self.intraday_symbols: list[str] = list(INTRADAY_DEFAULT_SYMBOLS)
        self.intraday_regime_state: dict[str, dict[str, Any]] = {}

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
        if self.intraday_busy:
            return "идёт Intraday scan"
        if self.intraday_task and not self.intraday_task.done() and self.intraday_enabled:
            return "Intraday включён, ожидание следующего круга"
        return "фоновых задач нет"

    def reset(self) -> None:
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
        if self.aplus_hunter_task and not self.aplus_hunter_task.done():
            self.aplus_hunter_task.cancel()
        if self.intraday_task and not self.intraday_task.done():
            self.intraday_task.cancel()
        self.active_task = None
        self.active_task_name = None
        self.aplus_hunter_enabled = False
        self.aplus_hunter_task = None
        self.aplus_hunter_busy = False
        self.aplus_status_message_id = None
        self.aplus_symbol_cooldown_until.clear()
        self.intraday_enabled = False
        self.intraday_task = None
        self.intraday_busy = False
        self.intraday_status_message_id = None
        self.intraday_last_status_text = None
        self.intraday_last_signature = None
        self.intraday_last_archive_sent_at = 0.0
        self.intraday_symbols = list(INTRADAY_DEFAULT_SYMBOLS)
        self.intraday_regime_state.clear()
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
    intraday_label = f"📊 Intraday: {'ON' if runtime and runtime.intraday_enabled else 'OFF'}"
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
            InlineKeyboardButton(intraday_label, callback_data=BTN_INTRADAY),
        ],
        [
            InlineKeyboardButton("🧪 Stress Test", callback_data=BTN_STRESS_TEST),
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
        "Служебные: /help, /api, /log_full, /ping, /reset.\n\n"
        f"{api_text}\n"
        f"Montage: {'ON' if runtime.montage_enabled else 'OFF'}\n"
        f"Intraday: {'ON' if runtime.intraday_enabled else 'OFF'} — {_symbols_short_list(runtime.intraday_symbols)}\n"
        "Команды Intraday-списка: int pol, xrp, sol / int del.\n"
        "В коде нет place_order/cancel_order, бот не открывает сделки.",
        runtime,
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if not await guarded(update, runtime):
        return
    text = (
        f"ChatGPT Scan Bot — {runtime.settings.app_version}\n\n"
        "Основные кнопки:\n"
        "• Gold/BTC/ETH/Silver/Oil 30d — старые одиночные архивы.\n"
        "• Multi 5 assets 30d — старый общий 30d архив.\n"
        "• Montage ON/OFF — старый montage-режим.\n"
        "• A+ Hunter ON/OFF — старый top-200 hunter с таймером.\n"
        "• Intraday ON/OFF — новый внутридневной режим, старые task-файлы не трогает.\n"
        "• Stress Test — Binance Spot parquet-only архив: SOL/ADA/XRP 3y, XAUT 4m / вся доступная история, 3 потока, без task-файлов.\n\n"
        "Intraday:\n"
        "• Скан каждые 5 минут после окончания предыдущего скана/архива.\n"
        "• Данные Intraday: свежая загрузка 30 дней, без parquet/cache.\n"
        "• Скоростной профиль MEXC как у A+ Hunter: последовательные запросы с throttle 0.35s.\n"
        "• Во время скана показывается короткий прогресс: Intraday scan - 10% / 20% / 90% / 100%.\n"
        "• После скана прогресс удаляется, вместо него приходит полный статус.\n"
        "• Если есть кандидаты, после 100% идут этапы архива: 1/3 archive → 2/3 archive → 3/3 archive. Ok.\n"
        "• Финальный статус идёт первым, архив отправляется ниже отдельным файлом.\n"
        "• Архив создаётся только для 🟢 MANUAL_REVIEW. Если зелёных несколько — один общий архив.\n"
        "• По умолчанию: BTC, ETH, XAU, SILVER, USOIL.\n\n"
        "Команды Intraday-списка:\n"
        "• int pol, xrp, sol — заменить список монет для Intraday.\n"
        "• int pol, int xrp, int sol — то же самое, можно писать с повтором int.\n"
        "• int del — вернуть список по умолчанию: BTC, ETH, XAU, SILVER, USOIL.\n\n"
        "Служебные команды:\n"
        "• /api — сохранить MEXC API key/secret для meta/status.\n"
        "• /log_full — скачать полный лог процесса.\n"
        "• /ping — версия, uptime, RAM/CPU/disk.\n"
        "• /reset — остановить фоновые задачи и очистить temp-state.\n"
        "• /status — debug-статус.\n\n"
        "Торговых endpoints нет: бот не открывает и не закрывает сделки."
    )
    await reply_with_menu(update, text, runtime)


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
        f"- intraday: {'ON' if runtime.intraday_enabled else 'OFF'}",
        f"- intraday_symbols: {', '.join(runtime.intraday_symbols)}",
        f"- intraday_scan_interval_sec: {runtime.settings.intraday_scan_interval_sec}",
        f"- intraday_days_back: {runtime.settings.intraday_days_back}",
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
    elif data == BTN_INTRADAY:
        await toggle_intraday(update, context)
    elif data == BTN_STRESS_TEST:
        await start_stress_test_job(update, context)
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
        int_cmd = _parse_intraday_symbols_command(text)
        if int_cmd is not None:
            action, symbols, error = int_cmd
            if error:
                await reply_with_menu(update, f"Intraday symbols: ошибка. {error}", runtime)
                return
            runtime.intraday_symbols = symbols
            runtime.intraday_last_signature = None
            runtime.intraday_last_archive_sent_at = 0.0
            runtime.intraday_regime_state.clear()
            runtime.logger.info("Intraday symbols command action=%s symbols=%s", action, symbols)
            if action == "reset":
                await reply_with_menu(update, f"Intraday список сброшен по умолчанию: {_symbols_short_list(runtime.intraday_symbols)}", runtime)
            else:
                await reply_with_menu(update, f"Intraday список заменён: {_symbols_short_list(runtime.intraday_symbols)}", runtime)
            return

        preset = _custom_preset_from_text(text)
        if preset:
            await start_scan_job(update, context, preset)
            return
        await reply_with_menu(
            update,
            "Выбери действие кнопкой, отправь /start, /help или напиши symbol для кастомного архива, например: xrp / sol / XRP_USDT. Для Intraday списка: int pol, xrp, sol или int del.",
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



async def start_stress_test_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    if runtime.active_task and not runtime.active_task.done():
        await reply_with_menu(update, f"Уже {runtime.active_summary()}. Дождись окончания или нажми /reset.", runtime)
        return
    if runtime.aplus_hunter_busy:
        await reply_with_menu(update, "Сейчас идёт A+ Hunter круг. Дождись завершения или выключи A+ Hunter.", runtime)
        return
    if runtime.intraday_busy:
        await reply_with_menu(update, "Сейчас идёт Intraday scan. Дождись завершения или выключи Intraday.", runtime)
        return
    chat_id = update.effective_chat.id
    runtime.active_task_name = "Stress Test"
    runtime.active_task = asyncio.create_task(build_stress_test_job(context, chat_id))
    await reply_with_menu(
        update,
        "Stress Test: запущено. Собираю Binance Spot 1m parquet: SOL/ADA/XRP за 3 года, XAUT за 4 месяца или всю доступную историю, если меньше. Работаю в 3 потока, старые режимы и task-файлы не трогаю. Подробные ошибки будут в /log_full.",
        runtime,
    )


async def build_stress_test_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    try:
        async def progress(msg: str) -> None:
            runtime.logger.info(msg)
            await context.bot.send_message(chat_id=chat_id, text=msg[:3900])

        zip_path = await build_stress_test_archive(runtime.settings, runtime.logger, progress)
        runtime.last_export = zip_path
        await send_archive_or_parts(context, chat_id, zip_path, runtime)
    except asyncio.CancelledError:
        runtime.logger.warning("Stress Test job cancelled")
        await context.bot.send_message(chat_id=chat_id, text="Stress Test: задача отменена /reset.")
    except Exception as exc:  # noqa: BLE001
        runtime.logger.exception("Stress Test job failed: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text=f"Stress Test: ошибка: {exc}\nНажми /log_full, чтобы забрать полный лог.")
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
    runtime.logger.info("A+ Hunter countdown start chat_id=%s seconds=%s", chat_id, seconds)
    remaining = int(seconds)
    while remaining > 0 and runtime.aplus_hunter_enabled:
        await _edit_aplus_status(context, chat_id, runtime, f"{base_text}\n\n⏳ Следующий scan через: {_format_mmss(remaining)}")
        sleep_for = 15 if remaining > 20 else 5
        await asyncio.sleep(min(sleep_for, remaining))
        remaining -= sleep_for
    runtime.logger.info("A+ Hunter countdown stop chat_id=%s enabled=%s remaining=%s", chat_id, runtime.aplus_hunter_enabled, remaining)


def _read_aplus_archive_symbols(zip_path: Path, logger: logging.Logger) -> list[str]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            with zf.open("manifest.json") as f:
                manifest = json.loads(f.read().decode("utf-8"))
        symbols = manifest.get("symbols") or manifest.get("requested_symbols") or []
        return [str(s).upper().replace("-", "_") for s in symbols if str(s).strip()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("A+ Hunter could not read archive symbols for cooldown file=%s: %s", zip_path, exc)
        return []


def _active_aplus_cooldowns(runtime: BotRuntime) -> dict[str, float]:
    now = time.time()
    expired = [s for s, until in runtime.aplus_symbol_cooldown_until.items() if until <= now]
    for symbol in expired:
        runtime.aplus_symbol_cooldown_until.pop(symbol, None)
    return dict(runtime.aplus_symbol_cooldown_until)


async def send_aplus_archive_only(context: ContextTypes.DEFAULT_TYPE, chat_id: int, zip_path: Path, runtime: BotRuntime) -> None:
    limit_bytes = runtime.settings.telegram_send_limit_mb * 1024 * 1024
    size = zip_path.stat().st_size
    runtime.logger.info("A+ Hunter Telegram send start file=%s size=%s chat_id=%s limit_mb=%s", zip_path.name, human_bytes(size), chat_id, runtime.settings.telegram_send_limit_mb)
    if size > limit_bytes:
        runtime.logger.info("A+ Hunter archive exceeds Telegram limit, sending as parts: file=%s", zip_path.name)
        await send_archive_or_parts(context, chat_id, zip_path, runtime)
        runtime.logger.info("A+ Hunter Telegram send done via parts file=%s", zip_path.name)
        return
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    with zip_path.open("rb") as f:
        await context.bot.send_document(
            chat_id=chat_id,
            document=f,
            filename=zip_path.name,
            caption=f"🎯 A+ Hunter archive: {zip_path.name}\nРазмер: {human_bytes(size)}",
        )
    runtime.logger.info("A+ Hunter Telegram send done file=%s chat_id=%s", zip_path.name, chat_id)


async def toggle_aplus_hunter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    query = update.callback_query
    chat_id = update.effective_chat.id
    if runtime.aplus_hunter_enabled:
        runtime.logger.info("A+ Hunter toggle OFF chat_id=%s", chat_id)
        runtime.aplus_hunter_enabled = False
        await _replace_aplus_status(
            context,
            chat_id,
            runtime,
            "🛑 A+ Hunter: OFF\n\nНовые сканы остановлены. Если текущий montage уже строится, он завершится, но следующий круг не запустится.",
        )
        return

    runtime.logger.info("A+ Hunter toggle ON chat_id=%s", chat_id)
    runtime.aplus_hunter_enabled = True
    await _replace_aplus_status(context, chat_id, runtime, "🎯 A+ Hunter: ON\n\nЗапускаю первый top-200 + forced scan.")
    if not runtime.aplus_hunter_task or runtime.aplus_hunter_task.done():
        runtime.aplus_hunter_task = asyncio.create_task(aplus_hunter_loop(context, chat_id))


async def aplus_hunter_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    loop_started = time.perf_counter()
    runtime.logger.info("A+ Hunter loop started chat_id=%s", chat_id)
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

            runtime.logger.info("A+ Hunter cycle start chat_id=%s", chat_id)
            cycle_started = time.perf_counter()
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
                cooldowns = _active_aplus_cooldowns(runtime)
                if cooldowns:
                    runtime.logger.info("A+ Hunter active symbol cooldowns: %s", {s: int((until - time.time()) / 60) for s, until in cooldowns.items()})
                zip_path = await build_aplus_hunter_archive(
                    runtime.settings,
                    runtime.logger,
                    runtime.secret_store,
                    progress,
                    symbol_cooldowns=cooldowns,
                )
                runtime.last_export = zip_path or runtime.last_export
                runtime.logger.info("A+ Hunter cycle build finished chat_id=%s zip=%s elapsed_sec=%.2f", chat_id, zip_path.name if zip_path else None, time.perf_counter() - cycle_started)
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
                runtime.logger.info("A+ Hunter cycle done no archive chat_id=%s elapsed_sec=%.2f", chat_id, time.perf_counter() - cycle_started)
                base = "✅ Scan завершён.\n\nA+ candidates после фильтров/cooldown: 0\nЛучше подождать."
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
            archive_symbols = _read_aplus_archive_symbols(zip_path, runtime.logger)
            cooldown_until = time.time() + APLUS_SYMBOL_COOLDOWN_SEC
            for symbol in archive_symbols:
                runtime.aplus_symbol_cooldown_until[symbol] = cooldown_until
            if archive_symbols:
                runtime.logger.info(
                    "A+ Hunter symbol cooldown set symbols=%s cooldown_sec=%s",
                    archive_symbols,
                    APLUS_SYMBOL_COOLDOWN_SEC,
                )
            runtime.logger.info("A+ Hunter cycle done archive sent chat_id=%s file=%s elapsed_sec=%.2f", chat_id, zip_path.name, time.perf_counter() - cycle_started)
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
        runtime.logger.info("A+ Hunter loop stopped chat_id=%s elapsed_sec=%.2f enabled=%s", chat_id, time.perf_counter() - loop_started, runtime.aplus_hunter_enabled)
        runtime.aplus_hunter_busy = False
        if not runtime.aplus_hunter_enabled:
            runtime.aplus_hunter_task = None



def _symbol_short(symbol: str) -> str:
    return symbol.upper().replace("_USDT", "")


def _symbols_short_list(symbols: list[str]) -> str:
    return "/".join(_symbol_short(s) for s in symbols)


def _parse_intraday_symbols_command(text: str) -> tuple[str, list[str], str | None] | None:
    """Parse text commands:
    - int pol, xrp, sol
    - int pol, int xrp, int sol
    - int del
    Returns (action, symbols, error). action is set/reset.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    lower = raw.lower().strip()
    if lower == "int del":
        return "reset", list(INTRADAY_DEFAULT_SYMBOLS), None
    if not lower.startswith("int "):
        return None

    body = raw[4:].strip()
    if not body:
        return "set", [], "После int укажи монеты: например int pol, xrp, sol"
    tokens = [t.strip() for t in re.split(r"[,\s]+", body) if t.strip()]
    symbols: list[str] = []
    seen: set[str] = set()
    bad: list[str] = []
    for token in tokens:
        if token.lower() == "int":
            continue
        symbol = _normalize_custom_symbol(token)
        if not symbol:
            bad.append(token)
            continue
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    if bad:
        return "set", symbols, f"Не понял символы: {', '.join(bad)}"
    if not symbols:
        return "set", symbols, "Не нашёл монеты для Intraday. Пример: int btc, eth, xau"
    return "set", symbols, None


async def _replace_intraday_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int, runtime: BotRuntime, text: str) -> None:
    """Delete + send a fresh Intraday status message.

    This intentionally moves the live scan/progress status during the cycle.
    When a green archive is ready, the final status is posted first and the archive
    document is sent below it so the file is the bottom message after the scan.
    """
    if runtime.intraday_status_message_id is not None:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=runtime.intraday_status_message_id)
        except Exception as exc:  # noqa: BLE001
            runtime.logger.debug("Could not delete Intraday status message: %s", exc)
    text = text[:3900]
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=main_menu(runtime))
    runtime.intraday_status_message_id = msg.message_id
    runtime.intraday_last_status_text = text
    runtime.logger.info("Intraday status replaced chat_id=%s message_id=%s", chat_id, msg.message_id)


async def _edit_intraday_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int, runtime: BotRuntime, text: str) -> None:
    text = text[:3900]
    if runtime.intraday_last_status_text == text:
        return
    if runtime.intraday_status_message_id is None:
        await _replace_intraday_status(context, chat_id, runtime, text)
        return
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=runtime.intraday_status_message_id, text=text, reply_markup=main_menu(runtime))
        runtime.intraday_last_status_text = text
    except Exception as exc:  # noqa: BLE001
        if "message is not modified" in str(exc).lower():
            runtime.intraday_last_status_text = text
            return
        runtime.logger.debug("Could not edit Intraday status message, replacing: %s", exc)
        await _replace_intraday_status(context, chat_id, runtime, text)


def _progress_bar(percent: int, width: int = 10) -> str:
    percent = max(0, min(100, int(percent)))
    filled = round(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


def _intraday_progress_text(
    symbols: list[str],
    percent: int,
    stage: str,
    details: str = "",
    *,
    current_symbol: str | None = None,
    current_index: int | None = None,
    total_symbols: int | None = None,
    rows: int | None = None,
    expected_rows: int | None = None,
    done_symbols: list[str] | None = None,
    next_step: str | None = None,
    elapsed_sec: float | None = None,
) -> str:
    """Very compact Intraday progress message.

    The detailed progress was too noisy for Telegram. The live progress message is
    now only one line and is deleted after the scan. The final full status message
    and optional archive are sent after it.
    """
    raw = max(0, min(100, int(percent)))
    if raw >= 100 and (stage == "скан завершён" or "no candidates" in details.lower() or "кандидатов нет" in details.lower()):
        return "🔎 Intraday scan - 100% No candidates"
    if raw >= 100:
        return "🔎 Intraday scan - 100%"
    # Keep only clean 10% steps: 10, 20, ... 90.
    shown = min(90, max(10, ((raw + 9) // 10) * 10))
    return f"🔎 Intraday scan - {shown}%"


def _intraday_candidates_progress_text(green: list[Any]) -> str:
    names = ", ".join(_symbol_short(r.symbol).lower() for r in green)
    return f"🔎 Intraday scan - 100% Candidates {names}"


def _intraday_archive_progress_text(step: int, total: int = 3, ok: bool = False) -> str:
    suffix = ". Ok" if ok else ""
    return f"🔎 Intraday scan - {step}/{total} archive{suffix}"


async def _intraday_countdown(context: ContextTypes.DEFAULT_TYPE, chat_id: int, runtime: BotRuntime, base_text: str, seconds: int = 300) -> None:
    runtime.logger.info("Intraday countdown start chat_id=%s seconds=%s", chat_id, seconds)
    remaining = int(seconds)
    while remaining > 0 and runtime.intraday_enabled:
        await _edit_intraday_status(context, chat_id, runtime, f"{base_text}\n\n⏳ Следующий Intraday scan через: {_format_mmss(remaining)}")
        sleep_for = 15 if remaining > 20 else 5
        await asyncio.sleep(min(sleep_for, remaining))
        remaining -= sleep_for
    runtime.logger.info("Intraday countdown stop chat_id=%s enabled=%s remaining=%s", chat_id, runtime.intraday_enabled, remaining)




def _intraday_error_report(symbol: str, exc: Exception) -> IntradayReport:
    msg = str(exc).replace("\n", " ")[:220] or exc.__class__.__name__
    return IntradayReport(
        symbol=symbol,
        price=float("nan"),
        regime="NO_DATA",
        allowed_direction="WAIT",
        decision="NO_TRADE",
        playbook="none",
        buyer_pressure=0,
        seller_pressure=0,
        absorption=0,
        trap_risk=100,
        late_risk=100,
        long_score=0,
        short_score=0,
        quality_score=0,
        vwap=float("nan"),
        day_open=float("nan"),
        day_high=float("nan"),
        day_low=float("nan"),
        high_24h=float("nan"),
        low_24h=float("nan"),
        distance_to_vwap_pct=0.0,
        distance_to_24h_high_pct=0.0,
        distance_to_24h_low_pct=0.0,
        comment=f"Ошибка загрузки/анализа Intraday: {msg}. Остальные монеты продолжают сканироваться.",
        archive_reason=None,
    )



def _opposite_intraday_trend(a: str | None, b: str | None) -> bool:
    return {a, b} == {"TREND_LONG", "TREND_SHORT"}


def _transition_report(report: IntradayReport, previous: str | None, pending_count: int) -> IntradayReport:
    return replace(
        report,
        regime="TRANSITION",
        allowed_direction="WAIT",
        decision="WAIT",
        playbook="none",
        quality_score=0,
        archive_reason=None,
        comment=(
            f"Режим меняется: было {previous or 'n/a'}, сейчас {report.regime}. "
            f"Нужно 2 скана подряд для подтверждения смены ({pending_count}/2). Сделку не открывать."
        ),
    )


def _apply_intraday_hysteresis(runtime: BotRuntime, report: IntradayReport) -> IntradayReport:
    """Prevent direct TREND_LONG <-> TREND_SHORT flips between 5m scans.

    A raw opposite trend is downgraded to TRANSITION/WAIT until the same new trend
    appears in two consecutive scans. This keeps Intraday from changing direction
    on one noisy candle. State is runtime-only and affects only Intraday.
    """
    raw_regime = str(report.regime or "")
    symbol = str(report.symbol or "")
    state = runtime.intraday_regime_state.get(symbol, {"stable": None, "pending": None, "count": 0})
    stable = state.get("stable")

    if raw_regime not in {"TREND_LONG", "TREND_SHORT"}:
        # Keep the last stable trend in memory through TRANSITION/RANGE/CHOP so a later
        # opposite trend still needs confirmation instead of flipping immediately.
        next_stable = stable if stable in {"TREND_LONG", "TREND_SHORT"} else raw_regime
        runtime.intraday_regime_state[symbol] = {"stable": next_stable, "pending": None, "count": 0}
        return report

    if stable in {None, raw_regime} or stable not in {"TREND_LONG", "TREND_SHORT"}:
        runtime.intraday_regime_state[symbol] = {"stable": raw_regime, "pending": None, "count": 0}
        return report

    if _opposite_intraday_trend(stable, raw_regime):
        pending = state.get("pending")
        count = int(state.get("count") or 0) + 1 if pending == raw_regime else 1
        if count < 2:
            runtime.intraday_regime_state[symbol] = {"stable": stable, "pending": raw_regime, "count": count}
            runtime.logger.info("Intraday hysteresis transition symbol=%s stable=%s raw=%s count=%s", symbol, stable, raw_regime, count)
            return _transition_report(report, stable, count)
        runtime.intraday_regime_state[symbol] = {"stable": raw_regime, "pending": None, "count": 0}
        runtime.logger.info("Intraday hysteresis confirmed new trend symbol=%s stable=%s", symbol, raw_regime)
        return report

    runtime.intraday_regime_state[symbol] = {"stable": raw_regime, "pending": None, "count": 0}
    return report

def _intraday_status_text(reports: list[Any], created_msk: str, symbols: list[str], archive_name: str | None = None) -> str:
    green = [r for r in reports if getattr(r, "is_green", False)]
    yellow = [r for r in reports if getattr(r, "color_emoji", "") == "🟡"]
    red = [r for r in reports if getattr(r, "color_emoji", "") == "🔴"]
    lines = [
        f"📊 Intraday status — {created_msk}",
        "Scan: 5m | Mode: MANUAL REVIEW | Auto-trade: OFF",
        f"Symbols: {_symbols_short_list(symbols)}",
        "",
    ]
    for r in reports:
        lines.append(r.short_line())
        rank = f" | rank {r.quality_score}" if getattr(r, "is_green", False) else ""
        lines.append(f"  Давление: B{r.buyer_pressure}/S{r.seller_pressure} | trap {r.trap_risk} | late {r.late_risk} | {r.playbook}{rank}")
        lines.append(f"  {r.comment}")
        lines.append("")
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"🟢 candidates: {len(green)} | 🟡 wait: {len(yellow)} | 🔴 no trade: {len(red)}")
    if green:
        lines.append("")
        lines.append("📦 Архив для проверки ниже:")
        lines.append(archive_name or "строится/отправляется")
        lines.append("")
        lines.append("Внутри:")
        for idx, r in enumerate(green, 1):
            lines.append(f"{idx}. {r.symbol} — {r.playbook} / {r.archive_reason or 'MANUAL_REVIEW'}")
    else:
        lines.append("")
        lines.append("📦 Архив: нет зелёных MANUAL_REVIEW кандидатов")
    return "\n".join(lines)


async def send_intraday_archive_only(context: ContextTypes.DEFAULT_TYPE, chat_id: int, zip_path: Path, runtime: BotRuntime) -> None:
    limit_bytes = runtime.settings.telegram_send_limit_mb * 1024 * 1024
    size = zip_path.stat().st_size
    runtime.logger.info("Intraday Telegram send start file=%s size=%s chat_id=%s limit_mb=%s", zip_path.name, human_bytes(size), chat_id, runtime.settings.telegram_send_limit_mb)
    if size > limit_bytes:
        runtime.logger.info("Intraday archive exceeds Telegram limit, sending as parts: file=%s", zip_path.name)
        await send_archive_or_parts(context, chat_id, zip_path, runtime)
        runtime.logger.info("Intraday Telegram send done via parts file=%s", zip_path.name)
        return
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    with zip_path.open("rb") as f:
        await context.bot.send_document(
            chat_id=chat_id,
            document=f,
            filename=zip_path.name,
            caption=f"📦 Intraday archive: {zip_path.name}\nРазмер: {human_bytes(size)}",
        )
    runtime.logger.info("Intraday Telegram send done file=%s chat_id=%s", zip_path.name, chat_id)


async def toggle_intraday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    chat_id = update.effective_chat.id
    if runtime.intraday_enabled:
        runtime.logger.info("Intraday toggle OFF chat_id=%s", chat_id)
        runtime.intraday_enabled = False
        await _replace_intraday_status(
            context,
            chat_id,
            runtime,
            "🛑 Intraday: OFF\n\nНовые 5m-сканы остановлены. Старые режимы scan/montage/A+ Hunter не изменены.",
        )
        return

    runtime.logger.info("Intraday toggle ON chat_id=%s symbols=%s", chat_id, runtime.intraday_symbols)
    runtime.intraday_enabled = True
    runtime.intraday_last_signature = None
    runtime.intraday_last_archive_sent_at = 0.0
    await _replace_intraday_status(
        context,
        chat_id,
        runtime,
        f"📊 Intraday: ON\n\nСканирую {_symbols_short_list(runtime.intraday_symbols)} каждые 5 минут. Данные: свежая загрузка {runtime.settings.intraday_days_back}d без parquet/cache. Прогресс короткий: 10%/20%/90%/100%, после скана он удаляется. Финальный статус и архив идут ниже.",
    )
    if not runtime.intraday_task or runtime.intraday_task.done():
        runtime.intraday_task = asyncio.create_task(intraday_loop(context, chat_id))


async def intraday_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    runtime: BotRuntime = context.application.bot_data["runtime"]
    runtime.logger.info("Intraday loop started chat_id=%s", chat_id)
    try:
        while runtime.intraday_enabled:
            try:
                if runtime.active_task and not runtime.active_task.done():
                    base = f"📊 Intraday: ON / пауза\n\nСейчас {runtime.active_summary()}. Интрадей продолжит после освобождения бота."
                    await _replace_intraday_status(context, chat_id, runtime, base)
                    await _intraday_countdown(context, chat_id, runtime, base, 60)
                    continue
                if runtime.aplus_hunter_busy:
                    base = "📊 Intraday: ON / пауза\n\nСейчас идёт A+ Hunter круг. Интрадей продолжит после освобождения API."
                    await _replace_intraday_status(context, chat_id, runtime, base)
                    await _intraday_countdown(context, chat_id, runtime, base, 60)
                    continue

                runtime.intraday_busy = True
                base = await intraday_cycle(context, chat_id, runtime)
                runtime.intraday_busy = False
                await _intraday_countdown(context, chat_id, runtime, base, runtime.settings.intraday_scan_interval_sec)
            except Exception as exc:  # noqa: BLE001
                runtime.logger.exception("Intraday cycle failed: %s", exc)
                runtime.intraday_busy = False
                base = f"⚠️ Intraday error: {exc}\n\nПовтор будет через 5 минут, если Intraday ON. Подробности в /log_full."
                await _replace_intraday_status(context, chat_id, runtime, base)
                await _intraday_countdown(context, chat_id, runtime, base, runtime.settings.intraday_scan_interval_sec)
    except asyncio.CancelledError:
        runtime.logger.warning("Intraday loop cancelled")
    finally:
        runtime.logger.info("Intraday loop stopped chat_id=%s enabled=%s", chat_id, runtime.intraday_enabled)
        runtime.intraday_busy = False
        if not runtime.intraday_enabled:
            runtime.intraday_task = None


async def intraday_cycle(context: ContextTypes.DEFAULT_TYPE, chat_id: int, runtime: BotRuntime) -> str:
    cycle_started = time.perf_counter()
    symbols = list(runtime.intraday_symbols or INTRADAY_DEFAULT_SYMBOLS)
    runtime.logger.info("Intraday cycle start chat_id=%s symbols=%s days=%s interval=%s", chat_id, symbols, runtime.settings.intraday_days_back, runtime.settings.base_interval)
    await _replace_intraday_status(
        context,
        chat_id,
        runtime,
        _intraday_progress_text(
            symbols,
            0,
            "подготовка",
            "Готовлю свежую загрузку 30d без cache/parquet.",
            next_step="получить server time → скачать свечи по монетам",
            elapsed_sec=time.perf_counter() - cycle_started,
        ),
    )

    client = MexcSpotClient(runtime.settings.mexc_base_url, runtime.logger, runtime.settings.mexc_market_type)
    # Match A+ Hunter speed profile for the lightweight public futures scan: fresh download,
    # no parquet/cache, but faster serialized requests than the old heavy 30d archive collector.
    if runtime.settings.mexc_market_type == "futures":
        client.min_request_interval_sec = 0.35
    runtime.logger.info("Intraday market client ready throttle=%.2fs cache=off days=%s", client.min_request_interval_sec, runtime.settings.intraday_days_back)
    reports = []
    data_by_symbol: dict[str, dict[str, Any]] = {}
    done: list[str] = []
    total_symbols = max(1, len(symbols))
    try:
        server_time = await client.server_time()
        interval_ms = INTERVAL_MS.get(runtime.settings.base_interval, 60_000)
        window = DownloadWindow.last_days_from_end_ms(runtime.settings.intraday_days_back, int(server_time["serverTime"]), interval_ms)
        expected_rows_total = max(1, (window.end_ms - window.start_ms) // interval_ms)
        approx_requests = max(1, (expected_rows_total + 1999) // 2000)
        runtime.logger.info("Intraday server_time=%s window_start=%s window_end=%s expected_rows=%s approx_requests_per_symbol=%s", server_time, window.start_ms, window.end_ms, expected_rows_total, approx_requests)
        await _edit_intraday_status(
            context,
            chat_id,
            runtime,
            _intraday_progress_text(
                symbols,
                5,
                "окно данных готово",
                f"Период: {runtime.settings.intraday_days_back}d, примерно {expected_rows_total} свечей и {approx_requests} запросов на монету.",
                done_symbols=done,
                next_step="загрузка и анализ каждой монеты",
                elapsed_sec=time.perf_counter() - cycle_started,
            ),
        )
        download_weight = 80
        for idx, symbol in enumerate(symbols, 1):
            runtime.logger.info("Intraday symbol start %s/%s symbol=%s days=%s", idx, len(symbols), symbol, runtime.settings.intraday_days_back)
            start_pct = 5 + int(download_weight * (idx - 1) / total_symbols)
            end_pct = 5 + int(download_weight * idx / total_symbols)
            last_progress_edit = 0.0
            last_progress_percent = -1

            await _edit_intraday_status(
                context,
                chat_id,
                runtime,
                _intraday_progress_text(
                    symbols,
                    start_pct,
                    "загрузка свечей",
                    f"Качаю {runtime.settings.base_interval} за {runtime.settings.intraday_days_back}d с MEXC. Без cache/parquet.",
                    current_symbol=symbol,
                    current_index=idx,
                    total_symbols=total_symbols,
                    rows=0,
                    expected_rows=expected_rows_total,
                    done_symbols=done,
                    next_step="после загрузки: анализ режима, давления, trap/late risk",
                    elapsed_sec=time.perf_counter() - cycle_started,
                ),
            )

            async def symbol_progress(local_pct: float, rows_loaded: int, expected_rows: int) -> None:
                nonlocal last_progress_edit, last_progress_percent
                local_pct = max(0.0, min(100.0, float(local_pct)))
                global_pct = start_pct + int((end_pct - start_pct) * local_pct / 100.0)
                now = time.monotonic()
                # Telegram edit limits: update when the visible percent moved enough,
                # every ~8 seconds, and always at 0/100 for a symbol.
                if local_pct not in {0.0, 100.0} and global_pct < last_progress_percent + 3 and now - last_progress_edit < 8:
                    return
                last_progress_edit = now
                last_progress_percent = global_pct
                approx_done_requests = max(1, (int(rows_loaded) + 1999) // 2000) if rows_loaded else 0
                await _edit_intraday_status(
                    context,
                    chat_id,
                    runtime,
                    _intraday_progress_text(
                        symbols,
                        global_pct,
                        "загрузка свечей",
                        f"{symbol}: {local_pct:.0f}% по данным, примерно {approx_done_requests}/{approx_requests} запросов.",
                        current_symbol=symbol,
                        current_index=idx,
                        total_symbols=total_symbols,
                        rows=int(rows_loaded),
                        expected_rows=int(expected_rows),
                        done_symbols=done,
                        next_step="после загрузки: анализ режима, давления, trap/late risk",
                        elapsed_sec=time.perf_counter() - cycle_started,
                    ),
                )

            try:
                df = await client.download_klines_dataframe(
                    symbol,
                    runtime.settings.base_interval,
                    window,
                    progress_every_requests=3,
                    progress_cb=symbol_progress,
                )
                runtime.logger.info("Intraday symbol downloaded symbol=%s rows=%s", symbol, len(df))
                analyze_pct = min(87, max(start_pct, end_pct - 2))
                await _edit_intraday_status(
                    context,
                    chat_id,
                    runtime,
                    _intraday_progress_text(
                        symbols,
                        analyze_pct,
                        "анализ монеты",
                        f"{symbol}: свечи загружены, считаю regime / pressure / playbook.",
                        current_symbol=symbol,
                        current_index=idx,
                        total_symbols=total_symbols,
                        rows=len(df),
                        expected_rows=expected_rows_total,
                        done_symbols=done,
                        next_step="после анализа: перейти к следующей монете",
                        elapsed_sec=time.perf_counter() - cycle_started,
                    ),
                )
                report, df_1m, frames = analyze_intraday_symbol(symbol, df)
                raw_regime = report.regime
                raw_decision = report.decision
                report = _apply_intraday_hysteresis(runtime, report)
                runtime.logger.info(
                    "Intraday report symbol=%s raw_regime=%s raw_decision=%s regime=%s decision=%s playbook=%s green=%s buyer=%s seller=%s trap=%s late=%s data_warning=%s comment=%s",
                    report.symbol,
                    raw_regime,
                    raw_decision,
                    report.regime,
                    report.decision,
                    report.playbook,
                    report.is_green,
                    report.buyer_pressure,
                    report.seller_pressure,
                    report.trap_risk,
                    report.late_risk,
                    getattr(report, "data_warning", False),
                    report.comment,
                )
                reports.append(report)
                data_by_symbol[symbol] = {"df_1m": df_1m, "frames": frames}
                rows_for_progress = len(df)
            except Exception as exc:  # noqa: BLE001
                runtime.logger.exception("Intraday symbol failed %s/%s symbol=%s: %s", idx, len(symbols), symbol, exc)
                report = _intraday_error_report(symbol, exc)
                reports.append(report)
                rows_for_progress = 0

            done.append(symbol)
            done_pct = end_pct
            await _edit_intraday_status(
                context,
                chat_id,
                runtime,
                _intraday_progress_text(
                    symbols,
                    done_pct,
                    "монета готова",
                    f"{report.symbol}: {report.regime} / {report.decision} | B{report.buyer_pressure}/S{report.seller_pressure} | trap {report.trap_risk} | late {report.late_risk}",
                    current_symbol=symbol,
                    current_index=idx,
                    total_symbols=total_symbols,
                    rows=rows_for_progress,
                    expected_rows=expected_rows_total,
                    done_symbols=done,
                    next_step="следующая монета" if idx < total_symbols else "ранжирование кандидатов",
                    elapsed_sec=time.perf_counter() - cycle_started,
                ),
            )
    finally:
        await client.close()

    await _edit_intraday_status(
        context,
        chat_id,
        runtime,
        _intraday_progress_text(
            symbols,
            88,
            "ранжирование",
            "Сортирую зелёные MANUAL_REVIEW по quality_score.",
            done_symbols=done,
            next_step="если зелёные есть — собрать один общий архив",
            elapsed_sec=time.perf_counter() - cycle_started,
        ),
    )
    reports.sort(key=_intraday_sort_key)
    green = [r for r in reports if r.is_green]
    archive_name: str | None = None
    zip_path: Path | None = None

    if green:
        runtime.logger.info("Intraday green candidates found count=%s symbols=%s", len(green), [r.symbol for r in green])
        await _edit_intraday_status(context, chat_id, runtime, _intraday_candidates_progress_text(green))
        await asyncio.sleep(0.5)
        await _edit_intraday_status(context, chat_id, runtime, _intraday_archive_progress_text(1, 3))
        zip_path = await asyncio.to_thread(build_intraday_candidates_archive, runtime.settings, runtime.logger, green, data_by_symbol)
        if zip_path is not None:
            await _edit_intraday_status(context, chat_id, runtime, _intraday_archive_progress_text(2, 3))
            runtime.last_export = zip_path
            archive_name = zip_path.name
            runtime.intraday_last_signature = "|".join(sorted(r.symbol for r in green))
            runtime.intraday_last_archive_sent_at = time.time()
            await _edit_intraday_status(context, chat_id, runtime, _intraday_archive_progress_text(3, 3, ok=True))
            await asyncio.sleep(0.5)
        else:
            archive_name = "ошибка: зелёные есть, но архив не собран — см /log_full"
            runtime.logger.error("Intraday green candidates found, but archive builder returned None symbols=%s", [r.symbol for r in green])
    else:
        runtime.logger.info("Intraday cycle no green candidates")
        await _edit_intraday_status(
            context,
            chat_id,
            runtime,
            _intraday_progress_text(
                symbols,
                100,
                "скан завершён",
                "Зелёных MANUAL_REVIEW кандидатов нет, архив не создаётся.",
                done_symbols=done,
                next_step="финальный статус → таймер 5:00",
                elapsed_sec=time.perf_counter() - cycle_started,
            ),
        )

    finished_msk = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M MSK")
    base = _intraday_status_text(reports, finished_msk, symbols, archive_name)
    await _replace_intraday_status(context, chat_id, runtime, base)
    if zip_path is not None:
        await send_intraday_archive_only(context, chat_id, zip_path, runtime)
    runtime.logger.info(
        "Intraday cycle finished chat_id=%s elapsed_sec=%.2f green=%s archive=%s",
        chat_id,
        time.perf_counter() - cycle_started,
        [r.symbol for r in green],
        archive_name,
    )
    return base


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
    application.add_handler(CommandHandler("help", handle_help))
    application.add_handler(CommandHandler("api", start_api_flow))
    application.add_handler(CommandHandler("log_full", handle_log_full))
    application.add_handler(CommandHandler("ping", handle_ping))
    application.add_handler(CommandHandler("reset", handle_reset))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("Bot started. Version: %s. Data root: %s", settings.app_version, settings.data_root)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
