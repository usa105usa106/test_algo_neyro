from __future__ import annotations

import logging
import math
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from charts import create_montage_for_symbol
from config import Settings
from file_utils import file_sha256, human_bytes, moscow_scan_stamp, safe_rmtree, utc_stamp, write_json, zip_directory
from intraday_engine import IntradayReport, resample_ohlcv


def _json_safe(value: Any) -> Any:
    """Return strict-JSON data for Intraday reports (NaN/Inf -> null)."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    # numpy/pandas scalar support without adding a hard dependency here.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:  # noqa: BLE001
            pass
    return value


def _task_text(created_msk: str, candidates: list[IntradayReport]) -> str:
    symbols = ", ".join(r.symbol for r in candidates)
    return f"""INTRADAY_TASK 55_full
Archive created: {created_msk} UTC+3/MSK
Candidates: {symbols}

Use ONLY this archive. Choose maximum ONE setup.

1. A candidate with report.decision=MANUAL_REVIEW is already confirmed by the deterministic engine on CLOSED candles. Do not demand another confirmation unless archive data directly contradicts the report.
2. Return WAIT only for a concrete reason: stale/missing data, report/CSV contradiction, TP1 already touched before LIMIT fill, stop already invalidated, impossible prices, or the last CLOSED 15m candle explicitly reverses the setup.
3. LIMIT only. Never MARKET.
4. Use suggested_entry, suggested_structural_stop and suggested_tp1/2/3 exactly, rounded only to price_tick. Never tighten the stop.
5. Every supported asset may produce LONG or SHORT. Choose direction only from the current CLOSED-candle regime, pressure, structure and confirmation in report.json. The 30d archive is context, not a permanent bull/bear bias; never apply a per-asset direction ban.
6. Trend plan: LIMIT is 0.15 ATR15 deeper than VWAP; stop is beyond 5 hours of CLOSED 15m structure, minimum 2.30 ATR15 for crypto/alts or 2.40 ATR15 for XAU/USOIL, maximum 4.00 ATR15. Targets are 0.80R / 1.60R / 2.40R.
7. At TP1 close 33%. Move the remainder to BE only after a CLOSED 15m candle beyond TP1 or after TP2. At TP2 close 33%; TP3 closes the remainder.

Format:
**Intraday A** / **WAIT — observation only, no entry**
Setup <symbol>: LONG LIMIT / SHORT LIMIT / WAIT_CONFIRMATION
Entry: **...**
Stop: **...**
TP1: **...** — 33%
TP2: **...** — 33%
TP3: **...** — remainder
Cancel: ...
Why: 1–3 short sentences.
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
) -> tuple[Path | None, list[IntradayReport]]:
    if not candidates:
        return None, []

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
            write_json(reports_out / symbol / "report.json", _json_safe(report.as_dict()))
            (reports_out / symbol / "report.txt").write_text(report.details_text() + "\n", encoding="utf-8")

            symbol_files: dict[str, str] = {}
            export_frames = {
                "1m_last24h": frames["1m"] if "1m" in frames else df_1m.tail(1440),
                "15m_closed": frames["15m_closed"] if "15m_closed" in frames else (frames["15m"] if "15m" in frames else resample_ohlcv(df_1m, "15min")),
                "1h_closed": frames["1h_closed"] if "1h_closed" in frames else (frames["1h"] if "1h" in frames else resample_ohlcv(df_1m, "1h")),
                "4h_closed": frames["4h_closed"] if "4h_closed" in frames else (frames["4h"] if "4h" in frames else resample_ohlcv(df_1m, "4h")),
                "1D_closed": frames["1D_closed"] if "1D_closed" in frames else (frames["1D"] if "1D" in frames else resample_ohlcv(df_1m, "1d")),
            }
            for tf, frame in export_frames.items():
                out = data_out / symbol / f"{symbol}_{tf}.csv"
                _write_frame_csv(out, frame, max_rows=2000)
                symbol_files[tf] = str(out.relative_to(build_dir))
            candle_files[symbol] = symbol_files
            included_candidates.append(report)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Intraday archive skip symbol due build error symbol=%s: %s", symbol, exc)
            continue

    if not chart_files or not included_candidates:
        shutil.rmtree(build_dir.parent, ignore_errors=True)
        return None, []
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
        "candidates": [_json_safe(r.as_dict()) for r in candidates],
        "chart_files": chart_files,
        "candle_files": candle_files,
        "instruction_files": ["intraday_task.txt", "status.txt", "reports/*/report.json"],
        "answer_rule_for_chatgpt": "Use only archive data. Maximum one setup. MANUAL_REVIEW is already confirmed on closed candles; WAIT only for concrete invalidation or data contradiction. LIMIT only. Use suggested Entry/Stop/TP exactly and never tighten structural stop. Every supported asset may produce LONG or SHORT from current closed-candle conditions. The 30d archive is context, not a permanent bull/bear bias; no per-asset direction bans. Sweep/Range remain independently available. Trend entry 0.15 ATR deeper than VWAP, stop beyond 5h closed 15m structure, 2.30/2.40 ATR minimum and 4.00 ATR maximum, targets 0.80/1.60/2.40R. TP1 33%; BE only after closed 15m beyond TP1 or TP2.",
        "storage_policy": "One zip per scan with all green MANUAL_REVIEW candidates only. Fresh 30d download in memory; no parquet/cache is used by Intraday.",
    }
    write_json(build_dir / "manifest.json", _json_safe(manifest))

    zip_path = settings.exports_dir / f"{prefix}-{finished_stamp}.zip"
    try:
        zip_directory(build_dir, zip_path)
        write_json(settings.exports_dir / f"{prefix}-{finished_stamp}.sha256.json", {
            "file": zip_path.name,
            "sha256": file_sha256(zip_path),
            "size_bytes": zip_path.stat().st_size,
            "size_human": human_bytes(zip_path.stat().st_size),
            "created_at_utc_plus_3_msk": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
        })
        logger.info("Intraday candidates archive ready: %s size=%s candidates=%s", zip_path, human_bytes(zip_path.stat().st_size), [r.symbol for r in candidates])
        return zip_path, candidates
    finally:
        shutil.rmtree(build_dir.parent, ignore_errors=True)
