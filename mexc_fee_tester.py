from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp

from mexc import to_contract_symbol


MEXC_FUTURES_BASE_URL = "https://api.mexc.com"
FEE_TEST_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
FEE_TEST_LEVERAGE = 2
FEE_TEST_MARGIN_FRACTION = Decimal("0.10")
FEE_TEST_HOLD_SECONDS = 5 * 60
FEE_TEST_LIMIT_FILL_WAIT_SECONDS = 90
FEE_TEST_LIMIT_PRICE_OFFSET_BPS = Decimal("2")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def d(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def decimal_to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: decimal_to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decimal_to_jsonable(v) for v in value]
    return value


def quantize_floor(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def quantize_ceil(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def fmt_decimal(value: Decimal) -> str:
    value = value.normalize()
    # Decimal('1E+3') is not accepted by many APIs, use fixed-point.
    if "E" in str(value) or "e" in str(value):
        return format(value, "f")
    return str(value)


@dataclass
class FeeTestOrderPlan:
    symbol: str
    contract_symbol: str
    equity_usdt: Decimal
    margin_usdt: Decimal
    leverage: int
    notional_usdt: Decimal
    price: Decimal
    contract_size: Decimal
    vol: Decimal
    open_type: int = 1  # isolated

    def as_dict(self) -> dict[str, Any]:
        return decimal_to_jsonable(self.__dict__)


class MexcFuturesPrivateClient:
    """Small MEXC Futures private client for fee-test orders only."""

    def __init__(self, api_key: str, api_secret: str, logger: logging.Logger, base_url: str = MEXC_FUTURES_BASE_URL):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.logger = logger
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=40)
        self.session = aiohttp.ClientSession(timeout=self.timeout)
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self.session.close()

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = 0.22 - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    def _sign_headers(self, method: str, params: dict[str, Any] | None = None, body: Any = None) -> tuple[dict[str, str], str | None]:
        ts = str(int(time.time() * 1000))
        method = method.upper()
        if method in {"GET", "DELETE"}:
            clean = {k: v for k, v in (params or {}).items() if v is not None}
            param_string = "&".join(f"{k}={clean[k]}" for k in sorted(clean))
            body_text = None
        else:
            if isinstance(body, dict):
                clean_body = {k: v for k, v in (body or {}).items() if v is not None}
            else:
                clean_body = body if body is not None else {}
            body_text = json.dumps(clean_body, separators=(",", ":"), ensure_ascii=False)
            param_string = body_text
        target = f"{self.api_key}{ts}{param_string}"
        signature = hmac.new(self.api_secret.encode("utf-8"), target.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {
            "ApiKey": self.api_key,
            "Request-Time": ts,
            "Signature": signature,
            "Recv-Window": "30000",
            "Content-Type": "application/json",
        }
        return headers, body_text

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None, private: bool = False) -> Any:
        await self._throttle()
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {}
        data = None
        if private:
            headers, data = self._sign_headers(method, params=params, body=body)
        async with self.session.request(method.upper(), url, params=params if method.upper() in {"GET", "DELETE"} else None, data=data, headers=headers) as resp:
            text = await resp.text()
            try:
                payload = json.loads(text) if text else None
            except Exception:
                payload = {"raw_text": text}
            if resp.status >= 400:
                raise RuntimeError(f"MEXC HTTP {resp.status} {method} {path}: {text[:1000]}")
            return payload

    async def get_contract_detail(self, symbol: str) -> dict[str, Any]:
        contract = to_contract_symbol(symbol)
        data = await self._request("GET", "/api/v1/contract/detail/country", {"symbol": contract})
        if not isinstance(data, dict) or data.get("success") is not True:
            raise RuntimeError(f"contract detail failed {contract}: {data}")
        payload = data.get("data")
        if isinstance(payload, list):
            payload = next((x for x in payload if x.get("symbol") == contract), payload[0] if payload else None)
        if not isinstance(payload, dict):
            raise RuntimeError(f"bad contract detail {contract}: {data}")
        return payload

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        contract = to_contract_symbol(symbol)
        data = await self._request("GET", "/api/v1/contract/ticker", {"symbol": contract})
        if not isinstance(data, dict) or data.get("success") is not True:
            raise RuntimeError(f"ticker failed {contract}: {data}")
        payload = data.get("data")
        if isinstance(payload, list):
            payload = next((x for x in payload if x.get("symbol") == contract), payload[0] if payload else None)
        if not isinstance(payload, dict):
            raise RuntimeError(f"bad ticker {contract}: {data}")
        return payload

    async def get_assets(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/v1/private/account/assets", private=True)
        if not isinstance(data, dict) or data.get("success") is not True:
            raise RuntimeError(f"assets failed: {data}")
        return data.get("data") or []

    async def get_usdt_equity(self) -> Decimal:
        assets = await self.get_assets()
        for row in assets:
            if str(row.get("currency", "")).upper() == "USDT":
                # equity = total including unrealized; cashBalance is also logged by test.
                return d(row.get("equity") or row.get("cashBalance") or row.get("availableBalance"))
        raise RuntimeError(f"USDT asset not found in assets response: {assets}")

    async def get_fee_details(self, symbol: str) -> dict[str, Any]:
        contract = to_contract_symbol(symbol)
        data = await self._request("GET", "/api/v1/private/account/tiered_fee_rate/v2", {"symbol": contract}, private=True)
        if not isinstance(data, dict) or data.get("success") is not True:
            return {"success": False, "raw": data}
        return data.get("data") or {}

    async def change_leverage(self, symbol: str, leverage: int, position_type: int = 1, open_type: int = 1) -> dict[str, Any]:
        body = {
            "symbol": to_contract_symbol(symbol),
            "leverage": int(leverage),
            "openType": int(open_type),
            "positionType": int(position_type),
        }
        return await self._request("POST", "/api/v1/private/position/change_leverage", body=body, private=True)

    async def create_order(
        self,
        symbol: str,
        side: int,
        order_type: int,
        vol: Decimal,
        price: Decimal | None = None,
        leverage: int | None = None,
        open_type: int = 1,
        external_oid: str | None = None,
        reduce_only: bool | None = None,
        position_mode: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "symbol": to_contract_symbol(symbol),
            "price": fmt_decimal(price or Decimal("0")),
            "vol": fmt_decimal(vol),
            "side": int(side),
            "type": int(order_type),
            "openType": int(open_type),
            "externalOid": external_oid,
        }
        if leverage is not None:
            body["leverage"] = int(leverage)
        if reduce_only is not None:
            body["reduceOnly"] = bool(reduce_only)
        if position_mode is not None:
            body["positionMode"] = int(position_mode)
        return await self._request("POST", "/api/v1/private/order/create", body=body, private=True)

    async def get_open_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": to_contract_symbol(symbol)} if symbol else {}
        data = await self._request("GET", "/api/v1/private/position/open_positions", params, private=True)
        if not isinstance(data, dict) or data.get("success") is not True:
            return []
        payload = data.get("data") or []
        return payload if isinstance(payload, list) else [payload]

    async def get_order_deals(self, order_id: str | int) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/api/v1/private/order/deal_details/{order_id}", private=True)
        if not isinstance(data, dict) or data.get("success") is not True:
            return []
        payload = data.get("data") or []
        return payload if isinstance(payload, list) else [payload]

    async def cancel_order(self, order_id: str | int) -> dict[str, Any]:
        # MEXC futures cancel endpoint expects a JSON list of order ids.
        return await self._request("POST", "/api/v1/private/order/cancel", body=[int(order_id)], private=True)


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = decimal_to_jsonable({"ts_utc": utc_now_iso(), **event})
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _append_csv(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = decimal_to_jsonable(row)
    headers = [
        "ts_utc", "test_id", "mode", "symbol", "phase", "side", "order_type", "order_id", "external_oid",
        "requested_vol", "filled_vol", "avg_price", "fee", "fee_currency", "taker", "profit", "raw_short",
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({k: flat.get(k, "") for k in headers})


class MexcFeeTestRunner:
    def __init__(self, api_key: str, api_secret: str, data_root: Path, logger: logging.Logger):
        self.client = MexcFuturesPrivateClient(api_key, api_secret, logger)
        self.data_root = data_root
        self.logger = logger
        self.log_jsonl = data_root / "logs" / "mexc_fee_test.jsonl"
        self.log_csv = data_root / "logs" / "mexc_fee_test.csv"
        self.symbols = FEE_TEST_SYMBOLS

    async def close(self) -> None:
        await self.client.close()

    def log(self, event: dict[str, Any]) -> None:
        self.logger.info("MEXC_FEE_TEST %s", json.dumps(decimal_to_jsonable(event), ensure_ascii=False)[:3000])
        _append_jsonl(self.log_jsonl, event)
        if event.get("csv_row"):
            _append_csv(self.log_csv, event["csv_row"])

    async def _build_plan(self, symbol: str, equity: Decimal, mode: str) -> FeeTestOrderPlan:
        detail = await self.client.get_contract_detail(symbol)
        ticker = await self.client.get_ticker(symbol)
        contract_symbol = to_contract_symbol(symbol)
        contract_size = d(detail.get("contractSize"), "0.0001")
        vol_unit = d(detail.get("volUnit"), "1")
        min_vol = d(detail.get("minVol"), "1")
        price_unit = d(detail.get("priceUnit"), "0.1")
        last = d(ticker.get("lastPrice") or ticker.get("fairPrice") or ticker.get("bid1") or ticker.get("ask1"))
        ask = d(ticker.get("ask1"), str(last))
        bid = d(ticker.get("bid1"), str(last))
        if mode == "limit":
            # For a long open, set buy limit slightly above ask so it is likely to fill quickly.
            offset = FEE_TEST_LIMIT_PRICE_OFFSET_BPS / Decimal("10000")
            price = quantize_ceil(ask * (Decimal("1") + offset), price_unit)
        else:
            price = last
        margin = equity * FEE_TEST_MARGIN_FRACTION
        notional = margin * Decimal(FEE_TEST_LEVERAGE)
        raw_vol = notional / (price * contract_size)
        vol = quantize_floor(raw_vol, vol_unit)
        if vol < min_vol:
            vol = min_vol
        plan = FeeTestOrderPlan(
            symbol=symbol,
            contract_symbol=contract_symbol,
            equity_usdt=equity,
            margin_usdt=margin,
            leverage=FEE_TEST_LEVERAGE,
            notional_usdt=notional,
            price=price,
            contract_size=contract_size,
            vol=vol,
        )
        self.log({
            "event": "plan_built",
            "mode": mode,
            "symbol": symbol,
            "plan": plan.as_dict(),
            "ticker": ticker,
            "contract_detail_subset": {
                "contractSize": str(contract_size),
                "priceUnit": str(price_unit),
                "volUnit": str(vol_unit),
                "minVol": str(min_vol),
                "makerFeeRate": detail.get("makerFeeRate"),
                "takerFeeRate": detail.get("takerFeeRate"),
                "isZeroFeeRate": detail.get("isZeroFeeRate"),
                "isZeroFeeSymbol": detail.get("isZeroFeeSymbol"),
                "apiAllowed": detail.get("apiAllowed"),
            },
        })
        return plan

    async def _wait_deals(self, order_id: str | int, timeout_sec: int = 30) -> list[dict[str, Any]]:
        end = time.monotonic() + timeout_sec
        last: list[dict[str, Any]] = []
        while time.monotonic() < end:
            deals = await self.client.get_order_deals(order_id)
            if deals:
                return deals
            last = deals
            await asyncio.sleep(2)
        return last

    def _summarize_deals(self, deals: list[dict[str, Any]]) -> dict[str, Any]:
        if not deals:
            return {"filled_vol": Decimal("0"), "avg_price": Decimal("0"), "fee": Decimal("0"), "fee_currency": "", "taker": None, "profit": Decimal("0")}
        total_vol = sum((d(x.get("vol")) for x in deals), Decimal("0"))
        if total_vol > 0:
            avg = sum((d(x.get("price")) * d(x.get("vol")) for x in deals), Decimal("0")) / total_vol
        else:
            avg = Decimal("0")
        fee = sum((d(x.get("fee")) for x in deals), Decimal("0"))
        profit = sum((d(x.get("profit")) for x in deals), Decimal("0"))
        return {
            "filled_vol": total_vol,
            "avg_price": avg,
            "fee": fee,
            "fee_currency": deals[0].get("feeCurrency") or "",
            "taker": any(bool(x.get("taker", x.get("isTaker", False))) for x in deals),
            "profit": profit,
        }

    async def _open_one(self, test_id: str, mode: str, plan: FeeTestOrderPlan) -> dict[str, Any]:
        await self.client.change_leverage(plan.symbol, plan.leverage, position_type=1, open_type=1)
        order_type = 5 if mode == "market" else 1
        price = None if mode == "market" else plan.price
        external_oid = f"fee_{mode}_open_{plan.symbol}_{uuid4().hex[:10]}"
        resp = await self.client.create_order(
            plan.symbol,
            side=1,  # open long
            order_type=order_type,
            vol=plan.vol,
            price=price,
            leverage=plan.leverage,
            open_type=1,
            external_oid=external_oid,
        )
        order_id = ((resp or {}).get("data") or {}).get("orderId") if isinstance(resp, dict) else None
        deals = await self._wait_deals(order_id, timeout_sec=FEE_TEST_LIMIT_FILL_WAIT_SECONDS if mode == "limit" else 30) if order_id else []
        cancel_response = None
        if mode == "limit" and order_id and not deals:
            try:
                cancel_response = await self.client.cancel_order(order_id)
            except Exception as exc:  # noqa: BLE001
                cancel_response = {"error": str(exc)}
        summary = self._summarize_deals(deals)
        event = {
            "event": "open_order",
            "test_id": test_id,
            "mode": mode,
            "symbol": plan.symbol,
            "side": "open_long",
            "order_type": "market" if mode == "market" else "limit",
            "order_id": order_id,
            "external_oid": external_oid,
            "plan": plan.as_dict(),
            "response": resp,
            "deals": deals,
            "cancel_response_if_unfilled": cancel_response,
            "summary": decimal_to_jsonable(summary),
            "csv_row": {
                "ts_utc": utc_now_iso(), "test_id": test_id, "mode": mode, "symbol": plan.symbol, "phase": "open",
                "side": "open_long", "order_type": "market" if mode == "market" else "limit", "order_id": order_id,
                "external_oid": external_oid, "requested_vol": plan.vol, **summary, "raw_short": json.dumps(resp, ensure_ascii=False)[:500],
            },
        }
        self.log(event)
        return {"plan": plan, "order_id": order_id, "deals": deals, "summary": summary, "response": resp}

    async def _close_one(self, test_id: str, mode: str, plan: FeeTestOrderPlan) -> dict[str, Any]:
        positions = await self.client.get_open_positions(plan.symbol)
        long_positions = [p for p in positions if str(p.get("symbol")) == plan.contract_symbol and int(p.get("positionType", 0)) == 1 and d(p.get("holdVol")) > 0]
        hold_vol = sum((d(p.get("holdVol")) for p in long_positions), Decimal("0"))
        close_vol = min(plan.vol, hold_vol) if hold_vol > 0 else Decimal("0")
        if close_vol <= 0:
            self.log({"event": "close_skipped_no_position", "test_id": test_id, "mode": mode, "symbol": plan.symbol, "positions": positions})
            return {"skipped": True, "reason": "no long position", "positions": positions}

        order_type = 5 if mode == "market" else 1
        price = None
        if mode == "limit":
            ticker = await self.client.get_ticker(plan.symbol)
            bid = d(ticker.get("bid1") or ticker.get("lastPrice"))
            detail = await self.client.get_contract_detail(plan.symbol)
            price_unit = d(detail.get("priceUnit"), "0.1")
            offset = FEE_TEST_LIMIT_PRICE_OFFSET_BPS / Decimal("10000")
            # For long close, sell limit slightly below bid so it is likely to fill quickly.
            price = quantize_floor(bid * (Decimal("1") - offset), price_unit)
        external_oid = f"fee_{mode}_close_{plan.symbol}_{uuid4().hex[:10]}"
        resp = await self.client.create_order(
            plan.symbol,
            side=4,  # close long
            order_type=order_type,
            vol=close_vol,
            price=price,
            leverage=None,
            open_type=1,
            external_oid=external_oid,
            reduce_only=True,
        )
        order_id = ((resp or {}).get("data") or {}).get("orderId") if isinstance(resp, dict) else None
        deals = await self._wait_deals(order_id, timeout_sec=FEE_TEST_LIMIT_FILL_WAIT_SECONDS if mode == "limit" else 30) if order_id else []
        cancel_response = None
        if mode == "limit" and order_id and not deals:
            try:
                cancel_response = await self.client.cancel_order(order_id)
            except Exception as exc:  # noqa: BLE001
                cancel_response = {"error": str(exc)}
        summary = self._summarize_deals(deals)
        event = {
            "event": "close_order",
            "test_id": test_id,
            "mode": mode,
            "symbol": plan.symbol,
            "side": "close_long",
            "order_type": "market" if mode == "market" else "limit",
            "order_id": order_id,
            "external_oid": external_oid,
            "requested_close_vol": str(close_vol),
            "response": resp,
            "deals": deals,
            "cancel_response_if_unfilled": cancel_response,
            "summary": decimal_to_jsonable(summary),
            "positions_before_close": positions,
            "csv_row": {
                "ts_utc": utc_now_iso(), "test_id": test_id, "mode": mode, "symbol": plan.symbol, "phase": "close",
                "side": "close_long", "order_type": "market" if mode == "market" else "limit", "order_id": order_id,
                "external_oid": external_oid, "requested_vol": close_vol, **summary, "raw_short": json.dumps(resp, ensure_ascii=False)[:500],
            },
        }
        self.log(event)

        # Safety fallback: if a limit close did not fill or left some long volume, close remaining long by market.
        fallback = None
        if mode == "limit":
            await asyncio.sleep(2)
            remaining_positions = await self.client.get_open_positions(plan.symbol)
            remaining_long = sum((d(p.get("holdVol")) for p in remaining_positions if str(p.get("symbol")) == plan.contract_symbol and int(p.get("positionType", 0)) == 1), Decimal("0"))
            if remaining_long > 0:
                fallback_oid = f"fee_market_fallback_close_{plan.symbol}_{uuid4().hex[:10]}"
                fallback_resp = await self.client.create_order(
                    plan.symbol, side=4, order_type=5, vol=remaining_long, price=None, leverage=None,
                    open_type=1, external_oid=fallback_oid, reduce_only=True,
                )
                fallback_order_id = ((fallback_resp or {}).get("data") or {}).get("orderId") if isinstance(fallback_resp, dict) else None
                fallback_deals = await self._wait_deals(fallback_order_id, timeout_sec=30) if fallback_order_id else []
                fallback_summary = self._summarize_deals(fallback_deals)
                fallback = {
                    "event": "market_fallback_close_after_limit", "test_id": test_id, "mode": mode,
                    "symbol": plan.symbol, "side": "close_long", "order_type": "market_fallback",
                    "order_id": fallback_order_id, "external_oid": fallback_oid, "requested_close_vol": str(remaining_long),
                    "response": fallback_resp, "deals": fallback_deals, "summary": decimal_to_jsonable(fallback_summary),
                    "csv_row": {
                        "ts_utc": utc_now_iso(), "test_id": test_id, "mode": mode, "symbol": plan.symbol, "phase": "close_fallback",
                        "side": "close_long", "order_type": "market_fallback", "order_id": fallback_order_id,
                        "external_oid": fallback_oid, "requested_vol": remaining_long, **fallback_summary, "raw_short": json.dumps(fallback_resp, ensure_ascii=False)[:500],
                    },
                }
                self.log(fallback)
        return {"plan": plan, "order_id": order_id, "deals": deals, "summary": summary, "response": resp, "fallback": fallback}

    async def run(self, mode: str, progress_cb=None) -> dict[str, Any]:
        if mode not in {"market", "limit"}:
            raise ValueError("mode must be market or limit")
        test_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{mode}_{uuid4().hex[:8]}"
        equity = await self.client.get_usdt_equity()
        fee_details = {}
        for symbol in self.symbols:
            try:
                fee_details[symbol] = await self.client.get_fee_details(symbol)
            except Exception as exc:  # noqa: BLE001
                fee_details[symbol] = {"error": str(exc)}
        self.log({
            "event": "test_start",
            "test_id": test_id,
            "mode": mode,
            "symbols": self.symbols,
            "equity_usdt": str(equity),
            "margin_fraction_per_symbol": str(FEE_TEST_MARGIN_FRACTION),
            "leverage": FEE_TEST_LEVERAGE,
            "hold_seconds": FEE_TEST_HOLD_SECONDS,
            "fee_details_before": fee_details,
        })
        if progress_cb:
            await progress_cb(f"MEXC fee-test {mode}: equity={equity} USDT, открываю BTC+ETH long...")
        plans = [await self._build_plan(symbol, equity, mode) for symbol in self.symbols]
        opened = await asyncio.gather(*(self._open_one(test_id, mode, plan) for plan in plans), return_exceptions=True)
        if progress_cb:
            await progress_cb(f"MEXC fee-test {mode}: ордера отправлены. Жду {FEE_TEST_HOLD_SECONDS // 60} минут до закрытия.")
        await asyncio.sleep(FEE_TEST_HOLD_SECONDS)
        closed = await asyncio.gather(*(self._close_one(test_id, mode, plan) for plan in plans), return_exceptions=True)
        final_positions = {}
        for symbol in self.symbols:
            try:
                final_positions[symbol] = await self.client.get_open_positions(symbol)
            except Exception as exc:  # noqa: BLE001
                final_positions[symbol] = {"error": str(exc)}
        result = {
            "test_id": test_id,
            "mode": mode,
            "opened": [str(x) if isinstance(x, Exception) else decimal_to_jsonable(x) for x in opened],
            "closed": [str(x) if isinstance(x, Exception) else decimal_to_jsonable(x) for x in closed],
            "final_positions": final_positions,
            "log_jsonl": str(self.log_jsonl),
            "log_csv": str(self.log_csv),
        }
        self.log({"event": "test_done", **result})
        return result


def tail_text(path: Path, max_chars: int = 3800) -> str:
    if not path.exists():
        return "Лог mexc_fee_test ещё не создан."
    data = path.read_text(encoding="utf-8", errors="replace")
    return data[-max_chars:]
