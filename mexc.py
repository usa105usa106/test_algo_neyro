from __future__ import annotations

import asyncio
import json
import logging
import time
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

FUTURES_INTERVAL = {
    "1m": "Min1",
    "5m": "Min5",
    "15m": "Min15",
    "30m": "Min30",
    "60m": "Min60",
    "1h": "Min60",
    "4h": "Hour4",
    "1d": "Day1",
}


def to_contract_symbol(symbol: str) -> str:
    symbol = symbol.upper().strip()
    if "_" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return symbol[:-4] + "_USDT"
    return symbol


@dataclass(frozen=True)
class DownloadWindow:
    start_ms: int
    end_ms: int

    @classmethod
    def last_days(cls, days: int) -> "DownloadWindow":
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        end = now - timedelta(minutes=1)
        start = end - timedelta(days=days)
        return cls(int(start.timestamp() * 1000), int(end.timestamp() * 1000))

    @classmethod
    def last_days_from_end_ms(cls, days: int, end_ms: int, interval_ms: int = 60_000) -> "DownloadWindow":
        # Use previous completed candle and align to interval boundary.
        end = int(end_ms) - interval_ms
        end = end - (end % interval_ms)
        start = end - days * 24 * 60 * 60 * 1000
        start = start - (start % interval_ms)
        return cls(start, end)

    def as_dict(self) -> dict:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "start_utc": datetime.fromtimestamp(self.start_ms / 1000, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(self.end_ms / 1000, tz=timezone.utc).isoformat(),
        }


class MexcSpotClient:
    """Market-data-only client. Current default is Binance Spot public klines only.

    No trading endpoints exist here. Futures code is retained only as unused legacy fallback,
    but config.py hardcodes binance_spot.
    """

    def __init__(self, base_url: str, logger: logging.Logger, market_type: str = "futures"):
        self.base_url = base_url.rstrip("/")
        self.logger = logger
        self.market_type = market_type.lower().strip()
        timeout = aiohttp.ClientTimeout(total=60)
        self.session = aiohttp.ClientSession(timeout=timeout)
        # Some exchanges can return app-level rate limits even with HTTP 200.
        # Keep requests intentionally slow and serialized. This is a data collector, speed is less
        # important than completing a clean multi-year archive.
        self.min_request_interval_sec = 1.25 if self.market_type == "futures" else (0.25 if self.market_type == "binance_spot" else 0.35)
        self._last_request_ts = 0.0
        self._request_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._request_lock:
            now = time.monotonic()
            wait = self.min_request_interval_sec - (now - self._last_request_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_ts = time.monotonic()

    async def close(self) -> None:
        await self.session.close()

    async def _get_json(self, path: str, params: dict[str, Any] | None = None, retries: int = 12) -> Any:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        last_text = ""
        for attempt in range(1, retries + 1):
            try:
                await self._throttle()
                async with self.session.get(url, params=params) as resp:
                    text = await resp.text()
                    last_text = text[:500]
                    if resp.status in {418, 429} or resp.status >= 500:
                        wait = min(10 + attempt * 10, 90)
                        self.logger.warning(
                            "Market data retry %s/%s status=%s wait=%ss url=%s params=%s body=%s",
                            attempt, retries, resp.status, wait, url, params, text[:300],
                        )
                        await asyncio.sleep(wait)
                        continue
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}: {text[:500]}")
                    if not text:
                        return None
                    data = json.loads(text)
                    # Futures API may return HTTP 200 with {'success': False, 'code': 510,
                    # 'message': 'Requests are too frequent...'}; treat it as retryable.
                    if isinstance(data, dict) and data.get("success") is False:
                        code = str(data.get("code", ""))
                        message = str(data.get("message", ""))
                        if code in {"510", "429", "418"} or "too frequent" in message.lower():
                            wait = min(15 + attempt * 15, 120)
                            self.logger.warning(
                                "Market data app-level rate limit %s/%s code=%s wait=%ss url=%s params=%s message=%s",
                                attempt, retries, code, wait, url, params, message,
                            )
                            await asyncio.sleep(wait)
                            continue
                    return data
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                wait = min(5 + attempt * 5, 60)
                self.logger.warning(
                    "Market data request error %s/%s: %s; wait=%ss url=%s params=%s",
                    attempt, retries, exc, wait, url, params,
                )
                await asyncio.sleep(wait)
        if last_error is not None:
            raise RuntimeError(f"Market data request failed after {retries} retries: {last_error}")
        raise RuntimeError(f"Market data request failed after {retries} retries. Last response: {last_text}")

    async def ping(self) -> bool:
        if self.market_type == "futures":
            data = await self._get_json("/api/v1/contract/ping")
            return bool(data and data.get("success") is True)
        data = await self._get_json("/api/v3/ping")
        return data == {}

    async def server_time(self) -> dict:
        if self.market_type == "futures":
            data = await self._get_json("/api/v1/contract/ping")
            server_ms = int(data.get("data")) if isinstance(data, dict) and data.get("data") else int(datetime.now(timezone.utc).timestamp() * 1000)
            return {"serverTime": server_ms, "source_endpoint": "/api/v1/contract/ping", "raw": data}
        data = await self._get_json("/api/v3/time")
        return {"serverTime": int(data.get("serverTime")), "source_endpoint": "/api/v3/time", "raw": data}

    async def exchange_info(self, symbols: list[str]) -> dict:
        if self.market_type == "binance_spot":
            # Binance supports JSON-encoded symbols parameter, but per-symbol fallback is
            # more tolerant if the gateway rejects the batch encoding.
            try:
                return await self._get_json("/api/v3/exchangeInfo", {"symbols": json.dumps(symbols)})
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Binance batch exchangeInfo failed, fallback per-symbol: %s", exc)
                items = []
                for sym in symbols:
                    data = await self._get_json("/api/v3/exchangeInfo", {"symbol": sym})
                    if isinstance(data, dict):
                        items.extend(data.get("symbols") or [])
                return {"market_type": "binance_spot", "symbols": items}

        if self.market_type == "futures":
            items: list[dict[str, Any]] = []
            for symbol in symbols:
                contract_symbol = to_contract_symbol(symbol)
                detail = None
                # Both variants are kept because MEXC has changed futures docs/paths over time.
                for path in ("/api/v1/contract/detail", "/api/v1/contract/detail/country"):
                    try:
                        result = await self._get_json(path, {"symbol": contract_symbol})
                        if isinstance(result, dict) and result.get("success"):
                            detail = result.get("data")
                            break
                    except Exception as exc:  # noqa: BLE001
                        self.logger.warning("Futures detail endpoint failed path=%s symbol=%s: %s", path, contract_symbol, exc)
                if isinstance(detail, list):
                    match = next((x for x in detail if x.get("symbol") == contract_symbol), None)
                    detail = match or (detail[0] if detail else None)
                if isinstance(detail, dict):
                    detail["requestedSymbol"] = symbol
                    items.append(detail)
                else:
                    items.append({"requestedSymbol": symbol, "symbol": contract_symbol, "warning": "contract detail unavailable"})
            return {"market_type": "futures", "symbols": items}

        # Spot supports symbol or symbols query parameter. If batch fails, fallback per-symbol.
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
            base["market_type"] = "spot"
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

    async def futures_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list[Any]]:
        if interval not in FUTURES_INTERVAL:
            raise ValueError(f"Unsupported futures interval: {interval}")
        contract_symbol = to_contract_symbol(symbol)
        params = {
            "interval": FUTURES_INTERVAL[interval],
            "start": int(start_ms // 1000),
            "end": int(end_ms // 1000),
        }
        data = await self._get_json(f"/api/v1/contract/kline/{contract_symbol}", params)
        if not isinstance(data, dict) or data.get("success") is not True:
            raise RuntimeError(f"Unexpected futures kline response for {contract_symbol}: {str(data)[:500]}")
        payload = data.get("data") or {}
        times = payload.get("time") or []
        opens = payload.get("realOpen") or payload.get("open") or []
        closes = payload.get("realClose") or payload.get("close") or []
        highs = payload.get("realHigh") or payload.get("high") or []
        lows = payload.get("realLow") or payload.get("low") or []
        vols = payload.get("vol") or []
        amounts = payload.get("amount") or []
        rows: list[list[Any]] = []
        for i, t in enumerate(times):
            try:
                open_time = int(t) * 1000
                rows.append([
                    open_time,
                    str(opens[i]),
                    str(highs[i]),
                    str(lows[i]),
                    str(closes[i]),
                    str(vols[i] if i < len(vols) else 0),
                    open_time + INTERVAL_MS[interval] - 1,
                    str(amounts[i] if i < len(amounts) else 0),
                ])
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Bad futures row symbol=%s index=%s: %s", symbol, i, exc)
        return rows

    async def download_klines_dataframe(
        self,
        symbol: str,
        interval: str,
        window: DownloadWindow,
        progress_every_requests: int = 10,
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
        chunk_limit = 2000 if self.market_type == "futures" else (1000 if self.market_type == "binance_spot" else 500)

        self.logger.info(
            "Downloading %s %s %s from %s to %s",
            self.market_type,
            symbol,
            interval,
            window.as_dict()["start_utc"],
            window.as_dict()["end_utc"],
        )
        if progress_cb:
            await progress_cb(0.0, 0, expected_total)

        consecutive_empty = 0
        while start <= end:
            chunk_end = min(start + interval_ms * chunk_limit - 1, end)
            if self.market_type == "futures":
                chunk = await self.futures_klines(symbol, interval, start, chunk_end)
            else:
                chunk = await self.klines(symbol, interval, start, chunk_end, limit=chunk_limit)
            request_count += 1

            if not chunk:
                consecutive_empty += 1
                self.logger.warning("Empty chunk %s %s %s start=%s end=%s; advancing", self.market_type, symbol, interval, start, chunk_end)
                start = chunk_end + interval_ms
                await asyncio.sleep(0.25 if self.market_type == "futures" else 0.05)
                if progress_cb and request_count % progress_every_requests == 0:
                    pct = min(100.0, max(0.0, (start - window.start_ms) / max(1, window.end_ms - window.start_ms) * 100.0))
                    await progress_cb(pct, len(rows), expected_total)
                continue

            consecutive_empty = 0
            chunk = sorted(chunk, key=lambda x: int(x[0]))
            added = 0
            for row in chunk:
                open_time = int(row[0])
                if open_time < window.start_ms or open_time > window.end_ms:
                    continue
                if last_open_seen is not None and open_time <= last_open_seen:
                    continue
                rows.append(row)
                last_open_seen = open_time
                added += 1

            if request_count % progress_every_requests == 0:
                pct = min(100.0, len(rows) / expected_total * 100)
                self.logger.info("Progress %s %s %s: rows=%s approx=%.1f%% requests=%s", self.market_type, symbol, interval, len(rows), pct, request_count)
                if progress_cb:
                    await progress_cb(pct, len(rows), expected_total)

            if added == 0 and last_open_seen is not None:
                # Defensive: move forward even if API repeats data.
                start = chunk_end + interval_ms
            elif last_open_seen is None:
                start = chunk_end + interval_ms
            else:
                start = last_open_seen + interval_ms
            await asyncio.sleep(0.25 if self.market_type in {"futures", "binance_spot"} else 0.05)

        df = self._klines_to_df(symbol, interval, rows)
        self.logger.info("Downloaded %s %s %s rows=%s expected=%s", self.market_type, symbol, interval, len(df), expected_total)
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
                "source_exchange": "BINANCE_SPOT_PUBLIC",
            })
        df = pd.DataFrame(clean)
        df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
        return df


def save_dataframe_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="zstd")


def extract_fees_from_exchange_info(exchange_info: dict) -> dict:
    fees = {"source": "Binance Spot public exchangeInfo fields / fee placeholder", "market_type": exchange_info.get("market_type"), "symbols": {}}
    for item in exchange_info.get("symbols", []):
        symbol = item.get("requestedSymbol") or item.get("symbol")
        if not symbol:
            continue
        fees["symbols"][symbol] = {
            "exchange_symbol": item.get("symbol"),
            "makerCommission": item.get("makerCommission"),
            "takerCommission": item.get("takerCommission"),
            "makerFeeRate": item.get("makerFeeRate"),
            "takerFeeRate": item.get("takerFeeRate"),
            "note": "Public symbol fields; Binance Spot public exchangeInfo does not provide your account-specific maker/taker fees. Verify fees before live trading.",
        }
    return fees
