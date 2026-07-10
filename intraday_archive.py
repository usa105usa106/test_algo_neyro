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
- report.decision=MANUAL_REVIEW means "candidate for human check", NOT automatic permission to trade. You may reject it.
- Real trade only if clean Intraday A: clear regime + clear location + CLOSED 1m/15m confirmation/rejection/hold + acceptable RR + no obvious stop magnet + no strong opposite 4H structure.
- B / B+ / A- are WAIT only, not tradable.
- If confirmation is missing, answer **WAIT — observation only, no entry** and write WAIT_CONFIRMATION.
- Do not use the current unfinished 15m/1h/4h candle as confirmation. It is context only.
- Do not place passive limit orders just because price is near VWAP/zone. Need a closed 1m/15m reclaim/hold or real sweep confirmation.
- Sweep means real closed-candle penetration beyond the prior 24h/day edge and return back inside. Equal high/low touch is not a sweep.
- If rank_score/quality_score is below 68, default to WAIT.
- Quality 68-69 is allowed as cautious Intraday A LIMIT when report.decision=MANUAL_REVIEW, DATA_WARNING is absent, trap/late are low, pressure supports direction, report already states a CLOSED hold/reclaim/rejection, CSV confirms the LAST CLOSED 15m candle agrees with the trade direction, and RR is clean. Do not demand an extra 15m close/retest beyond the confirmation already present in report/CSV.
- Green MANUAL_REVIEW must be direction-confirmed by CSV, not only by the report text. For LONG, the last closed 15m candle must show buyer hold/reclaim in the entry/VWAP zone. For SHORT, the last closed 15m candle must show seller rejection/weakness from the entry/VWAP zone. If the last closed 15m candle is an opposite reclaim/hold against the intended trade, downgrade to WAIT_CONFIRMATION.
- Reject if report/montage/CSV materially disagree or DATA_WARNING exists.
- Reject if trend just flipped, is transitional, or 4H structure is strongly opposite to the intended direction.
- Near 24h high/low is not an automatic reject. For LONG, 24h high can be TP/liquidity target; for SHORT, 24h low can be TP/liquidity target. Reject only if room to the edge is smaller than TP1/RR or there is a closed rejection/sweep against the trade.
- Do not chase after impulse. If price moved part-way toward TP before entry, do not chase; a return to the planned retest/entry zone is allowed only if TP1 was NOT reached before fill and closed 15m structure is still valid.
- MISSED rule: if the planned TP1 / first profit target was reached before the LIMIT entry was filled, the old limit setup is invalid/missed. Do NOT keep the old limit on the retest. A new trade is allowed only after a fresh closed 15m hold/reclaim/rejection in the trade direction.

Stop and RR rules:
- Stop quality must be checked for EVERY symbol, not only majors. This applies to BTC/ETH/XAU/SILVER/USOIL and also ADA/BCH/XRP/GRAM/other alts.
- Do not give micro-invalidation unless the intraday_task explicitly says micro scalp. This task is not micro scalp.
- SL must be structural: beyond the actual setup invalidation swing / day edge / nearest liquidity magnet / obvious sweep zone.
- It is forbidden to place SL inside normal noise, 1m wick zone, obvious magnet, previous reaction high/low, or liquidity pocket. If nearby high/low zone is 4088-4090, SL cannot be 4085-4088; SL must be above the zone, e.g. 4091-4095.
- Do NOT tighten SL just to keep RR attractive. If the real structural SL is wider, use the real structural SL and recalculate TP from that risk.
- Do NOT automatically force SL behind a far 24h high/low if that makes the trade a rescue swing instead of intraday. If only valid SL is far 24h extreme and RR is bad, answer WAIT.
- If the nearest day high/low is too close to entry compared with structural risk, do not force the trade; answer WAIT / observation only and wait for breakout+retest.
- If structural SL makes RR bad, do not force the trade; answer WAIT / observation only.
- TP must be calculated from the real SL risk. Wide SL requires wider TP. Micro-TPs with a wide SL are forbidden.
- TP1 closes 33%. Do not force instant BE after TP1. Move remainder to BE only after 15m close beyond TP1 in trade direction or after TP2; before that, keep structural SL unless structure breaks.
- Before final answer, check: is SL beyond structure? do TPs match real risk? If not, answer WAIT / observation only.

Compact template:
**WAIT — observation only, no entry** / **Intraday A**
Setup <symbol>: LONG LIMIT / SHORT LIMIT / WAIT_CONFIRMATION
Entry: **...**
Stop: **...**
TP1: **...** — закрыть 33%; BE только после 15m close за TP1 или после TP2
TP2: **...** — закрыть 33%; остаток в BE
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
        "answer_rule_for_chatgpt": "Use only this archive data. Brief Russian answer. report.decision=MANUAL_REVIEW is a candidate for human check, not automatic permission, but do not over-confirm it. Maximum one real setup per archive, Intraday A only; B/B+/A- => WAIT observation only. LIMIT only. Bold Entry/Stop/TP numbers. Use midpoint for ranges. Require CLOSED 1m/15m rejection/hold or sweep reclaim; unfinished candles are context only; reject strong opposite 4H structure. If report already states a closed hold/reclaim/rejection and CSV confirms it, do not require an extra 15m close/retest beyond the report. However, green MANUAL_REVIEW must be direction-confirmed by the LAST CLOSED 15m candle in CSV: LONG needs buyer hold/reclaim in the entry/VWAP zone; SHORT needs seller rejection/weakness from the entry/VWAP zone. If the last closed 15m is an opposite reclaim/hold against the intended trade, answer WAIT_CONFIRMATION. If price moved part-way toward TP before entry, do not chase; a return to the planned retest/entry zone is allowed only if TP1 was NOT reached before fill and closed 15m structure remains valid. If TP1 / first profit target was reached before limit fill, the old setup is MISSED and new entry requires fresh closed 15m confirmation. If rank_score/quality_score < 68, default WAIT; 68-69 may be cautious Intraday A LIMIT when DATA_WARNING is absent, trap/late are low, pressure supports direction, closed confirmation exists, last closed 15m agrees with direction, and RR is clean. Near 24h high/low is not automatic reject; it can be TP/liquidity target if TP1/RR room is enough and there is no closed rejection/sweep against the trade. SL must be structural beyond actual setup invalidation / nearest liquidity magnet for every symbol; do not place SL inside normal noise/1m wick zone/previous reaction high-low, and do not tighten SL just to keep RR attractive. If nearest day edge is too close or structural SL ruins RR, answer WAIT. TP1 closes 33%; move remainder to BE only after 15m close beyond TP1 in trade direction or after TP2.",
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
