from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

import pandas as pd

from config import Settings
from file_utils import utc_stamp, write_json, zip_directory, file_sha256, human_bytes, safe_rmtree, dir_size_bytes
from mexc import DownloadWindow, MexcSpotClient, save_dataframe_parquet, extract_fees_from_exchange_info
from charts import load_ohlcv, resample_ohlcv, _plot_candles
from security import SecretStore

ProgressCallback = Callable[[str], Awaitable[None]]


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


async def build_data_archive(
    settings: Settings,
    logger: logging.Logger,
    secret_store: SecretStore,
    progress_cb: ProgressCallback | None = None,
) -> Path:
    stamp = utc_stamp()
    build_dir = settings.work_dir / f"data_build_{stamp}" / "research_input_BTC_ETH_data"
    safe_rmtree(build_dir)
    candles_out = build_dir / "candles"
    meta_out = build_dir / "meta"
    candles_out.mkdir(parents=True, exist_ok=True)
    meta_out.mkdir(parents=True, exist_ok=True)

    reporter = PercentReporter("Parquet", progress_cb)
    api_mask = secret_store.load_mexc_api_mask()
    logger.info("Starting data archive build: market=%s symbols=%s days=%s", settings.mexc_market_type, settings.symbols, settings.days_back)
    await reporter.report(0, f"старт. Рынок={settings.mexc_market_type}, символы={settings.symbols}, период={settings.days_back} дней", force=True)

    client = MexcSpotClient(settings.mexc_base_url, logger, settings.mexc_market_type)
    try:
        await reporter.report(5, "проверяю Binance Spot public API")
        ping_ok = await client.ping()
        server_time = await client.server_time()
        interval_ms = 60_000
        if settings.base_interval in {"1m", "5m", "15m", "30m", "60m", "1h", "4h", "1d"}:
            from mexc import INTERVAL_MS
            interval_ms = INTERVAL_MS.get(settings.base_interval, 60_000)
        window = DownloadWindow.last_days_from_end_ms(settings.days_back, int(server_time["serverTime"]), interval_ms)
        exchange_info = await client.exchange_info(settings.symbols)
        fees = extract_fees_from_exchange_info(exchange_info)
        await reporter.report(10, "Binance Spot доступен, meta получена")

        row_counts: dict[str, int] = {}
        candle_files: dict[str, str] = {}
        symbols_count = max(1, len(settings.symbols))
        download_total_span = 72.0

        for idx, symbol in enumerate(settings.symbols):
            symbol_base = 10.0 + idx * (download_total_span / symbols_count)
            symbol_span = download_total_span / symbols_count
            await reporter.report(symbol_base, f"начинаю скачивать {symbol} {settings.base_interval}")

            async def symbol_progress(symbol_pct: float, rows: int, expected: int, symbol_name: str = symbol) -> None:
                absolute_pct = symbol_base + symbol_span * (symbol_pct / 100.0)
                await reporter.report(
                    absolute_pct,
                    f"{symbol_name}: скачано примерно {rows:,}/{expected:,} свечей",
                )

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
            if coverage < settings.min_coverage_ratio:
                raise RuntimeError(
                    f"{symbol}: скачано слишком мало свечей: {len(df):,}/{expected_rows:,} "
                    f"({coverage:.1%}). Это не полный 3-year архив. "
                    f"Текущий источник={settings.mexc_market_type}. Для 1m за 3 года нужен стабильный historical data источник; Binance Spot public klines должен отдавать полный период."
                )

            await reporter.report(symbol_base + symbol_span * 0.92, f"{symbol}: сохраняю Parquet")
            out_file = candles_out / f"{symbol}_{settings.base_interval}.parquet"
            save_dataframe_parquet(df, out_file)
            # Keep a local reusable copy outside the archive, so Charts can run without re-downloading.
            local_copy = settings.candles_dir / out_file.name
            save_dataframe_parquet(df, local_copy)
            row_counts[symbol] = len(df)
            candle_files[symbol] = str(Path("candles") / out_file.name)
            logger.info("Saved %s rows=%s size=%s", out_file, len(df), human_bytes(out_file.stat().st_size))
            await reporter.report(
                symbol_base + symbol_span,
                f"{symbol} готов, строк={len(df):,}, файл={human_bytes(out_file.stat().st_size)}",
            )

        await reporter.report(85, "пишу manifest/meta")
        write_json(meta_out / "exchange_info.json", exchange_info)
        write_json(meta_out / "fees.json", fees)
        write_json(meta_out / "api_status.json", {
            "binance_spot_public_ping_ok": ping_ok,
            "binance_server_time": server_time,
            "api_key_saved_mask": api_mask,
            "note": f"Market data uses Binance Spot public endpoints from {settings.mexc_base_url}. No futures endpoints and no trading/order endpoints exist in this bot. API key is optional and not used for candle download.",
        })

        manifest = {
            "archive_type": "research_input_BTC_ETH_data",
            "collector_version": settings.app_version,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "exchange": "BINANCE_SPOT_PUBLIC_HISTORY_FOR_RESEARCH",
            "market_type": settings.mexc_market_type,
            "base_url": settings.mexc_base_url,
            "symbols": settings.symbols,
            "base_interval": settings.base_interval,
            "days_back": settings.days_back,
            "download_window": window.as_dict(),
            "min_coverage_ratio": settings.min_coverage_ratio,
            "candle_files": candle_files,
            "row_counts": row_counts,
            "required_for_chatgpt_research": True,
            "contents": {
                "candles": "1m OHLCV parquet files for BTC/ETH for the requested 1095-day / 3-year window from Binance Spot public historical klines.",
                "meta/exchange_info.json": "Binance Spot public exchangeInfo symbol fields",
                "meta/fees.json": "fee placeholder/public fields; verify actual account/exchange fees before live trading",
                "meta/api_status.json": "public/API status and masked key info",
            },
            "progress_note": "Bot sends 0/10/20/.../100% Telegram updates during archive creation.",
            "next_step_for_chatgpt": "Upload this 3-year data archive first. Charts archive is optional but useful for visual context. Then ask to recheck NSM v2 on 3-year data.",
        }
        write_json(build_dir / "manifest.json", manifest)

        await reporter.report(92, "упаковываю zip")
        zip_path = settings.exports_dir / f"research_input_BTC_ETH_data_{stamp}.zip"
        zip_directory(build_dir, zip_path)
        write_json(settings.exports_dir / f"research_input_BTC_ETH_data_{stamp}.sha256.json", {
            "file": zip_path.name,
            "sha256": file_sha256(zip_path),
            "size_bytes": zip_path.stat().st_size,
            "size_human": human_bytes(zip_path.stat().st_size),
        })
        logger.info("Data archive ready: %s size=%s", zip_path, human_bytes(zip_path.stat().st_size))
        await reporter.report(100, f"архив готов: {zip_path.name}, размер={human_bytes(zip_path.stat().st_size)}", force=True)
        return zip_path
    finally:
        await client.close()


async def _make_charts_for_symbol_with_progress(
    symbol: str,
    candle_path: Path,
    out_root: Path,
    logger: logging.Logger,
    chart_done_cb: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[list[str], list[str]]:
    chart_files: list[str] = []
    warnings: list[str] = []

    df_1m = await asyncio.to_thread(load_ohlcv, candle_path)
    if df_1m.empty:
        raise RuntimeError(f"No candle data in {candle_path}")

    latest_ts = df_1m.index.max()

    async def plot(df: pd.DataFrame, title: str, output: Path, figsize=(16, 8), mav=(20, 50)) -> None:
        await asyncio.to_thread(_plot_candles, df, title, output, figsize, mav)
        rel = str(output.relative_to(out_root.parent))
        chart_files.append(rel)
        if chart_done_cb:
            await chart_done_cb(rel)

    # 1D full 3-year window: readable, ~1095 candles.
    df_1d = resample_ohlcv(df_1m, "1d")
    p = out_root / "overview" / f"{symbol}_1D_full_3y.png"
    await plot(df_1d, f"{symbol} 1D full 3 years", p, figsize=(18, 9), mav=(20, 50, 200))

    # 4H monthly: one readable chart per month, last 36 months in data.
    df_4h = resample_ohlcv(df_1m, "4h")
    months = sorted(df_4h.index.to_period("M").unique())[-36:]
    for month in months:
        month_df = df_4h[df_4h.index.to_period("M") == month]
        if len(month_df) < 5:
            continue
        p = out_root / "monthly_4h" / f"{symbol}_4H_{month}.png"
        await plot(month_df, f"{symbol} 4H {month}", p, figsize=(16, 8), mav=(20, 50))

    # 1H last 180 days, grouped by month.
    df_1h = resample_ohlcv(df_1m, "1h")
    recent_1h = df_1h[df_1h.index >= latest_ts - pd.Timedelta(days=180)]
    recent_months = sorted(recent_1h.index.to_period("M").unique())
    for month in recent_months:
        month_df = recent_1h[recent_1h.index.to_period("M") == month]
        if len(month_df) < 24:
            continue
        p = out_root / "monthly_1h_recent" / f"{symbol}_1H_{month}.png"
        await plot(month_df, f"{symbol} 1H recent {month}", p, figsize=(18, 9), mav=(20, 50))

    # 15m last 56 days, eight weekly chunks.
    df_15m = resample_ohlcv(df_1m, "15min")
    start_recent = latest_ts - pd.Timedelta(days=56)
    recent_15m = df_15m[df_15m.index >= start_recent]
    for i in range(8):
        start = start_recent + pd.Timedelta(days=7 * i)
        end = start + pd.Timedelta(days=7)
        chunk = recent_15m[(recent_15m.index >= start) & (recent_15m.index < end)]
        if len(chunk) < 24:
            warnings.append(f"{symbol} 15m week {i+1}: too few rows")
            continue
        p = out_root / "weekly_15m_recent" / f"{symbol}_15m_week_{i+1}.png"
        title = f"{symbol} 15m week {i+1}: {start.date()} to {end.date()}"
        await plot(chunk, title, p, figsize=(18, 9), mav=(20, 50))

    logger.info("Created %s chart files for %s", len(chart_files), symbol)
    return chart_files, warnings


async def build_charts_archive(
    settings: Settings,
    logger: logging.Logger,
    progress_cb: ProgressCallback | None = None,
) -> Path:
    stamp = utc_stamp()
    build_root = settings.work_dir / f"charts_build_{stamp}" / "research_input_BTC_ETH_charts"
    charts_out = build_root / "charts"
    safe_rmtree(build_root)
    charts_out.mkdir(parents=True, exist_ok=True)

    reporter = PercentReporter("Charts", progress_cb)
    logger.info("Starting charts archive build")
    await reporter.report(0, "старт. Использую локальные Parquet из storage/candles", force=True)

    missing = []
    for symbol in settings.symbols:
        candle_path = settings.candles_dir / f"{symbol}_{settings.base_interval}.parquet"
        if not candle_path.exists():
            missing.append(str(candle_path))
    if missing:
        raise RuntimeError(
            "Не найдены Parquet-файлы. Сначала нажми Parquet. Missing: " + ", ".join(missing)
        )
    await reporter.report(10, "Parquet файлы найдены, начинаю рендер графиков")

    chart_files: list[str] = []
    warnings: list[str] = []
    expected_charts = max(1, len(settings.symbols) * 52)  # 1D + 36 monthly 4H + ~7 monthly 1H + 8 weekly 15m.
    chart_done = 0

    async def chart_done_cb(rel_path: str) -> None:
        nonlocal chart_done
        chart_done += 1
        pct = 10 + min(80, chart_done / expected_charts * 80)
        await reporter.report(pct, f"отрисовано {chart_done} графиков; последний: {rel_path}")

    for symbol in settings.symbols:
        candle_path = settings.candles_dir / f"{symbol}_{settings.base_interval}.parquet"
        await reporter.report(10 + min(80, chart_done / expected_charts * 80), f"обрабатываю {symbol}")
        symbol_files, symbol_warnings = await _make_charts_for_symbol_with_progress(
            symbol,
            candle_path,
            charts_out,
            logger,
            chart_done_cb,
        )
        chart_files.extend(symbol_files)
        warnings.extend(symbol_warnings)

    await reporter.report(92, "пишу manifest")
    manifest = {
        "archive_type": "research_input_BTC_ETH_charts",
        "collector_version": settings.app_version,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": settings.symbols,
        "source_parquet_interval": settings.base_interval,
        "chart_set": {
            "overview": "1D full 3-year window per symbol",
            "monthly_4h": "4H charts for last 36 months per symbol",
            "monthly_1h_recent": "1H charts for recent ~180 days grouped by month",
            "weekly_15m_recent": "15m charts for recent 56 days split into eight weekly windows",
        },
        "chart_files_count": len(chart_files),
        "chart_files": chart_files,
        "warnings": warnings,
        "progress_note": "Bot sends 0/10/20/.../100% Telegram updates during chart rendering.",
        "note": "Charts are visual context only. Parquet data archive is the main research input.",
    }
    write_json(build_root / "manifest.json", manifest)

    await reporter.report(96, "упаковываю zip")
    zip_path = settings.exports_dir / f"research_input_BTC_ETH_charts_{stamp}.zip"
    zip_directory(build_root, zip_path)
    write_json(settings.exports_dir / f"research_input_BTC_ETH_charts_{stamp}.sha256.json", {
        "file": zip_path.name,
        "sha256": file_sha256(zip_path),
        "size_bytes": zip_path.stat().st_size,
        "size_human": human_bytes(zip_path.stat().st_size),
        "chart_files_count": len(chart_files),
    })
    logger.info("Charts archive ready: %s size=%s files=%s", zip_path, human_bytes(zip_path.stat().st_size), len(chart_files))
    await reporter.report(100, f"архив готов: {zip_path.name}, размер={human_bytes(zip_path.stat().st_size)}, графиков={len(chart_files)}", force=True)
    return zip_path


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
    })
    zip_path = settings.exports_dir / f"log_full_{stamp}.zip"
    zip_directory(build_dir, zip_path)
    logger.info("Log archive ready: %s size=%s", zip_path, human_bytes(zip_path.stat().st_size))
    return zip_path
