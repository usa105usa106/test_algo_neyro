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


def _one_full_15m_deadline_msk(created_msk: str) -> str:
    created = datetime.strptime(created_msk, "%Y-%m-%d %H:%M:%S")
    boundary = created.replace(second=0, microsecond=0)
    boundary += timedelta(minutes=(-boundary.minute) % 15)
    if created > boundary:
        boundary += timedelta(minutes=15)
    # At an exact boundary the candle starting now is the complete validity candle;
    # otherwise use the next complete candle. Validity remains 15–30 minutes.
    valid_until = boundary + timedelta(minutes=15)
    return valid_until.strftime("%Y-%m-%d %H:%M MSK")


def _task_text(created_msk: str, candidates: list[IntradayReport]) -> str:
    symbols = ", ".join(r.symbol for r in candidates)
    valid_until = _one_full_15m_deadline_msk(created_msk)
    return f"""INTRADAY_TASK 58_full
Archive created: {created_msk} UTC+3/MSK
Candidates: {symbols}
LIMIT validity deadline after one full 15m candle: {valid_until}

Use ONLY this archive. Choose maximum ONE setup.

1. A candidate with report.decision=MANUAL_REVIEW is already confirmed by the deterministic engine on CLOSED candles. Do not demand another confirmation unless archive data directly contradicts the report.
2. Return WAIT only for a concrete reason: stale/missing data, report/CSV contradiction, price already moved 0.60R from LIMIT toward TP1 before the answer/order, TP1 already touched before LIMIT fill, stop already invalidated, impossible prices, or the last CLOSED 15m candle explicitly reverses the setup.
3. LIMIT only. Never MARKET. The LIMIT gets one complete 15m candle after publication and is valid only until {valid_until} (15–30 minutes depending on archive time). If it has not filled by then, cancel it.
4. Before fill, cancel immediately if price travels 0.60R or more from Entry toward TP1. That setup is MISSED; do not reuse the old Entry after a return.
5. On a later Intraday scan, cancel the old pending LIMIT if the symbol is no longer MANUAL_REVIEW in the same direction/playbook, becomes WAIT/TRANSITION/NO_DATA, is missing from the completed scan, or a materially rebuilt Entry/Stop replaces it. A new order requires a fresh archive/setup.
6. Use suggested_entry, suggested_structural_stop and suggested_tp1/2/3 exactly, rounded only to price_tick. Never tighten the stop.
7. Any exact MEXC Futures symbol added with `int ...` may produce LONG or SHORT; there is no fixed Intraday whitelist. Choose direction only from the current CLOSED-candle regime, pressure, structure and confirmation in report.json. The 30d archive is context, not a permanent bull/bear bias; never apply a per-asset direction ban.
8. Trend plan: LIMIT is 0.15 ATR15 deeper than VWAP; stop is beyond 5 hours of CLOSED 15m structure, minimum 2.30 ATR15 for crypto/alts or 2.40 ATR15 for XAU/USOIL, maximum 4.00 ATR15. Trend local-room safety floor is deliberately modest at 0.12R, not 0.80R, to avoid strangling frequency. Targets remain 0.80R / 1.60R / 2.40R.
9. At TP1 close 33%. Move the remainder to BE only after a CLOSED 15m candle beyond TP1 or after TP2. At TP2 close 33%; TP3 closes the remainder.

Format:
**Intraday A** / **WAIT — observation only, no entry**
Setup <symbol>: LONG LIMIT / SHORT LIMIT / WAIT_CONFIRMATION
Entry: **...**
Stop: **...**
TP1: **...** — 33%
TP2: **...** — 33%
TP3: **...** — remainder
Valid until: **{valid_until}**
Cancel: no fill by deadline; before fill price moves >=0.60R toward TP1; later scan becomes WAIT/TRANSITION/NO_DATA/missing/opposite/rebuilt.
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
) -> tuple[Path | None, list[IntradayReport], float | None]:
    if not candidates:
        return None, [], None

    candidates = sorted(candidates, key=lambda r: (-int(getattr(r, "quality_score", 0) or 0), str(getattr(r, "symbol", ""))))

    utc_build_stamp = utc_stamp()
    chart_created_msk = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
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
            rel = create_montage_for_symbol(symbol, df_1m, charts_out, chart_created_msk, task_hint, logger)
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
        return None, [], None
    candidates = included_candidates

    # User-facing archive stamp is taken at the end of archive creation, right before manifest+zip.
    # One green symbol -> intraday_btc-HHMM_DDMM.zip; 2+ green symbols -> intraday_multi-HHMM_DDMM.zip.
    finished_stamp = moscow_scan_stamp()
    if len(candidates) == 1:
        base = (candidates[0].symbol or "one").upper().replace("_USDT", "").replace("-", "_").lower()
        prefix = f"intraday_{base}"
    else:
        prefix = "intraday_multi"

    # Use one publication timestamp for both the task deadline and runtime pending
    # state. The old code recomputed expiry after Telegram send, which could cross a
    # 15m boundary and leave the task and cancellation monitor with different times.
    publication_utc = datetime.now(timezone.utc)
    publication_msk = (publication_utc + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    valid_until_msk = _one_full_15m_deadline_msk(publication_msk)
    valid_until_naive = datetime.strptime(valid_until_msk.replace(" MSK", ""), "%Y-%m-%d %H:%M")
    valid_until_epoch = (valid_until_naive - timedelta(hours=3)).replace(tzinfo=timezone.utc).timestamp()

    (build_dir / "intraday_task.txt").write_text(_task_text(publication_msk, candidates) + "\n", encoding="utf-8")
    (build_dir / "status.txt").write_text(_report_text(candidates) + "\n", encoding="utf-8")
    manifest = {
        "archive_type": "intraday_candidates_manual_review",
        "collector_version": settings.app_version,
        "created_at_utc": publication_utc.isoformat(),
        "created_at_utc_plus_3_msk": publication_msk,
        "limit_valid_until_utc_plus_3_msk": valid_until_msk,
        "limit_valid_until_epoch_utc": valid_until_epoch,
        "chart_created_at_utc_plus_3_msk": chart_created_msk,
        "telegram_archive_stamp_utc_plus_3": finished_stamp,
        "symbols": [r.symbol for r in candidates],
        "candidate_count": len(candidates),
        "candidates": [_json_safe(r.as_dict()) for r in candidates],
        "chart_files": chart_files,
        "candle_files": candle_files,
        "instruction_files": ["intraday_task.txt", "status.txt", "reports/*/report.json"],
        "answer_rule_for_chatgpt": "Use only archive data. Maximum one setup. MANUAL_REVIEW is already confirmed on closed candles. LIMIT only and valid for only one complete 15m candle after publication (15–30 minutes). Before fill, cancel if price moves 0.60R toward TP1; never reuse a missed/expired old Entry. Cancel on a later Intraday WAIT/TRANSITION/NO_DATA/missing/opposite/materially rebuilt setup. Use suggested Entry/Stop/TP exactly and never tighten structural stop. Any exact MEXC Futures symbol added with `int ...` may produce LONG or SHORT; there is no fixed Intraday whitelist. Trend local-room floor is a modest 0.12R; stops remain 2.30/2.40 ATR minimum and 4.00 ATR maximum; targets remain 0.80/1.60/2.40R.",
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
        return zip_path, candidates, valid_until_epoch
    finally:
        shutil.rmtree(build_dir.parent, ignore_errors=True)
