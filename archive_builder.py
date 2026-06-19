from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Awaitable

import pandas as pd

from charts import load_ohlcv, resample_ohlcv, _plot_candles
from config import SYMBOL_CANDIDATES, ScanPreset, Settings
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
    "gold": "Gold",
    "btc": "BTC",
    "eth": "ETH",
    "silver": "Silver",
    "oil": "Oil",
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
        key = _asset_key_for_symbol(preset.symbols[0])
        if key:
            return ASSET_LABELS[key]
        return preset.symbols[0]
    return preset.title


def _exact_candidates_for_symbol(symbol: str) -> list[str]:
    key = _asset_key_for_symbol(symbol)
    if key and key in SYMBOL_CANDIDATES:
        # Exact-only: do not substitute XAU with XAUT or WTI with Brent.
        return list(dict.fromkeys(SYMBOL_CANDIDATES[key]))
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


def _chatgpt_task_text(preset: ScanPreset, created_msk: str) -> str:
    assets = ", ".join(preset.symbols)
    asset_label = _asset_label_for_preset(preset)
    if len(preset.symbols) == 1:
        return f"""TASK:
Analyze this MEXC Futures scan archive using Elite 5 Rejection / Rostislav-style.

Archive created: {created_msk} UTC+3/MSK
Assets: {assets}
Data: 1m OHLC for the last 30 days, plus 5 charts per asset: 1D, 4H, 1H, 15m, 1m.

Return ONLY the ready setup, no extra explanation.

Required short format:
Setup {asset_label}:
Маркет - <use only if A+; otherwise пропускаем, нет сетапа A+>
Лимит <price> <long/short>
TP1: <price>
TP2: <price>
TP3: <price>
SL: <price>
Убрать лимит: <clear cancellation rule>

Rules:
- Do not chase price.
- No market after a strong move unless the setup is A+.
- Prefer SHORT only from pullback if 1H/4H are weak.
- LONG is usually SOFT only after a strong dump into a lower zone with reaction.
- If no clean setup exists, answer exactly:
wait, сейчас лучше не входить, подожди и пришли новый архив.
""".strip()

    return f"""TASK:
Analyze this MEXC Futures multi-asset scan archive using Elite 5 Rejection / Rostislav-style.

Archive created: {created_msk} UTC+3/MSK
Assets: {assets}
Data: 1m OHLC for the last 30 days, plus 5 charts per asset: 1D, 4H, 1H, 15m, 1m.

Return ONLY 5 ready setups, no extra explanation:
Setup Gold:
...
Setup BTC:
...
Setup ETH:
...
Setup Silver:
...
Setup Oil:
...

Required short format for each asset:
Маркет - <use only if A+; otherwise пропускаем, нет сетапа A+>
Лимит <price> <long/short>
TP1: <price>
TP2: <price>
TP3: <price>
SL: <price>
Убрать лимит: <clear cancellation rule>

Rules:
- Do not chase price.
- No market after a strong move unless the setup is A+.
- Prefer SHORT only from pullback if 1H/4H are weak.
- LONG is usually SOFT only after a strong dump into a lower zone with reaction.
- If one asset has no clean setup, write WAIT for that asset.
- If no clean setup exists on all 5 assets, answer exactly:
wait, сейчас лучше не входить, подожди и пришли новый архив.
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
    await plot(df_1d, f"{symbol} 1D — last 30 days", charts_out / symbol / f"{symbol}_1D.png", figsize=(18, 9), mav=(7, 20))

    df_4h = resample_ohlcv(df_1m, "4h")
    await plot(df_4h, f"{symbol} 4H — last 30 days", charts_out / symbol / f"{symbol}_4H.png", figsize=(18, 9), mav=(20, 50))

    df_1h = resample_ohlcv(df_1m, "1h")
    await plot(df_1h, f"{symbol} 1H — last 30 days", charts_out / symbol / f"{symbol}_1H.png", figsize=(18, 9), mav=(20, 50, 200))

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
        (build_dir / "task.txt").write_text(task_text + "\n", encoding="utf-8")

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
            "answer_rule_for_chatgpt": "Return only ready setup. If no setup: wait, сейчас лучше не входить, подожди и пришли новый архив.",
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
