from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from charts import create_montage_for_symbol
from config import Settings
from file_utils import file_sha256, human_bytes, moscow_scan_stamp, safe_rmtree, utc_stamp, write_json, zip_directory
from intraday_engine import IntradayReport, resample_ohlcv


def _task_text(created_msk: str, candidates: list[IntradayReport]) -> str:
    symbols = ", ".join(r.symbol for r in candidates)
    return f"""INTRADAY_TASK:
Analyze this archive as INTRADAY MANUAL_REVIEW candidates.
Archive created: {created_msk} UTC+3/MSK
Symbols: {symbols}

IMPORTANT:
- This is not old standard scan mode, not montage swing mode, not A+ Hunter.
- Intraday uses fresh downloaded candles for the scan; no parquet/cache assumptions.
- Use the bot report only as a first filter. Confirm manually from montage 1m/15m/1h/4h/1D.
- Data sanity is mandatory: compare report.json levels with montage/CSV. If DATA_WARNING exists or chart/data levels disagree materially, do not give a trade.
- Candidates in this archive are already sorted by bot quality_score, strongest first, but you must re-check them manually.
- In the final answer, always rank setups by your own intraday strength: best setup first, then weaker setups in descending order.
- If the archive order and your manual ranking differ, explicitly say which setup is strongest and why.
- Do not force a trade. If the candidate is weak, answer WAIT / NO TRADE.
- MAXIMUM ONE real tradable setup per archive. If there is no clean Intraday A, say no trade.
- B / B+ / A- are NOT tradable classes. They must be WAIT / observation only.
- A real setup requires Intraday A quality only: clear regime, clear location, confirmation, acceptable RR, and no obvious stop magnet.
- Entry must be LIMIT only. If only MARKET would work, answer WAIT / missed entry.
- Do NOT give passive limit orders only because price is near a zone. If confirmation is missing, answer WAIT_CONFIRMATION.
- Entry must be based on location plus confirmation: pullback rejection, sweep confirmation, or range-edge rejection/hold.
- Do not chase after impulse.
- Avoid long near 24h high after pump and short near 24h low after dump.
- Stop must be outside structure/noise, not inside normal 15m noise.
- Reject setups where the stop is immediately behind an obvious high/low/liquidity magnet. Use a wider structural stop, or answer WAIT if RR becomes bad.
- If the regime recently flipped (TREND_LONG ↔ TREND_SHORT) or looks transitional, answer WAIT until a stable follow-up scan confirms it.

Return in Russian.
For each candidate, give one of:
1) at most one precise Intraday A setup with Entry / Stop / TP1 / TP2 / TP3 / cancellation; or
2) WAIT_CONFIRMATION if a zone is interesting but needs 5m/15m rejection/hold; or
3) WAIT / NO_TRADE with short reason.
Start with the strongest valid intraday setup. If no valid Intraday A exists, say WAIT / NO_TRADE and rank the rejects by closest-to-valid first.
""".strip()


def _report_text(reports: list[IntradayReport]) -> str:
    lines = []
    for r in reports:
        lines.append(r.details_text())
        lines.append("")
    return "\n".join(lines).strip()


def _write_frame_csv(path: Path, df: pd.DataFrame, max_rows: int = 2000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.tail(max_rows).copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out = out.reset_index().rename(columns={"index": "datetime_utc"})
    out.to_csv(path, index=False)


def build_intraday_candidates_archive(
    settings: Settings,
    logger: logging.Logger,
    candidates: list[IntradayReport],
    data_by_symbol: dict[str, dict[str, Any]],
) -> Path | None:
    if not candidates:
        return None

    candidates = sorted(candidates, key=lambda r: (-int(getattr(r, "quality_score", 0) or 0), str(getattr(r, "symbol", ""))))

    utc_build_stamp = utc_stamp()
    created_msk = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    build_dir = settings.work_dir / f"intraday_build_{utc_build_stamp}" / "intraday_candidates"
    safe_rmtree(build_dir)
    charts_out = build_dir / "charts"
    reports_out = build_dir / "reports"
    data_out = build_dir / "data"
    charts_out.mkdir(parents=True, exist_ok=True)
    reports_out.mkdir(parents=True, exist_ok=True)
    data_out.mkdir(parents=True, exist_ok=True)

    chart_files: list[str] = []
    candle_files: dict[str, dict[str, str]] = {}
    included_candidates: list[IntradayReport] = []
    task_hint = "Intraday manual review: regime / pressure / location. Confirm or reject setup."

    for report in candidates:
        symbol = report.symbol
        try:
            payload = data_by_symbol.get(symbol) or {}
            df_1m = payload.get("df_1m")
            frames = payload.get("frames") or {}
            if df_1m is None or df_1m.empty:
                logger.warning("Intraday archive skip empty df for %s", symbol)
                continue
            rel = create_montage_for_symbol(symbol, df_1m, charts_out, created_msk, task_hint, logger)
            chart_files.append(rel)
            write_json(reports_out / symbol / "report.json", report.as_dict())
            (reports_out / symbol / "report.txt").write_text(report.details_text() + "\n", encoding="utf-8")

            symbol_files: dict[str, str] = {}
            for tf, frame in {
                "1m_last24h": frames.get("1m", df_1m.tail(1440)),
                "15m": frames.get("15m", resample_ohlcv(df_1m, "15min")),
                "1h": frames.get("1h", resample_ohlcv(df_1m, "1h")),
                "4h": frames.get("4h", resample_ohlcv(df_1m, "4h")),
                "1D": frames.get("1D", resample_ohlcv(df_1m, "1d")),
            }.items():
                out = data_out / symbol / f"{symbol}_{tf}.csv"
                _write_frame_csv(out, frame, max_rows=2000)
                symbol_files[tf] = str(out.relative_to(build_dir))
            candle_files[symbol] = symbol_files
            included_candidates.append(report)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Intraday archive skip symbol due build error symbol=%s: %s", symbol, exc)
            continue

    if not chart_files or not included_candidates:
        return None
    candidates = included_candidates

    # User-facing archive stamp is taken at the end of archive creation, right before manifest+zip.
    # One green symbol -> intraday_btc-HHMM_DDMM.zip; 2+ green symbols -> intraday_multi-HHMM_DDMM.zip.
    finished_stamp = moscow_scan_stamp()
    if len(candidates) == 1:
        base = (candidates[0].symbol or "one").upper().replace("_USDT", "").replace("-", "_").lower()
        prefix = f"intraday_{base}"
    else:
        prefix = "intraday_multi"

    (build_dir / "intraday_task.txt").write_text(_task_text(created_msk, candidates) + "\n", encoding="utf-8")
    (build_dir / "status.txt").write_text(_report_text(candidates) + "\n", encoding="utf-8")
    manifest = {
        "archive_type": "intraday_candidates_manual_review",
        "collector_version": settings.app_version,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "created_at_utc_plus_3_msk": created_msk,
        "telegram_archive_stamp_utc_plus_3": finished_stamp,
        "symbols": [r.symbol for r in candidates],
        "candidate_count": len(candidates),
        "candidates": [r.as_dict() for r in candidates],
        "chart_files": chart_files,
        "candle_files": candle_files,
        "instruction_files": ["intraday_task.txt", "status.txt", "reports/*/report.json"],
        "answer_rule_for_chatgpt": "Confirm or reject intraday candidates. Maximum one real setup per archive, Intraday A only. B/B+/A- => WAIT. LIMIT entries only. Require confirmation/rejection and reject obvious stop magnets.",
        "storage_policy": "One zip per scan with all green MANUAL_REVIEW candidates only. Fresh 30d download in memory; no parquet/cache is used by Intraday.",
    }
    write_json(build_dir / "manifest.json", manifest)

    zip_path = settings.exports_dir / f"{prefix}-{finished_stamp}.zip"
    zip_directory(build_dir, zip_path)
    write_json(settings.exports_dir / f"{prefix}-{finished_stamp}.sha256.json", {
        "file": zip_path.name,
        "sha256": file_sha256(zip_path),
        "size_bytes": zip_path.stat().st_size,
        "size_human": human_bytes(zip_path.stat().st_size),
        "created_at_utc_plus_3_msk": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
    })
    logger.info("Intraday candidates archive ready: %s size=%s candidates=%s", zip_path, human_bytes(zip_path.stat().st_size), [r.symbol for r in candidates])
    return zip_path
