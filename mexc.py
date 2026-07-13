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
    """Market-data-only client for public MEXC/spot/futures klines.

    No trading endpoints exist here: no place_order, no cancel_order, no private account actions.
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
                        if resp.status in {418, 429} and self.market_type in {"futures", "spot"}:
                            self.min_request_interval_sec = min(max(self.min_request_interval_sec * 1.5, self.min_request_interval_sec + 0.5), 10.0)
                        wait = min(10 + attempt * 10, 90)
                        self.logger.warning(
                            "Market data retry %s/%s status=%s wait=%ss next_pause=%.2fs url=%s params=%s body=%s",
                            attempt, retries, resp.status, wait, self.min_request_interval_sec, url, params, text[:300],
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
                            if self.market_type in {"futures", "spot"}:
                                self.min_request_interval_sec = min(max(self.min_request_interval_sec * 1.5, self.min_request_interval_sec + 0.5), 10.0)
                            wait = min(15 + attempt * 15, 120)
                            self.logger.warning(
                                "Market data app-level rate limit %s/%s code=%s wait=%ss next_pause=%.2fs url=%s params=%s message=%s",
                                attempt, retries, code, wait, self.min_request_interval_sec, url, params, message,
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
            # MEXC futures ping has changed response shape over time. Some gateways
            # return a millisecond timestamp, some return seconds, and some return
            # only a success/pong payload. Never let this break scans: normalize
            # numeric timestamps and fall back to local UTC time when unavailable.
            server_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            raw_value = data.get("data") if isinstance(data, dict) else None
            try:
                if raw_value is not None:
                    parsed = int(raw_value)
                    server_ms = parsed * 1000 if parsed < 10_000_000_000 else parsed
            except (TypeError, ValueError):
                self.logger.warning("Futures ping did not include numeric server time; using local UTC time. raw=%s", data)
            return {"serverTime": server_ms, "source_endpoint": "/api/v1/contract/ping", "raw": data}
        data = await self._get_json("/api/v3/time")
        return {"serverTime": int(data.get("serverTime")), "source_endpoint": "/api/v3/time", "raw": data}

    async def futures_tickers(self) -> list[dict[str, Any]]:
        """Return all MEXC Futures tickers from the public contract endpoint."""
        if self.market_type != "futures":
            return []
        data = await self._get_json("/api/v1/contract/ticker")
        if isinstance(data, dict) and data.get("success") is True and isinstance(data.get("data"), list):
            return data["data"]
        if isinstance(data, list):
            return data
        raise RuntimeError(f"Unexpected futures ticker response: {str(data)[:500]}")

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
                    # Exact-only mode: never accept the first arbitrary contract when
                    # the requested symbol was not returned exactly. XAU != XAUT and
                    # USOIL/WTI != UKOIL/Brent, so substitution is unsafe.
                    detail = next((x for x in detail if x.get("symbol") == contract_symbol), None)
                if isinstance(detail, dict) and detail.get("symbol") == contract_symbol:
                    detail["requestedSymbol"] = symbol
                    items.append(detail)
                elif isinstance(detail, dict):
                    items.append({
                        "requestedSymbol": symbol,
                        "symbol": contract_symbol,
                        "warning": f"contract detail returned different symbol: {detail.get('symbol')}",
                    })
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

    async def spot_klines_until_end(self, symbol: str, interval: str, end_ms: int, limit: int = 500) -> list[list[Any]]:
        """Return spot klines closest to end_ms using an endTime cursor.

        Spot REST endpoints can return empty lists when a long historical
        range is walked from an old startTime. For long Stress Test backfills
        we page from newest to oldest with endTime only, then dedupe by
        open time. This keeps old scan modes untouched.
        """
        params = {
            "symbol": symbol,
            "interval": interval,
            "endTime": int(end_ms),
            "limit": int(limit),
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
        fail_on_empty_chunk: bool = False,
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
                self.logger.warning("Empty chunk %s %s %s start=%s end=%s attempt=%s", self.market_type, symbol, interval, start, chunk_end, consecutive_empty)
                if fail_on_empty_chunk:
                    if consecutive_empty < 3:
                        await asyncio.sleep(0.75 * consecutive_empty)
                        continue
                    raise RuntimeError(
                        f"Incomplete {interval} data for {symbol}: API returned an empty chunk "
                        f"{start}-{chunk_end} three times; refusing to skip candles"
                    )
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

        df = self._klines_to_df(symbol, interval, rows, source_exchange=self._source_exchange_name())
        self.logger.info("Downloaded %s %s %s rows=%s expected=%s", self.market_type, symbol, interval, len(df), expected_total)
        if progress_cb:
            await progress_cb(100.0, len(df), expected_total)
        return df

    async def futures_klines_until_end(self, symbol: str, interval: str, end_ms: int) -> list[list[Any]]:
        """Return up to 2000 futures klines closest to end_ms.

        MEXC futures docs explicitly support passing only `end`; this is safer for
        multi-year backfills because some gateways return only recent data or empty
        chunks when old start/end ranges are walked forward.
        """
        if interval not in FUTURES_INTERVAL:
            raise ValueError(f"Unsupported futures interval: {interval}")
        contract_symbol = to_contract_symbol(symbol)
        params = {
            "interval": FUTURES_INTERVAL[interval],
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

    async def download_klines_dataframe_backward(
        self,
        symbol: str,
        interval: str,
        window: DownloadWindow,
        progress_every_requests: int = 10,
        progress_cb: Callable[[float, int, int], Awaitable[None]] | None = None,
    ) -> pd.DataFrame:
        """Backfill klines from newest to oldest using an end-time cursor.

        Stress Test uses this for long archives. Old 30d scan modes keep the
        original forward segmented downloader untouched. Futures uses MEXC's
        contract endpoint with only `end`; Spot uses `/api/v3/klines` with
        only `endTime`. This avoids walking from 2023 through thousands of
        empty REST chunks when the exchange refuses old startTime windows.
        """
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")

        interval_ms = INTERVAL_MS[interval]
        expected_total = max(1, (window.end_ms - window.start_ms) // interval_ms)
        cursor_end = int(window.end_ms)
        rows_by_open: dict[int, list[Any]] = {}
        request_count = 0
        chunk_limit = 2000 if self.market_type == "futures" else (1000 if self.market_type == "binance_spot" else 500)
        max_requests = int(expected_total // chunk_limit) + 250
        empty_pages = 0
        stale_pages = 0
        oldest_seen: int | None = None

        self.logger.info(
            "Backward downloading %s %s %s from %s to %s",
            self.market_type,
            symbol,
            interval,
            window.as_dict()["start_utc"],
            window.as_dict()["end_utc"],
        )
        if progress_cb:
            await progress_cb(0.0, 0, expected_total)

        while cursor_end >= window.start_ms and request_count < max_requests:
            if self.market_type == "futures":
                chunk = await self.futures_klines_until_end(symbol, interval, cursor_end)
            elif self.market_type == "binance_spot":
                # Binance Spot Stress Test uses endTime-only backward paging.
                # Binance accepts endTime without startTime on /api/v3/klines.
                params = {
                    "symbol": symbol,
                    "interval": interval,
                    "endTime": int(cursor_end),
                    "limit": int(chunk_limit),
                }
                chunk = await self._get_json("/api/v3/klines", params)
            else:
                chunk = await self.spot_klines_until_end(symbol, interval, cursor_end, limit=chunk_limit)
            request_count += 1

            if not chunk:
                empty_pages += 1
                self.logger.warning(
                    "Empty backward page %s %s %s cursor_end=%s empty_pages=%s; stepping back",
                    self.market_type,
                    symbol,
                    interval,
                    cursor_end,
                    empty_pages,
                )
                cursor_end -= interval_ms * chunk_limit
                if empty_pages >= 10 and not rows_by_open:
                    # No current/recent klines at all: this is a real symbol/API problem.
                    self.logger.error(
                        "No backward kline pages returned for %s %s after %s requests; check symbol/API endpoint",
                        symbol,
                        interval,
                        request_count,
                    )
                    break
                if progress_cb and request_count % progress_every_requests == 0:
                    pct = min(100.0, len(rows_by_open) / expected_total * 100)
                    await progress_cb(pct, len(rows_by_open), expected_total)
                await asyncio.sleep(0.25 if self.market_type in {"futures", "binance_spot"} else 0.08)
                continue

            empty_pages = 0
            chunk = sorted(chunk, key=lambda x: int(x[0]))
            page_opens = [int(row[0]) for row in chunk if row and row[0] is not None]
            if not page_opens:
                cursor_end -= interval_ms * chunk_limit
                continue

            page_oldest = min(page_opens)
            page_newest = max(page_opens)
            added = 0
            for row in chunk:
                try:
                    open_time = int(row[0])
                except Exception:  # noqa: BLE001
                    continue
                if open_time < window.start_ms or open_time > window.end_ms:
                    continue
                if open_time not in rows_by_open:
                    rows_by_open[open_time] = row
                    added += 1

            if oldest_seen is not None and page_oldest >= oldest_seen:
                stale_pages += 1
                self.logger.warning(
                    "Backward page did not move older %s %s page_oldest=%s previous_oldest=%s stale_pages=%s",
                    self.market_type,
                    symbol,
                    page_oldest,
                    oldest_seen,
                    stale_pages,
                )
            else:
                stale_pages = 0
            oldest_seen = page_oldest if oldest_seen is None else min(oldest_seen, page_oldest)

            if request_count % progress_every_requests == 0:
                pct = min(100.0, len(rows_by_open) / expected_total * 100)
                self.logger.info(
                    "Backward progress %s %s %s: rows=%s approx=%.1f%% requests=%s page=%s..%s added=%s",
                    self.market_type,
                    symbol,
                    interval,
                    len(rows_by_open),
                    pct,
                    request_count,
                    page_oldest,
                    page_newest,
                    added,
                )
                if progress_cb:
                    await progress_cb(pct, len(rows_by_open), expected_total)

            if page_oldest <= window.start_ms:
                break
            if stale_pages >= 5:
                self.logger.error(
                    "Backward kline cursor stuck for %s %s at oldest=%s cursor_end=%s; stopping to avoid infinite loop",
                    symbol,
                    interval,
                    oldest_seen,
                    cursor_end,
                )
                break
            cursor_end = page_oldest - 1
            await asyncio.sleep(0.25 if self.market_type in {"futures", "binance_spot"} else 0.08)

        if request_count >= max_requests:
            self.logger.error(
                "Backward kline max_requests reached for %s %s rows=%s expected=%s max_requests=%s",
                symbol,
                interval,
                len(rows_by_open),
                expected_total,
                max_requests,
            )

        rows = [rows_by_open[k] for k in sorted(rows_by_open)]
        df = self._klines_to_df(symbol, interval, rows, source_exchange=self._source_exchange_name())
        self.logger.info(
            "Backward downloaded %s %s %s rows=%s expected=%s requests=%s",
            self.market_type,
            symbol,
            interval,
            len(df),
            expected_total,
            request_count,
        )
        if progress_cb:
            await progress_cb(100.0, len(df), expected_total)
        return df

    @staticmethod
    def _latest_open_ms(df: pd.DataFrame) -> int | None:
        if df is None or df.empty or "open_time" not in df.columns:
            return None
        values = pd.to_numeric(df["open_time"], errors="coerce").dropna()
        if values.empty:
            return None
        return int(values.max())

    async def download_intraday_klines_dataframe(
        self,
        symbol: str,
        interval: str,
        window: DownloadWindow,
        progress_every_requests: int = 10,
        progress_cb: Callable[[float, int, int], Awaitable[None]] | None = None,
    ) -> pd.DataFrame:
        """Download recent Intraday candles without assuming 30 days of listing history.

        Custom contracts can be listed after the requested window start, and MEXC may
        return an empty *leading* start/end page even though current candles exist.
        The old strict forward downloader treated that as a fatal hole and produced
        NO_DATA for valid symbols such as a newly listed/renamed contract.

        Intraday now pages newest -> oldest first. If that endpoint is stale/empty, a
        tolerant forward pass is used as a second exact-symbol attempt. Recent data
        gaps are still detected later by the Intraday engine, so this does not turn
        incomplete candles into a green setup. No symbol substitution is performed.
        """
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")

        interval_ms = INTERVAL_MS[interval]
        expected_total = max(1, (window.end_ms - window.start_ms) // interval_ms)
        recent_tolerance_ms = max(10 * interval_ms, 10 * 60_000)
        backward_error: Exception | None = None
        backward_df = pd.DataFrame()

        async def backward_progress(pct: float, rows: int, expected: int) -> None:
            if progress_cb:
                await progress_cb(min(85.0, max(0.0, float(pct)) * 0.85), rows, expected)

        try:
            backward_df = await self.download_klines_dataframe_backward(
                symbol,
                interval,
                window,
                progress_every_requests=progress_every_requests,
                progress_cb=backward_progress,
            )
        except Exception as exc:  # noqa: BLE001
            backward_error = exc
            self.logger.warning(
                "Intraday newest-first download failed symbol=%s interval=%s: %s; trying tolerant forward paging",
                symbol,
                interval,
                exc,
            )

        backward_latest = self._latest_open_ms(backward_df)
        if backward_latest is not None and backward_latest >= window.end_ms - recent_tolerance_ms:
            backward_df.attrs["intraday_download_strategy"] = "newest_first"
            backward_df.attrs["requested_start_ms"] = int(window.start_ms)
            backward_df.attrs["requested_end_ms"] = int(window.end_ms)
            if progress_cb:
                await progress_cb(100.0, len(backward_df), expected_total)
            self.logger.info(
                "Intraday data accepted newest-first symbol=%s rows=%s first=%s latest=%s requested_start=%s requested_end=%s",
                symbol,
                len(backward_df),
                int(pd.to_numeric(backward_df["open_time"], errors="coerce").min()) if not backward_df.empty else None,
                backward_latest,
                window.start_ms,
                window.end_ms,
            )
            return backward_df

        self.logger.warning(
            "Intraday newest-first data not recent enough symbol=%s rows=%s latest=%s expected_end=%s; trying tolerant forward paging",
            symbol,
            len(backward_df),
            backward_latest,
            window.end_ms,
        )

        async def forward_progress(pct: float, rows: int, expected: int) -> None:
            if progress_cb:
                mapped = 85.0 + min(15.0, max(0.0, float(pct)) * 0.15)
                await progress_cb(mapped, rows, expected)

        forward_error: Exception | None = None
        forward_df = pd.DataFrame()
        try:
            forward_df = await self.download_klines_dataframe(
                symbol,
                interval,
                window,
                progress_every_requests=progress_every_requests,
                progress_cb=forward_progress,
                fail_on_empty_chunk=False,
            )
        except Exception as exc:  # noqa: BLE001
            forward_error = exc
            self.logger.warning(
                "Intraday tolerant forward download failed symbol=%s interval=%s: %s",
                symbol,
                interval,
                exc,
            )

        candidates = [df for df in (backward_df, forward_df) if df is not None and not df.empty]
        if not candidates:
            detail = "; ".join(
                part for part in [
                    f"newest-first: {backward_error}" if backward_error else None,
                    f"forward: {forward_error}" if forward_error else None,
                ]
                if part
            )
            suffix = f" ({detail})" if detail else ""
            raise RuntimeError(
                f"No 1m candles returned for exact MEXC contract {symbol}. "
                f"Check that the contract is listed and active{suffix}"
            )

        # Prefer the source with the freshest last candle; use row count only as a
        # tie-breaker. A stale result is returned for explicit DATA_WARNING handling
        # rather than being silently replaced with another asset.
        chosen = max(
            candidates,
            key=lambda df: (self._latest_open_ms(df) or -1, len(df)),
        )
        chosen_latest = self._latest_open_ms(chosen)
        strategy = "tolerant_forward" if chosen is forward_df else "newest_first_stale"
        chosen.attrs["intraday_download_strategy"] = strategy
        chosen.attrs["requested_start_ms"] = int(window.start_ms)
        chosen.attrs["requested_end_ms"] = int(window.end_ms)
        if progress_cb:
            await progress_cb(100.0, len(chosen), expected_total)
        self.logger.info(
            "Intraday data selected symbol=%s strategy=%s rows=%s latest=%s expected_end=%s",
            symbol,
            strategy,
            len(chosen),
            chosen_latest,
            window.end_ms,
        )
        return chosen

    def _source_exchange_name(self) -> str:
        if self.market_type == "futures":
            return "MEXC_FUTURES_PUBLIC"
        if self.market_type == "binance_spot":
            return "BINANCE_SPOT_PUBLIC"
        return "MEXC_SPOT_PUBLIC"

    @staticmethod
    def _klines_to_df(
        symbol: str,
        interval: str,
        rows: list[list[Any]],
        source_exchange: str | None = None,
    ) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=[
                "open_time", "datetime_utc", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "symbol", "interval", "source_exchange"
            ])
        if source_exchange is None:
            source_exchange = "MEXC_FUTURES_PUBLIC" if symbol.upper().endswith("_USDT") or "_" in symbol else "BINANCE_SPOT_PUBLIC"
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
                "source_exchange": source_exchange,
            })
        df = pd.DataFrame(clean)
        df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
        return df


def save_dataframe_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="zstd")


def extract_fees_from_exchange_info(exchange_info: dict) -> dict:
    fees = {"source": "Public exchangeInfo fields / fee placeholder", "market_type": exchange_info.get("market_type"), "symbols": {}}
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
            "note": "Public symbol fields; verify actual futures account/exchange fees before live trading.",
        }
    return fees
