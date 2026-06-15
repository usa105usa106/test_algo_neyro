from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp
import pandas as pd


INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "60m": 60 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


@dataclass(frozen=True)
class DownloadWindow:
    start_ms: int
    end_ms: int

    @classmethod
    def last_days(cls, days: int) -> "DownloadWindow":
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        # Use the previous completed minute to avoid partial candles.
        end = now - timedelta(minutes=1)
        start = end - timedelta(days=days)
        return cls(int(start.timestamp() * 1000), int(end.timestamp() * 1000))

    def as_dict(self) -> dict:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "start_utc": datetime.fromtimestamp(self.start_ms / 1000, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(self.end_ms / 1000, tz=timezone.utc).isoformat(),
        }


class MexcSpotClient:
    def __init__(self, base_url: str, logger: logging.Logger):
        self.base_url = base_url.rstrip("/")
        self.logger = logger
        timeout = aiohttp.ClientTimeout(total=45)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        await self.session.close()

    async def _get_json(self, path: str, params: dict[str, Any] | None = None, retries: int = 6) -> Any:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                async with self.session.get(url, params=params) as resp:
                    text = await resp.text()
                    if resp.status in {418, 429} or resp.status >= 500:
                        wait = min(2 ** attempt, 30)
                        self.logger.warning("MEXC retry %s/%s status=%s wait=%ss url=%s", attempt, retries, resp.status, wait, url)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}: {text[:500]}")
                    if not text:
                        return None
                    return json.loads(text)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                wait = min(2 ** attempt, 30)
                self.logger.warning("MEXC request error %s/%s: %s; wait=%ss", attempt, retries, exc, wait)
                await asyncio.sleep(wait)
        raise RuntimeError(f"MEXC request failed after {retries} retries: {last_error}")

    async def ping(self) -> bool:
        data = await self._get_json("/api/v3/ping")
        return data == {}

    async def server_time(self) -> dict:
        return await self._get_json("/api/v3/time")

    async def exchange_info(self, symbols: list[str]) -> dict:
        # MEXC supports symbol or symbols query parameter. If batch fails, fallback per-symbol.
        try:
            return await self._get_json("/api/v3/exchangeInfo", {"symbols": ",".join(symbols)})
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Batch exchangeInfo failed: %s; fallback per symbol", exc)
            items = []
            base = {}
            for symbol in symbols:
                result = await self._get_json("/api/v3/exchangeInfo", {"symbol": symbol})
                base = {k: v for k, v in result.items() if k != "symbols"}
                items.extend(result.get("symbols", []))
            base["symbols"] = items
            return base

    async def klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int = 500,
    ) -> list[list[Any]]:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": limit,
        }
        return await self._get_json("/api/v3/klines", params)

    async def download_klines_dataframe(
        self,
        symbol: str,
        interval: str,
        window: DownloadWindow,
        progress_every_requests: int = 25,
        progress_cb: Callable[[float, int, int], Awaitable[None]] | None = None,
    ) -> pd.DataFrame:
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")
        interval_ms = INTERVAL_MS[interval]
        start = window.start_ms
        end = window.end_ms
        rows: list[list[Any]] = []
        request_count = 0
        last_open_seen: int | None = None
        expected_total = max(1, (end - start) // interval_ms)

        self.logger.info("Downloading %s %s from %s to %s", symbol, interval, window.as_dict()["start_utc"], window.as_dict()["end_utc"])
        if progress_cb:
            await progress_cb(0.0, 0, expected_total)

        while start <= end:
            chunk_end = min(start + interval_ms * 500 - 1, end)
            chunk = await self.klines(symbol, interval, start, chunk_end, limit=500)
            request_count += 1

            if not chunk:
                self.logger.warning("Empty chunk %s %s start=%s end=%s; advancing", symbol, interval, start, chunk_end)
                start = chunk_end + interval_ms
                await asyncio.sleep(0.05)
                continue

            # Defensive sorting because APIs can occasionally return unexpected order.
            chunk = sorted(chunk, key=lambda x: int(x[0]))
            for row in chunk:
                open_time = int(row[0])
                if open_time < window.start_ms or open_time > window.end_ms:
                    continue
                if last_open_seen is not None and open_time <= last_open_seen:
                    continue
                rows.append(row)
                last_open_seen = open_time

            if request_count % progress_every_requests == 0:
                pct = min(100.0, len(rows) / expected_total * 100)
                self.logger.info("Progress %s %s: rows=%s approx=%.1f%% requests=%s", symbol, interval, len(rows), pct, request_count)
                if progress_cb:
                    await progress_cb(pct, len(rows), expected_total)

            if last_open_seen is None:
                start = chunk_end + interval_ms
            else:
                start = last_open_seen + interval_ms
            await asyncio.sleep(0.04)

        df = self._klines_to_df(symbol, interval, rows)
        self.logger.info("Downloaded %s %s rows=%s", symbol, interval, len(df))
        if progress_cb:
            await progress_cb(100.0, len(df), expected_total)
        return df

    @staticmethod
    def _klines_to_df(symbol: str, interval: str, rows: list[list[Any]]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=[
                "open_time", "datetime_utc", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "symbol", "interval", "source_exchange"
            ])
        clean = []
        for row in rows:
            clean.append({
                "open_time": int(row[0]),
                "datetime_utc": pd.to_datetime(int(row[0]), unit="ms", utc=True),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": int(row[6]) if len(row) > 6 and row[6] is not None else None,
                "quote_volume": float(row[7]) if len(row) > 7 and row[7] is not None else None,
                "symbol": symbol,
                "interval": interval,
                "source_exchange": "MEXC_SPOT",
            })
        df = pd.DataFrame(clean)
        df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
        return df


def save_dataframe_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="zstd")


def extract_fees_from_exchange_info(exchange_info: dict) -> dict:
    fees = {"source": "MEXC /api/v3/exchangeInfo public fields", "symbols": {}}
    for item in exchange_info.get("symbols", []):
        symbol = item.get("symbol")
        if not symbol:
            continue
        fees["symbols"][symbol] = {
            "makerCommission": item.get("makerCommission"),
            "takerCommission": item.get("takerCommission"),
            "note": "Public symbol commission fields. Verify account-specific fees before live trading.",
        }
    return fees
