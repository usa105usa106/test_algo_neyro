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
Archive created: {created_msk} UTC+3/MSK
Symbols: {symbols}

Analyze ONLY this archive data: montage, CSV candles, report.json/status/manifest. Do not use previous conversation memory, previous archive conclusions, old setups, or assumptions from other scans.
This is only Intraday mode. Do not use old standard scan / A+ Hunter / montage task rules.

Answer in Russian, briefly, without extra talk.
Final answer format:
- Start with either **WAIT — observation only, no entry** or **Intraday A**.
- If WAIT, still give an observation setup/zone, but clearly mark that there is no entry now.
- If Intraday A, give only one real tradable setup from the archive. Maximum one real setup per archive.
- Use LIMIT only. No MARKET. If MARKET would be needed, answer WAIT / missed.
- Write Entry/Limit, Stop, TP1, TP2, TP3 numbers in bold.
- If a zone is a range, give one exact midpoint number. Example: entry zone 4070-4080 => **4075**. TP range 4060-4050 => TP1 **4055**.
- End with only 1-3 short sentences explaining why.

Decision rules:
- Real trade only if clean Intraday A: clear regime + clear location + confirmation/rejection/hold + acceptable RR + no obvious stop magnet.
- B / B+ / A- are WAIT only, not tradable.
- If confirmation is missing, answer **WAIT — observation only, no entry** and write WAIT_CONFIRMATION.
- Do not place passive limit orders just because price is near a zone. Need 5m/15m rejection/hold or sweep confirmation.
- Reject if report/montage/CSV materially disagree or DATA_WARNING exists.
- Reject if trend just flipped or is transitional.
- Do not chase after impulse. If target/low/high was reached before entry, setup is missed.

Stop and RR rules:
- Do not give micro-invalidation unless the intraday_task explicitly says micro scalp. This task is not micro scalp.
- SL must be structural: beyond day high/low, 24h high/low, nearest liquidity magnet, and obvious sweep zone.
- It is forbidden to place SL inside an obvious magnet. If nearby high/low zone is 4088-4090, SL cannot be 4085-4088; SL must be above the zone, e.g. 4091-4095.
- If structural SL makes RR bad, do not force the trade; answer WAIT / observation only.
- TP must be calculated from the real SL risk. Wide SL requires wider TP. Micro-TPs with a wide SL are forbidden.
- Before final answer, check: is SL beyond structure? do TPs match real risk? If not, answer WAIT / observation only.

Compact template:
**WAIT — observation only, no entry** / **Intraday A**
Setup <symbol>: LONG LIMIT / SHORT LIMIT / WAIT_CONFIRMATION
Entry: **...**
Stop: **...**
TP1: **...** — 33% + BE
TP2: **...** — 33% + BE
TP3: **...** — остаток
Cancel: ...
Why: 1-3 short sentences.
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
        "answer_rule_for_chatgpt": "Use only this archive data. Brief Russian answer. Maximum one real setup per archive, Intraday A only; B/B+/A- => WAIT observation only. LIMIT only. Bold Entry/Stop/TP numbers. Use midpoint for ranges. Require 5m/15m rejection/hold. SL must be structural beyond day/24h high-low and liquidity magnets; no micro-invalidation. If structural SL ruins RR, answer WAIT.",
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
