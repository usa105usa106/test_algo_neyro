from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Awaitable

import pandas as pd

from charts import load_ohlcv, resample_ohlcv, _plot_candles
from config import ScanPreset, Settings
from file_utils import (
    dir_size_bytes,
    file_sha256,
    human_bytes,
    moscow_scan_stamp,
    safe_rmtree,
    utc_stamp,
    write_json,
    zip_directory,
)
from mexc import DownloadWindow, INTERVAL_MS, MexcSpotClient, save_dataframe_parquet
from security import SecretStore

ProgressCallback = Callable[[str], Awaitable[None]]


ASSET_LABELS = {
    "gold": "Gold / XAU",
    "btc": "BTC",
    "eth": "ETH",
    "silver": "Silver / XAG",
    "oil": "Oil / WTI",
}


def _asset_key_for_symbol(symbol: str) -> str | None:
    s = symbol.upper().replace("-", "_")
    if s.startswith(("XAU", "GOLD", "XAUT", "PAXG")):
        return "gold"
    if s.startswith("BTC"):
        return "btc"
    if s.startswith("ETH"):
        return "eth"
    if s.startswith(("SILVER", "XAG")):
        return "silver"
    if s.startswith(("OIL", "WTI", "USOIL", "UKOIL")):
        return "oil"
    return None


def _asset_label_for_preset(preset: ScanPreset) -> str:
    if preset.key in ASSET_LABELS:
        return ASSET_LABELS[preset.key]
    if len(preset.symbols) == 1:
        # Custom scans should keep their own symbol label. Do not call XAUT "Gold" or
        # UKOIL "Oil" unless the user used the exact main preset/alias.
        symbol = preset.symbols[0].upper().replace("-", "_")
        if symbol.endswith("_USDT"):
            return symbol[:-5]
        return symbol
    return preset.title


def _exact_candidates_for_symbol(symbol: str) -> list[str]:
    # Exact-only: verify exactly the requested symbol. Do not map custom XAUT to XAU,
    # UKOIL to USOIL, or any other similarly named instrument with a different price.
    return [symbol]


async def _notify(cb: ProgressCallback | None, message: str) -> None:
    if cb:
        try:
            await cb(message)
        except Exception:
            pass


class PercentReporter:
    """Sends compact Telegram progress updates at 0/10/20/.../100%."""

    def __init__(self, prefix: str, cb: ProgressCallback | None):
        self.prefix = prefix
        self.cb = cb
        self.last_bucket = -10

    async def report(self, percent: float, message: str, *, force: bool = False) -> None:
        pct = max(0, min(100, int(percent)))
        bucket = 100 if pct >= 100 else (pct // 10) * 10
        if force or bucket > self.last_bucket:
            self.last_bucket = bucket
            await _notify(self.cb, f"{self.prefix}: {bucket}% — {message}")


def _setup_format_text(preset: ScanPreset) -> str:
    """Human-readable strict output template stored inside every scan archive."""
    if len(preset.symbols) == 1:
        asset_label = _asset_label_for_preset(preset)
        return f"""SETUP_FORMAT.txt
Use this exact structure when answering from this archive.
Answer in Russian only. No extra comments before or after the setup. Do not add an "Актив:" line.

If there is NO clean setup, answer exactly one line:
wait, сейчас лучше не входить, подожди и пришли новый архив.

If there IS a setup, answer exactly like this:

Setup {asset_label}:

Маркет - <пропускаем, нет A+ сетапа / LONG MARKET price / SHORT MARKET price>

Лимит:
SHORT LIMIT 1: <price or WAIT>
SHORT LIMIT 2: <price or WAIT>
или
LONG LIMIT 1: <price or WAIT>
LONG LIMIT 2: <price or WAIT>

Тейки:
TP1: <price> — закрыть 33%, SL в б/у
TP2: <price> — закрыть 33%, SL в б/у
TP3: <price> — закрыть остаток

SL: <price>

Убрать лимит:
<условие, когда снять лимитки, например: если цена ушла к TP1/TP2 без входа — не догонять>
<условие, когда идея сломана, например: если пробой и закрепление выше/ниже SL-зоны>

Причина:
<1–3 коротких предложения по 1D/4H/1H/15m/1m: почему LONG/SHORT/WAIT и почему вход именно от этой зоны>

Rules for the format:
- Write LIMIT orders in a column, one order per line. Never write both limits on one line.
- Use SHORT LIMIT for short entries and LONG LIMIT for long entries. Do not write SELL LIMIT or BUY LIMIT.
- Write TP1/TP2/TP3 in a column, one take-profit per line, with management text on the same TP line. Never write all take-profits on one line.
- Do not add a separate "Сопровождение" section; management is already inside TP1/TP2/TP3 lines.
- Write SL on its own separate line.
- If market entry is not A+, write: Маркет - пропускаем, нет A+ сетапа.
- Use LIMIT when entry should be only from pullback/reaction zone.
- Use WAIT if price is late, already near TP, no zone, no clear SL, or timeframes conflict.
- Do not write long theory. Do not add warnings unrelated to the setup.
""".strip()

    return """SETUP_FORMAT.txt
Use this exact structure when answering from this multi-asset archive.
Answer in Russian only. No extra comments before or after the setups. Do not add "Актив:" lines.

If there is NO clean setup on all assets, answer exactly one line:
wait, сейчас лучше не входить, подожди и пришли новый архив.

If at least one asset has a setup, return all 5 blocks in this order.
For assets without a setup, write WAIT inside that asset block.

Setup Gold / XAU:

Маркет - <пропускаем, нет A+ сетапа / LONG MARKET price / SHORT MARKET price>

Лимит:
SHORT LIMIT 1: <price or WAIT>
SHORT LIMIT 2: <price or WAIT>
или
LONG LIMIT 1: <price or WAIT>
LONG LIMIT 2: <price or WAIT>

Тейки:
TP1: <price or WAIT> — закрыть 33%, SL в б/у
TP2: <price or WAIT> — закрыть 33%, SL в б/у
TP3: <price or WAIT> — закрыть остаток

SL: <price or WAIT>

Убрать лимит:
<...>
<...>

Причина:
<...>

Setup BTC:

Маркет - <...>

Лимит:
SHORT LIMIT 1: <...>
SHORT LIMIT 2: <...>
или
LONG LIMIT 1: <...>
LONG LIMIT 2: <...>

Тейки:
TP1: <...> — закрыть 33%, SL в б/у
TP2: <...> — закрыть 33%, SL в б/у
TP3: <...> — закрыть остаток

SL: <...>

Убрать лимит:
<...>
<...>

Причина:
<...>

Setup ETH:

Маркет - <...>

Лимит:
SHORT LIMIT 1: <...>
SHORT LIMIT 2: <...>
или
LONG LIMIT 1: <...>
LONG LIMIT 2: <...>

Тейки:
TP1: <...> — закрыть 33%, SL в б/у
TP2: <...> — закрыть 33%, SL в б/у
TP3: <...> — закрыть остаток

SL: <...>

Убрать лимит:
<...>
<...>

Причина:
<...>

Setup Silver / XAG:

Маркет - <...>

Лимит:
SHORT LIMIT 1: <...>
SHORT LIMIT 2: <...>
или
LONG LIMIT 1: <...>
LONG LIMIT 2: <...>

Тейки:
TP1: <...> — закрыть 33%, SL в б/у
TP2: <...> — закрыть 33%, SL в б/у
TP3: <...> — закрыть остаток

SL: <...>

Убрать лимит:
<...>
<...>

Причина:
<...>

Setup Oil / WTI:

Маркет - <...>

Лимит:
SHORT LIMIT 1: <...>
SHORT LIMIT 2: <...>
или
LONG LIMIT 1: <...>
LONG LIMIT 2: <...>

Тейки:
TP1: <...> — закрыть 33%, SL в б/у
TP2: <...> — закрыть 33%, SL в б/у
TP3: <...> — закрыть остаток

SL: <...>

Убрать лимит:
<...>
<...>

Причина:
<...>

Rules for the format:
- Write LIMIT orders in a column, one order per line. Never write both limits on one line.
- Use SHORT LIMIT for short entries and LONG LIMIT for long entries. Do not write SELL LIMIT or BUY LIMIT.
- Write TP1/TP2/TP3 in a column, one take-profit per line, with management text on the same TP line. Never write all take-profits on one line.
- Do not add a separate "Сопровождение" section; management is already inside TP1/TP2/TP3 lines.
- Write SL on its own separate line.
- If market entry is not A+, write: Маркет - пропускаем, нет A+ сетапа.
- Use LIMIT when entry should be only from pullback/reaction zone.
- Use WAIT if price is late, already near TP, no zone, no clear SL, or timeframes conflict.
- Do not write long theory. Do not add warnings unrelated to the setup.
""".strip()


def _chatgpt_task_text(preset: ScanPreset, created_msk: str) -> str:
    assets = ", ".join(preset.symbols)
    setup_format = _setup_format_text(preset)

    strategy_block = """STRATEGY: Elite 5 Rejection / Rostislav-style

ROLE:
You are a manual/semi-auto trading assistant. Analyze only the archive data/charts and return a concrete setup: LONG / SHORT / WAIT, entry zone, limit orders, SL, TP1/TP2/TP3 with management inside TP lines, trade class and short reason.
Do not give long theory. Do not discuss general market opinions. Answer only with the setup.

TRADING UNIVERSE:
Main priority assets: BTC, ETH, XAU/GOLD, XAG/SILVER, OIL.
Main priority: XAU/GOLD.
If this archive was created from a custom text symbol, analyze that requested symbol too, but keep the same strict rules and reject weak/late setups.
Use exact MEXC symbols from manifest only. Do not replace XAU_USDT with XAUT_USDT. Do not replace USOIL_USDT/WTI with UKOIL_USDT/Brent. Do not replace a custom symbol with another instrument.

TIMEFRAMES:
1D = общий фон.
4H = главный старший контекст.
1H = структура движения.
15m = зона входа.
1m = точная реакция / микровход.
If some context is incomplete or manifest shows partial history, use available data and do not invent missing candles.

CORE PRINCIPLE:
The strategy is not based on smart money, random patterns or chasing pumps. Use trend, levels, structure, impulse, pullback, reaction and entry from a zone.
Price makes a strong move, then comes to an important zone. Do not chase price. Wait for pullback or reaction. Enter from the zone. SL goes behind the local invalidation high/low. TPs are partial.

SHORT MODEL:
Look for SHORT when 1H/4H are weak, price has already fallen or bounced after a fall, and price pulls back upward into resistance / local high / broken zone / MA / level.
Entry only from pullback. Do not short market at the bottom. SL above local high. TP levels below by nearest zones.
Rule: want SHORT -> wait for pullback upward. If price already dropped to TP zone without entry, skip.

LONG MODEL:
Look for LONG only when there was a strong dump, price reached a lower local zone, and 1m/15m shows upward reaction.
This is usually a SOFT quick bounce, not a global reversal. Targets must be close. After TP1 reduce risk or move SL to entry.

TRADE CLASSES:
A+ = higher timeframes agree, clean zone, strong impulse, clear invalidation, entry is not late. Allocation 20% total balance, isolated 10x.
A = good clear setup, but not perfect. Allocation 10% total balance, isolated 10x.
SOFT = cautious quick scalp, often countertrend, close targets, only from a good zone. Allocation up to 10% if entry is high-quality.
REJECT/WAIT = bad setup, late price, no zone, no clear SL, conflicting timeframes, or price already reached targets.

POSITION MANAGEMENT:
TP1 = close part of position.
TP2 = close part of position.
TP3 = close remainder.
For SOFT: after TP1 move SL to entry or strongly tighten it.
For A/A+: after TP2 SL must be at entry.
If price returns to entry after TP1/TP2, exit remainder without loss.

BANS:
Do not chase price.
Do not enter market after a strong move unless clearly A+.
Do not give entry if price already reached TP1/TP2.
Do not place a limit too close to current price when price is already low for SHORT or already high for LONG.
Do not average against position.
Do not hold SOFT as a large trend trade.
Do not open any setup without clear SL.
If setup is unclear, answer WAIT/no trade.

OUTPUT RULES:
Answer in Russian only.
Return ONLY the final setup, no extra explanation.
If no clean setup exists on the archive, answer exactly:
wait, сейчас лучше не входить, подожди и пришли новый архив."""

    title = "multi-asset scan archive" if len(preset.symbols) > 1 else "scan archive"
    charts_desc = "5 charts per asset: 1D, 4H, 1H, 15m, 1m" if len(preset.symbols) > 1 else "5 charts: 1D, 4H, 1H, 15m, 1m"
    symbols_label = "Requested exact symbols" if len(preset.symbols) > 1 else "Requested exact symbol"

    return f"""TASK:
Analyze this MEXC Futures {title} using the strategy and output template below.

Archive created: {created_msk} UTC+3/MSK
{symbols_label}: {assets}
Data: 1m OHLC requested for last 30 days, plus {charts_desc}.

{strategy_block}

STRICT SETUP WRITING FORMAT:
The answer must follow the template below. The same template is also stored as setup_format.txt in the archive root.

{setup_format}
""".strip()


async def _plot_scan_charts_for_symbol(
    symbol: str,
    candle_path: Path,
    charts_out: Path,
    logger: logging.Logger,
    chart_done_cb: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[list[str], list[str]]:
    chart_files: list[str] = []
    warnings: list[str] = []

    df_1m = await asyncio.to_thread(load_ohlcv, candle_path)
    if df_1m.empty:
        raise RuntimeError(f"No candle data in {candle_path}")

    latest_ts = df_1m.index.max()
    available_days = len(df_1m) / 1440.0
    window_label = f"requested 30d / available ~{available_days:.1f}d"

    async def plot(df: pd.DataFrame, title: str, output: Path, figsize=(16, 8), mav=(20, 50)) -> None:
        if len(df) < 2:
            warnings.append(f"{title}: too few rows")
            return
        await asyncio.to_thread(_plot_candles, df, title, output, figsize, mav)
        rel = str(output.relative_to(charts_out.parent))
        chart_files.append(rel)
        if chart_done_cb:
            await chart_done_cb(rel)

    # Exactly 5 charts per asset, focused on current manual/semi-auto setup analysis.
    df_1d = resample_ohlcv(df_1m, "1d")
    await plot(df_1d, f"{symbol} 1D — {window_label}", charts_out / symbol / f"{symbol}_1D.png", figsize=(18, 9), mav=(7, 20))

    df_4h = resample_ohlcv(df_1m, "4h")
    await plot(df_4h, f"{symbol} 4H — {window_label}", charts_out / symbol / f"{symbol}_4H.png", figsize=(18, 9), mav=(20, 50))

    df_1h = resample_ohlcv(df_1m, "1h")
    await plot(df_1h, f"{symbol} 1H — {window_label}", charts_out / symbol / f"{symbol}_1H.png", figsize=(18, 9), mav=(20, 50, 200))

    df_15m = resample_ohlcv(df_1m, "15min")
    recent_15m = df_15m[df_15m.index >= latest_ts - pd.Timedelta(days=7)]
    await plot(recent_15m, f"{symbol} 15m — last 7 days", charts_out / symbol / f"{symbol}_15m.png", figsize=(18, 9), mav=(20, 50, 200))

    recent_1m = df_1m[df_1m.index >= latest_ts - pd.Timedelta(hours=24)]
    await plot(recent_1m, f"{symbol} 1m — last 24 hours", charts_out / symbol / f"{symbol}_1m.png", figsize=(18, 9), mav=(20, 50, 200))

    logger.info("Created %s scan chart files for %s", len(chart_files), symbol)
    return chart_files, warnings


async def _resolve_scan_symbols(client: MexcSpotClient, preset: ScanPreset, logger: logging.Logger) -> tuple[list[str], dict[str, dict[str, object]]]:
    """Verify exact scan symbols against MEXC Futures without substitution.

    If the detail endpoint is unavailable or the exact symbol is not confirmed, keep the
    original requested symbol so the scan fails transparently during candle download and
    /log_full captures details. No fallback symbols are selected automatically.
    """
    resolved_symbols: list[str] = []
    resolution: dict[str, dict[str, object]] = {}

    for requested in preset.symbols:
        candidates = _exact_candidates_for_symbol(requested)
        selected = requested
        found: list[str] = []
        warnings: list[str] = []
        try:
            info = await client.exchange_info(candidates)
            for item in info.get("symbols", []):
                symbol = item.get("symbol") or item.get("requestedSymbol")
                warning = item.get("warning")
                if symbol and not warning:
                    found.append(str(symbol))
                elif warning:
                    warnings.append(f"{symbol or item.get('requestedSymbol')}: {warning}")
            if found:
                selected = found[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Exact symbol check failed for %s candidates=%s: %s", requested, candidates, exc)
            warnings.append(str(exc))

        if selected not in resolved_symbols:
            resolved_symbols.append(selected)
        resolution[requested] = {
            "requested": requested,
            "selected": selected,
            "candidates": candidates,
            "found": found,
            "warnings": warnings,
        }

    return resolved_symbols, resolution


async def build_scan_archive(
    settings: Settings,
    logger: logging.Logger,
    secret_store: SecretStore,
    preset: ScanPreset,
    progress_cb: ProgressCallback | None = None,
) -> Path:
    utc_build_stamp = utc_stamp()
    scan_stamp = moscow_scan_stamp()
    created_msk = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    build_dir = settings.work_dir / f"scan_build_{utc_build_stamp}" / f"chatgpt_scan_{preset.key}"
    safe_rmtree(build_dir)
    candles_out = build_dir / "candles"
    charts_out = build_dir / "charts"
    meta_out = build_dir / "meta"
    candles_out.mkdir(parents=True, exist_ok=True)
    charts_out.mkdir(parents=True, exist_ok=True)
    meta_out.mkdir(parents=True, exist_ok=True)

    reporter = PercentReporter(f"Scan {preset.title}", progress_cb)
    logger.info("Starting ChatGPT scan build preset=%s symbols=%s", preset.key, preset.symbols)
    await reporter.report(0, f"старт. MEXC Futures, 1m за {settings.days_back} дней, symbols={preset.symbols}", force=True)

    api_mask = secret_store.load_mexc_api_mask()
    client = MexcSpotClient(settings.mexc_base_url, logger, settings.mexc_market_type)
    try:
        await reporter.report(5, f"проверяю MEXC endpoint {settings.mexc_base_url}")
        ping_ok = await client.ping()
        server_time = await client.server_time()
        interval_ms = INTERVAL_MS.get(settings.base_interval, 60_000)
        window = DownloadWindow.last_days_from_end_ms(settings.days_back, int(server_time["serverTime"]), interval_ms)
        resolved_symbols, symbol_resolution = await _resolve_scan_symbols(client, preset, logger)
        exchange_info = await client.exchange_info(resolved_symbols)
        await reporter.report(10, f"MEXC доступен, начинаю сбор свечей: {resolved_symbols}")

        row_counts: dict[str, int] = {}
        candle_files: dict[str, str] = {}
        chart_files: list[str] = []
        warnings: list[str] = []

        symbols_count = max(1, len(resolved_symbols))
        download_span = 62.0
        charts_span = 20.0

        for idx, symbol in enumerate(resolved_symbols):
            symbol_base = 10.0 + idx * (download_span / symbols_count)
            symbol_span = download_span / symbols_count
            await reporter.report(symbol_base, f"скачиваю {symbol} {settings.base_interval} за {settings.days_back} дней")

            async def symbol_progress(symbol_pct: float, rows: int, expected: int, symbol_name: str = symbol) -> None:
                absolute_pct = symbol_base + symbol_span * (symbol_pct / 100.0)
                await reporter.report(absolute_pct, f"{symbol_name}: {rows:,}/{expected:,} свечей")

            df = await client.download_klines_dataframe(
                symbol,
                settings.base_interval,
                window,
                progress_cb=symbol_progress,
            )
            if df.empty:
                raise RuntimeError(f"No data downloaded for {symbol}")

            expected_rows = max(1, int((window.end_ms - window.start_ms) // interval_ms))
            coverage = len(df) / expected_rows
            actual_days_by_rows = len(df) * interval_ms / (24 * 60 * 60 * 1000)
            if coverage < settings.min_coverage_ratio:
                if actual_days_by_rows >= settings.min_effective_days:
                    msg = (
                        f"{symbol}: доступна неполная история в окне {settings.days_back}d: "
                        f"{len(df):,}/{expected_rows:,} свечей ({coverage:.1%}), "
                        f"примерно {actual_days_by_rows:.1f} дней. Продолжаю сбор: данных достаточно для scan/setup."
                    )
                    warnings.append(msg)
                    logger.warning(msg)
                    await reporter.report(symbol_base + symbol_span * 0.98, msg)
                else:
                    raise RuntimeError(
                        f"{symbol}: скачано слишком мало свечей: {len(df):,}/{expected_rows:,} ({coverage:.1%}), "
                        f"примерно {actual_days_by_rows:.1f} дней. Минимум для scan: {settings.min_effective_days:g} дней. "
                        f"Проверь symbol на MEXC Futures или увеличь паузу/повторы."
                    )

            out_file = candles_out / f"{symbol}_{settings.base_interval}_30d.parquet"
            save_dataframe_parquet(df, out_file)
            # Keep a local copy for debugging/reuse.
            save_dataframe_parquet(df, settings.candles_dir / out_file.name)
            row_counts[symbol] = len(df)
            candle_files[symbol] = str(Path("candles") / out_file.name)
            await reporter.report(symbol_base + symbol_span, f"{symbol}: свечи готовы, rows={len(df):,}")

        await reporter.report(74, "строю 5 графиков на актив")
        expected_charts = max(1, len(resolved_symbols) * 5)
        chart_done = 0

        async def chart_done_cb(rel_path: str) -> None:
            nonlocal chart_done
            chart_done += 1
            pct = 74 + min(charts_span, chart_done / expected_charts * charts_span)
            await reporter.report(pct, f"графики {chart_done}/{expected_charts}; последний: {rel_path}")

        for symbol in resolved_symbols:
            candle_path = candles_out / f"{symbol}_{settings.base_interval}_30d.parquet"
            files, warns = await _plot_scan_charts_for_symbol(symbol, candle_path, charts_out, logger, chart_done_cb)
            chart_files.extend(files)
            warnings.extend(warns)

        await reporter.report(95, "пишу manifest/task")
        task_text = _chatgpt_task_text(preset, created_msk)
        setup_format_text = _setup_format_text(preset)
        (build_dir / "task.txt").write_text(task_text + "\n", encoding="utf-8")
        (build_dir / "setup_format.txt").write_text(setup_format_text + "\n", encoding="utf-8")

        manifest = {
            "archive_type": "chatgpt_scan_30d_mexc_futures",
            "collector_version": settings.app_version,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "created_at_utc_plus_3_msk": created_msk,
            "telegram_archive_stamp_utc_plus_3": scan_stamp,
            "exchange": "MEXC_FUTURES_PUBLIC",
            "base_url": settings.mexc_base_url,
            "market_type": settings.mexc_market_type,
            "preset_key": preset.key,
            "preset_title": preset.title,
            "requested_symbols": preset.symbols,
            "symbols": resolved_symbols,
            "symbol_resolution": symbol_resolution,
            "symbol_policy": "exact_only_no_fallback: trade the exact listed symbol only",
            "base_interval": settings.base_interval,
            "days_back": settings.days_back,
            "download_window": window.as_dict(),
            "min_coverage_ratio": settings.min_coverage_ratio,
            "min_effective_days": settings.min_effective_days,
            "ping_ok": ping_ok,
            "server_time": server_time,
            "api_key_saved_mask": api_mask,
            "candle_files": candle_files,
            "row_counts": row_counts,
            "chart_files_count": len(chart_files),
            "chart_files": chart_files,
            "chart_set": {
                "1D": "available data inside requested 30d window",
                "4H": "available data inside requested 30d window",
                "1H": "available data inside requested 30d window",
                "15m": "last 7 days",
                "1m": "last 24 hours",
            },
            "warnings": warnings,
            "request_policy": {
                "endpoint": "/api/v1/contract/kline/{symbol}",
                "base_url": settings.mexc_base_url,
                "chunk_limit_1m_candles": 2000,
                "rate_limit_handling": "On HTTP/app-level too-frequent/rate-limit errors, increase request pause and retry.",
                "symbol_policy": "Exact symbols only. No automatic fallback/substitution.",
            },
            "instruction_files": ["task.txt", "setup_format.txt"],
            "answer_rule_for_chatgpt": "Return only ready setup using setup_format.txt. If no setup: wait, сейчас лучше не входить, подожди и пришли новый архив.",
        }
        write_json(build_dir / "manifest.json", manifest)
        write_json(meta_out / "exchange_info.json", exchange_info)
        write_json(meta_out / "api_status.json", {
            "mexc_futures_public_ping_ok": ping_ok,
            "mexc_server_time": server_time,
            "api_key_saved_mask": api_mask,
            "note": "Market data uses MEXC public futures endpoints. No place_order/cancel_order/trading endpoints exist in this bot.",
        })

        await reporter.report(98, "упаковываю zip")
        zip_path = settings.exports_dir / f"chatgpt_scan-{preset.key}-{scan_stamp}.zip"
        zip_directory(build_dir, zip_path)
        write_json(settings.exports_dir / f"chatgpt_scan-{preset.key}-{scan_stamp}.sha256.json", {
            "file": zip_path.name,
            "sha256": file_sha256(zip_path),
            "size_bytes": zip_path.stat().st_size,
            "size_human": human_bytes(zip_path.stat().st_size),
            "created_at_utc_plus_3_msk": created_msk,
        })
        logger.info("Scan archive ready: %s size=%s", zip_path, human_bytes(zip_path.stat().st_size))
        await reporter.report(100, f"архив готов: {zip_path.name}, размер={human_bytes(zip_path.stat().st_size)}, графиков={len(chart_files)}", force=True)
        return zip_path
    finally:
        await client.close()


def build_logs_archive(settings: Settings, logger: logging.Logger) -> Path:
    stamp = utc_stamp()
    build_dir = settings.work_dir / f"logs_build_{stamp}" / "log_full"
    safe_rmtree(build_dir)
    if settings.logs_dir.exists():
        shutil.copytree(settings.logs_dir, build_dir / "logs", dirs_exist_ok=True)
    export_index = []
    for file in sorted(settings.exports_dir.glob("*")):
        if file.is_file():
            export_index.append({
                "name": file.name,
                "size": human_bytes(file.stat().st_size),
                "mtime_utc": datetime.fromtimestamp(file.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
    write_json(build_dir / "export_index.json", {"exports": export_index})
    write_json(build_dir / "runtime_snapshot.json", {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_root": str(settings.data_root),
        "logs_size": human_bytes(dir_size_bytes(settings.logs_dir)),
        "exports_size": human_bytes(dir_size_bytes(settings.exports_dir)),
        "collector_version": settings.app_version,
    })
    zip_path = settings.exports_dir / f"log_full_{stamp}.zip"
    zip_directory(build_dir, zip_path)
    logger.info("Log archive ready: %s size=%s", zip_path, human_bytes(zip_path.stat().st_size))
    return zip_path
