from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Awaitable

import pandas as pd

from charts import create_montage_for_symbol, load_ohlcv, resample_ohlcv, _plot_candles
from config import ScanPreset, Settings
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
    "gold": "Gold / XAU",
    "btc": "BTC",
    "eth": "ETH",
    "silver": "Silver / XAG",
    "oil": "Oil / WTI",
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
        # Custom scans should keep their own symbol label. Do not call XAUT "Gold" or
        # UKOIL "Oil" unless the user used the exact main preset/alias.
        symbol = preset.symbols[0].upper().replace("-", "_")
        if symbol.endswith("_USDT"):
            return symbol[:-5]
        return symbol
    return preset.title


def _exact_candidates_for_symbol(symbol: str) -> list[str]:
    # Exact-only: verify exactly the requested symbol. Do not map custom XAUT to XAU,
    # UKOIL to USOIL, or any other similarly named instrument with a different price.
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


def _setup_format_text(preset: ScanPreset) -> str:
    """Human-readable strict output template stored inside every scan archive."""
    if len(preset.symbols) == 1:
        asset_label = _asset_label_for_preset(preset)
        return f"""SETUP_FORMAT.txt
CRITICAL OUTPUT RULES:
- Answer in Russian only.
- Return ONLY the setup. No comments before or after.
- The final answer MUST be one markdown txt code block: start with ```txt and end with ```.
- Line breaks are mandatory. Do NOT write LIMIT orders or TP levels in one paragraph.
- Copy the vertical structure exactly.
- Do not add an \"Актив:\" line.

If there is NO clean setup, answer exactly one line, without code block:
wait, сейчас лучше не входить, подожди и пришли новый архив.

If there IS a setup, answer exactly in this vertical format:

```txt
Setup {asset_label}:

Маркет - пропускаем, нет A+ сетапа

Лимит:
SHORT LIMIT 1: <price or WAIT>
SHORT LIMIT 2: <price or WAIT>

Тейки:
TP1: <price> — закрыть 33%, SL в б/у
TP2: <price> — закрыть 33%, SL в б/у
TP3: <price> — закрыть остаток

SL: <price>

Убрать лимит:
<условие, когда снять лимитки: если цена ушла к TP1/TP2 без входа — не догонять>
<условие, когда идея сломана: если пробой и закрепление выше/ниже SL-зоны>

Причина:
<1–3 коротких предложения по 1D/4H/1H/15m/1m>
```

For LONG setup, use this same vertical structure, but the limit block must be:
```txt
Лимит:
LONG LIMIT 1: <price or WAIT>
LONG LIMIT 2: <price or WAIT>
```

For A+ market setup, the market line can be:
```txt
Маркет - LONG MARKET <price>
```
or
```txt
Маркет - SHORT MARKET <price>
```

ABSOLUTE FORMAT BANS:
- NEVER write: Лимит: SHORT LIMIT 1: ... SHORT LIMIT 2: ...
- NEVER write: Тейки: TP1: ... TP2: ... TP3: ...
- NEVER write SELL LIMIT or BUY LIMIT.
- NEVER add a separate "Сопровождение" section.
- NEVER put two prices on the same line unless it is a range in Причина/Убрать лимит.
""".strip()

    return """SETUP_FORMAT.txt
CRITICAL OUTPUT RULES:
- Answer in Russian only.
- Return ONLY the setups. No comments before or after.
- The final answer MUST be one markdown txt code block: start with ```txt and end with ```.
- Line breaks are mandatory. Do NOT write LIMIT orders or TP levels in one paragraph.
- Copy the vertical structure exactly.
- Do not add "Актив:" lines.

If there is NO clean setup on all assets, answer exactly one line, without code block:
wait, сейчас лучше не входить, подожди и пришли новый архив.

If at least one asset has a setup, return blocks in this order. For assets without a setup, write WAIT inside that asset block.

```txt
Setup Gold / XAU:

Маркет - пропускаем, нет A+ сетапа

Лимит:
SHORT LIMIT 1: <price or WAIT>
SHORT LIMIT 2: <price or WAIT>

Тейки:
TP1: <price or WAIT> — закрыть 33%, SL в б/у
TP2: <price or WAIT> — закрыть 33%, SL в б/у
TP3: <price or WAIT> — закрыть остаток

SL: <price or WAIT>

Убрать лимит:
<...>
<...>

Причина:
<...>

Setup BTC:

Маркет - пропускаем, нет A+ сетапа

Лимит:
SHORT LIMIT 1: <price or WAIT>
SHORT LIMIT 2: <price or WAIT>

Тейки:
TP1: <price or WAIT> — закрыть 33%, SL в б/у
TP2: <price or WAIT> — закрыть 33%, SL в б/у
TP3: <price or WAIT> — закрыть остаток

SL: <price or WAIT>

Убрать лимит:
<...>
<...>

Причина:
<...>

Setup ETH:

Маркет - пропускаем, нет A+ сетапа

Лимит:
SHORT LIMIT 1: <price or WAIT>
SHORT LIMIT 2: <price or WAIT>

Тейки:
TP1: <price or WAIT> — закрыть 33%, SL в б/у
TP2: <price or WAIT> — закрыть 33%, SL в б/у
TP3: <price or WAIT> — закрыть остаток

SL: <price or WAIT>

Убрать лимит:
<...>
<...>

Причина:
<...>

Setup Silver / XAG:

Маркет - пропускаем, нет A+ сетапа

Лимит:
SHORT LIMIT 1: <price or WAIT>
SHORT LIMIT 2: <price or WAIT>

Тейки:
TP1: <price or WAIT> — закрыть 33%, SL в б/у
TP2: <price or WAIT> — закрыть 33%, SL в б/у
TP3: <price or WAIT> — закрыть остаток

SL: <price or WAIT>

Убрать лимит:
<...>
<...>

Причина:
<...>

Setup Oil / WTI:

Маркет - пропускаем, нет A+ сетапа

Лимит:
SHORT LIMIT 1: <price or WAIT>
SHORT LIMIT 2: <price or WAIT>

Тейки:
TP1: <price or WAIT> — закрыть 33%, SL в б/у
TP2: <price or WAIT> — закрыть 33%, SL в б/у
TP3: <price or WAIT> — закрыть остаток

SL: <price or WAIT>

Убрать лимит:
<...>
<...>

Причина:
<...>
```

For LONG setups, replace the limit block with:
```txt
Лимит:
LONG LIMIT 1: <price or WAIT>
LONG LIMIT 2: <price or WAIT>
```

For A+ market setup, the market line can be:
```txt
Маркет - LONG MARKET <price>
```
or
```txt
Маркет - SHORT MARKET <price>
```

ABSOLUTE FORMAT BANS:
- NEVER write: Лимит: SHORT LIMIT 1: ... SHORT LIMIT 2: ...
- NEVER write: Тейки: TP1: ... TP2: ... TP3: ...
- NEVER write SELL LIMIT or BUY LIMIT.
- NEVER add a separate "Сопровождение" section.
- NEVER put two limit orders or all take-profits in one line.
""".strip()

def _chatgpt_task_text(preset: ScanPreset, created_msk: str) -> str:
    assets = ", ".join(preset.symbols)
    setup_format = _setup_format_text(preset)

    strategy_block = """STRATEGY: Elite 5 Rejection / Rostislav-style

ROLE:
You are a manual/semi-auto trading assistant. Analyze only the archive data/charts and return a concrete setup: LONG / SHORT / WAIT, entry zone, limit orders, SL, TP1/TP2/TP3 with management inside TP lines, trade class and short reason.
Do not give long theory. Do not discuss general market opinions. Answer only with the setup.

TRADING UNIVERSE:
Main priority assets: BTC, ETH, XAU/GOLD, XAG/SILVER, OIL.
Main priority: XAU/GOLD.
If this archive was created from a custom text symbol, analyze that requested symbol too, but keep the same strict rules and reject weak/late setups.
Use exact MEXC symbols from manifest only. Do not replace XAU_USDT with XAUT_USDT. Do not replace USOIL_USDT/WTI with UKOIL_USDT/Brent. Do not replace a custom symbol with another instrument.

TIMEFRAMES:
1D = общий фон.
4H = главный старший контекст.
1H = структура движения.
15m = зона входа.
1m = точная реакция / микровход.
If some context is incomplete or manifest shows partial history, use available data and do not invent missing candles.

CORE PRINCIPLE:
The strategy is not based on smart money, random patterns or chasing pumps. Use trend, levels, structure, impulse, pullback, reaction and entry from a zone.
Price makes a strong move, then comes to an important zone. Do not chase price. Wait for pullback or reaction. Enter from the zone. SL goes behind the local invalidation high/low. TPs are partial.

SHORT MODEL:
Look for SHORT when 1H/4H are weak, price has already fallen or bounced after a fall, and price pulls back upward into resistance / local high / broken zone / MA / level.
Entry only from pullback. Do not short market at the bottom. SL above local high. TP levels below by nearest zones.
Rule: want SHORT -> wait for pullback upward. If price already dropped to TP zone without entry, skip.

LONG MODEL:
Look for LONG only when there was a strong dump, price reached a lower local zone, and 1m/15m shows upward reaction.
This is usually a SOFT quick bounce, not a global reversal. Targets must be close. After TP1 reduce risk or move SL to entry.

TRADE CLASSES:
A+ = higher timeframes agree, clean zone, strong impulse, clear invalidation, entry is not late. Allocation 20% total balance, isolated 10x.
A = good clear setup, but not perfect. Allocation 10% total balance, isolated 10x.
SOFT = cautious quick scalp, often countertrend, close targets, only from a good zone. Allocation up to 10% if entry is high-quality.
REJECT/WAIT = bad setup, late price, no zone, no clear SL, conflicting timeframes, or price already reached targets.

POSITION MANAGEMENT:
TP1 = close part of position.
TP2 = close part of position.
TP3 = close remainder.
For SOFT: after TP1 move SL to entry or strongly tighten it.
For A/A+: after TP2 SL must be at entry.
If price returns to entry after TP1/TP2, exit remainder without loss.

BANS:
Do not chase price.
Do not enter market after a strong move unless clearly A+.
Do not give entry if price already reached TP1/TP2.
Do not place a limit too close to current price when price is already low for SHORT or already high for LONG.
Do not average against position.
Do not hold SOFT as a large trend trade.
Do not open any setup without clear SL.
If setup is unclear, answer WAIT/no trade.

OUTPUT RULES:
Answer in Russian only.
Return ONLY the final setup, no extra explanation.
If no clean setup exists on the archive, answer exactly:
wait, сейчас лучше не входить, подожди и пришли новый архив."""

    title = "multi-asset scan archive" if len(preset.symbols) > 1 else "scan archive"
    charts_desc = "5 charts per asset: 1D, 4H, 1H, 15m, 1m" if len(preset.symbols) > 1 else "5 charts: 1D, 4H, 1H, 15m, 1m"
    symbols_label = "Requested exact symbols" if len(preset.symbols) > 1 else "Requested exact symbol"

    return f"""TASK:
Analyze this MEXC Futures {title} using the strategy and output template below.

Archive created: {created_msk} UTC+3/MSK
{symbols_label}: {assets}
Data: 1m OHLC requested for last 30 days, plus {charts_desc}.

{strategy_block}

STRICT SETUP WRITING FORMAT:
The answer must follow the template below. The same template is also stored as setup_format.txt in the archive root.

{setup_format}
""".strip()


def _downloaded_df_to_ohlcv_montage(df: pd.DataFrame) -> pd.DataFrame:
    if "datetime_utc" in df.columns:
        dt = pd.to_datetime(df["datetime_utc"], utc=True)
    else:
        dt = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    out = df.copy()
    out.index = dt
    out = out[["open", "high", "low", "close", "volume", "quote_volume"]].sort_index()
    out.columns = ["Open", "High", "Low", "Close", "Volume", "QuoteVolume"]
    return out


def _montage_task_text(preset: ScanPreset, created_msk: str) -> str:
    assets = ", ".join(preset.symbols)
    return f"""TASK:
Analyze this archive in MONTAGE mode.

Archive created: {created_msk} UTC+3/MSK
Requested exact symbols: {assets}
Archive contains montage charts only. Each montage is one image per asset with this layout:
- top-left: 1D
- top-right: 4H
- middle-left: 1H
- middle-right: 15m
- bottom-left: 1m
- bottom-right: info/task panel

Every chart already includes:
- candles
- price on the right
- last high/low
- MA7 / MA25 / MA99
- MACD
- exact symbol / asset name
- current time UTC+3 / MSK

MONTAGE MODE LOGIC:
- Montage mode is a SWING mode.
- For every asset you must always build TWO alternative setups:
  1) Swing LONG
  2) Swing SHORT
- Do not choose only one direction.
- Give both LONG and SHORT plans even if one side is weaker.
- If a side is completely invalid, write WAIT for that side.
- Montage mode classes are ONLY: A+, A, WAIT.
- Do NOT use SOFT in montage mode. SOFT remains only for standard non-montage scan mode.
- Prefer limit entries from zones, not market chasing.
- Use 1D / 4H / 1H for context, 15m / 1m for entry refinement.
- Focus on trend, structure, levels, pullback zones, invalidation and realistic take-profits.

RANKING RULE:
If the archive contains more than one montage / asset:
- rank all assets as 1, 2, 3, ... from best to worst
- 1 = best expected setup quality
- last = weakest expected setup quality
- inside each ranked asset block, still give both Swing LONG and Swing SHORT setups

IMPORTANT STYLE RULES:
- Answer in Russian only.
- Return only final setups.
- No intro.
- No outro.
- No long analysis paragraphs.
- No chain-of-thought.
- Output must look like a clean ready-made setup note.
- Short reason is allowed.
- Keep the wording close to this style.

REQUIRED OUTPUT STYLE:
For each asset, first show the asset line and current price line.
Then explicitly state whether price should be entered from market or not.
Then provide:
- Swing LONG <asset>
- Swing SHORT <asset>
- final line: Лучший план: ... Сейчас — WAIT / LONG LIMIT / SHORT LIMIT.

USE THIS EXACT STRUCTURE TEMPLATE:

If there are multiple assets:
1. <SYMBOL> — лучший выбор / сильный / средний / слабый
<symbol> текущая: <price>
С рынка не входить. <short note>

Swing LONG <BASE>
Вердикт: LONG LIMIT / WAIT
Класс: A+ / A / WAIT

Лимитки:
1. <zone 1>
2. <zone 2>

Стоп: <price>
TP1: <price or zone> — закрыть 33%, SL в б/у
TP2: <price or zone> — закрыть 33%, SL в б/у
TP3: <price or zone> — закрыть остаток

Отмена идеи: <short invalidation>
Причина: <short reason>

Swing SHORT <BASE>
Вердикт: SHORT LIMIT / WAIT
Класс: A+ / A / WAIT

Лимитки:
1. <zone 1>
2. <zone 2>

Стоп: <price>
TP1: <price or zone> — закрыть 33%, SL в б/у
TP2: <price or zone> — закрыть 33%, SL в б/у
TP3: <price or zone> — закрыть остаток

Отмена идеи: <short invalidation>
Причина: <short reason>

Лучший план: ждать либо LONG от <zone>, либо SHORT от <zone>. Сейчас — WAIT / LONG LIMIT / SHORT LIMIT.

If there is only one asset, use the same structure but without ranking words.

FORMAT NOTES:
- Use exactly the labels: Вердикт, Класс, Лимитки, Стоп, TP1, TP2, TP3, Отмена идеи, Причина, Лучший план.
- For limit zones, ranges like 1722–1716 are allowed.
- For TP levels, both single prices and zones are allowed.
- If a side is WAIT, you may still provide the likely trigger zone, but the verdict and class must say WAIT.
- The final answer must be practical and ready to trade from the written levels.
- Do not mention montage instructions in the final answer.
""".strip()




def _aplus_log(logger: logging.Logger, scan_id: str, stage: str, **fields: Any) -> None:
    detail = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    logger.info("A+ Hunter [%s] %s%s", scan_id, stage, f" | {detail}" if detail else "")


def _aplus_log_error(logger: logging.Logger, scan_id: str, stage: str, exc: BaseException, **fields: Any) -> None:
    detail = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    logger.exception("A+ Hunter [%s] ERROR %s%s: %s", scan_id, stage, f" | {detail}" if detail else "", exc)

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


FORCED_APLUS_SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    # Exact metals/oil/crypto symbols: no unsafe substitution such as XAU->XAUT or USOIL->UKOIL.
    "NVDA": ("NVDA_USDT", "NVIDIA_USDT"),
    "TSLA": ("TSLA_USDT",),
    "USOIL": ("USOIL_USDT",),
    "SILVER": ("SILVER_USDT",),
    "XAU": ("XAU_USDT",),
    "BTC": ("BTC_USDT",),
    "ETH": ("ETH_USDT",),
    "SP500": ("SP500_USDT", "US500_USDT", "SPX_USDT"),
    "GOOGL": ("GOOGL_USDT", "GOOGLE_USDT"),
    "NAS100": ("NAS100_USDT", "US100_USDT", "NASDAQ_USDT"),
}


def _ticker_liquidity_value(ticker: dict[str, Any]) -> float:
    # MEXC response fields can vary by gateway; prefer quote/notional fields.
    for key in ("amount24", "amount24h", "turnover24", "turnover24h", "volumeUsd24h", "quoteVolume"):
        value = _safe_float(ticker.get(key), 0.0)
        if value > 0:
            return value
    price = _safe_float(ticker.get("lastPrice") or ticker.get("last") or ticker.get("fairPrice"), 0.0)
    volume = _safe_float(ticker.get("volume24") or ticker.get("volume24h") or ticker.get("vol24"), 0.0)
    return price * volume


def _normalize_aplus_symbol_alias(alias: str) -> str:
    symbol = str(alias or "").upper().strip().replace("-", "_").replace("/", "_")
    if not symbol:
        return ""
    if symbol.endswith("_USDT"):
        return symbol
    if symbol.endswith("USDT"):
        return symbol[:-4] + "_USDT"
    return f"{symbol}_USDT"


def _resolve_forced_aplus_symbols(available_symbols: set[str]) -> tuple[list[str], list[dict[str, Any]]]:
    resolved: list[str] = []
    report: list[dict[str, Any]] = []
    for asset, aliases in FORCED_APLUS_SYMBOL_ALIASES.items():
        normalized_aliases = [_normalize_aplus_symbol_alias(alias) for alias in aliases]
        match = next((symbol for symbol in normalized_aliases if symbol in available_symbols), None)
        if match:
            resolved.append(match)
            report.append({"asset": asset, "resolved_symbol": match, "aliases_checked": normalized_aliases, "status": "resolved"})
        else:
            report.append({"asset": asset, "resolved_symbol": None, "aliases_checked": normalized_aliases, "status": "not_found_on_mexc_futures"})
    return resolved, report


async def _aplus_top200_symbols(client: MexcSpotClient, logger: logging.Logger, limit: int = 200) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    tickers = await client.futures_tickers()
    by_symbol: dict[str, dict[str, Any]] = {}
    available_symbols: set[str] = set()
    for ticker in tickers:
        symbol = str(ticker.get("symbol") or "").upper().strip()
        if not symbol.endswith("_USDT"):
            continue
        # Keep the user's global rule: only symbols with substring STOCK are blocked.
        if "STOCK" in symbol:
            continue
        available_symbols.add(symbol)
        liquidity = _ticker_liquidity_value(ticker)
        if liquidity <= 0:
            continue
        item = dict(ticker)
        item["symbol"] = symbol
        item["_liquidity"] = liquidity
        previous = by_symbol.get(symbol)
        if previous is None or liquidity > float(previous.get("_liquidity", 0.0)):
            by_symbol[symbol] = item
    filtered = list(by_symbol.values())
    filtered.sort(key=lambda x: x.get("_liquidity", 0.0), reverse=True)
    selected = filtered[:limit]
    symbols = [str(x.get("symbol") or "").upper().strip() for x in selected]

    forced_symbols, forced_report = _resolve_forced_aplus_symbols(available_symbols)
    seen = set(symbols)
    for symbol in forced_symbols:
        if symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
            for item in forced_report:
                if item.get("resolved_symbol") == symbol:
                    item["added_to_universe"] = True
                    break
        else:
            for item in forced_report:
                if item.get("resolved_symbol") == symbol:
                    item["added_to_universe"] = False
                    item["dedupe_reason"] = "already_in_top_200"
                    break

    duplicate_count = len(symbols) - len(set(symbols))
    logger.info(
        "A+ Hunter universe selected: top=%s final=%s unique=%s duplicates=%s forced=%s",
        len(selected), len(symbols), len(set(symbols)), duplicate_count, forced_report,
    )
    logger.info("A+ Hunter top symbols selected first20=%s", symbols[:20])
    if duplicate_count:
        logger.error("A+ Hunter universe duplicate symbols detected: final=%s unique=%s", len(symbols), len(set(symbols)))
    return symbols, selected, forced_report


def _aplus_direction_score(df: pd.DataFrame, direction: str) -> tuple[float, list[str]]:
    reasons: list[str] = []
    if len(df) < 120:
        return 0.0, ["too_few_15m_candles"]

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    price = float(close.iloc[-1])
    ma7 = float(close.rolling(7).mean().iloc[-1])
    ma25 = float(close.rolling(25).mean().iloc[-1])
    ma99 = float(close.rolling(99).mean().iloc[-1])
    high96 = float(high.tail(96).max())
    low96 = float(low.tail(96).min())
    rng = max(high96 - low96, price * 0.0001)
    range_pos = (price - low96) / rng
    ret8 = float(price / close.iloc[-8] - 1.0) if len(close) >= 8 and close.iloc[-8] else 0.0
    ret32 = float(price / close.iloc[-32] - 1.0) if len(close) >= 32 and close.iloc[-32] else 0.0
    volatility = rng / max(price, 1e-9)
    dist_ma25 = abs(price - ma25) / max(price, 1e-9)

    last32 = close.tail(32).pct_change().dropna()
    sign_changes = 0
    if len(last32) >= 3:
        signs = last32.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0)).tolist()
        signs = [x for x in signs if x != 0]
        sign_changes = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    choppy = sign_changes >= 18
    middle_range = 0.44 <= range_pos <= 0.56

    if direction == "LONG":
        trend_ok = price > ma25 and ma25 >= ma99 * 0.997
        momentum_ok = ret8 > 0 and ret32 > 0
        late_entry = range_pos > 0.90 or ret8 > 0.070
        zone_ok = 0.20 <= range_pos <= 0.86
        zone_wide = 0.12 <= range_pos <= 0.92
        trend = 28 if price > ma25 > ma99 else 20 if trend_ok else 10 if price > ma99 else 2
        momentum = 18 if momentum_ok and ret8 <= 0.055 and ret32 <= 0.25 else 12 if momentum_ok else 5 if ret8 > 0 else 0
        zone = 18 if zone_ok and not middle_range else 10 if zone_wide else 2
        freshness = 14 if not late_entry and abs(ret8) <= 0.055 else 8 if not late_entry else -10
        reasons.append(f"LONG price={price:.8g} ma7={ma7:.8g} ma25={ma25:.8g} ma99={ma99:.8g} range_pos={range_pos:.2f}")
    else:
        trend_ok = price < ma25 and ma25 <= ma99 * 1.003
        momentum_ok = ret8 < 0 and ret32 < 0
        late_entry = range_pos < 0.10 or ret8 < -0.070
        zone_ok = 0.14 <= range_pos <= 0.80
        zone_wide = 0.08 <= range_pos <= 0.88
        trend = 28 if price < ma25 < ma99 else 20 if trend_ok else 10 if price < ma99 else 2
        momentum = 18 if momentum_ok and ret8 >= -0.055 and ret32 >= -0.25 else 12 if momentum_ok else 5 if ret8 < 0 else 0
        zone = 18 if zone_ok and not middle_range else 10 if zone_wide else 2
        freshness = 14 if not late_entry and abs(ret8) <= 0.055 else 8 if not late_entry else -10
        reasons.append(f"SHORT price={price:.8g} ma7={ma7:.8g} ma25={ma25:.8g} ma99={ma99:.8g} range_pos={range_pos:.2f}")

    vol_score = 12 if 0.012 <= volatility <= 0.20 else 8 if 0.004 <= volatility <= 0.28 else 2
    clean_score = 8 if dist_ma25 <= 0.050 else 5 if dist_ma25 <= 0.090 else 1
    score = trend + momentum + zone + vol_score + clean_score + freshness

    cap = 98.0
    caps: list[str] = []
    if not trend_ok:
        cap = min(cap, 78.0)
        caps.append("no_clear_1h_trend_cap78")
    if not momentum_ok:
        cap = min(cap, 86.0)
        caps.append("weak_momentum_cap86")
    if middle_range:
        cap = min(cap, 88.0)
        caps.append("middle_range_cap88")
    if late_entry:
        cap = min(cap, 82.0)
        caps.append("late_entry_cap82")
    if not (0.004 <= volatility <= 0.28):
        cap = min(cap, 82.0)
        caps.append("bad_volatility_cap82")
    if dist_ma25 > 0.090:
        cap = min(cap, 84.0)
        caps.append("overextended_from_ma25_cap84")
    if choppy:
        cap = min(cap, 84.0)
        caps.append("choppy_15m_cap84")

    score = max(0.0, min(cap, score))
    reasons.append(f"trend={trend} momentum={momentum} zone={zone} vol={vol_score} clean={clean_score} freshness={freshness}")
    reasons.append(f"ret8={ret8:.3%} ret32={ret32:.3%} volatility96={volatility:.3%} dist_ma25={dist_ma25:.3%} sign_changes32={sign_changes}")
    reasons.append("gates=" + (", ".join(caps) if caps else "passed; max_score_cap98"))
    return score, reasons


async def _aplus_score_symbol(client: MexcSpotClient, symbol: str, server_ms: int, logger: logging.Logger) -> dict[str, Any]:
    interval = "15m"
    window = DownloadWindow.last_days_from_end_ms(4, server_ms, INTERVAL_MS[interval])
    rows = await client.futures_klines(symbol, interval, window.start_ms, window.end_ms)
    df = client._klines_to_df(symbol, interval, rows)
    if df.empty:
        return {"symbol": symbol, "score": 0.0, "direction": "WAIT", "rows_15m": 0, "reason": ["empty_15m"]}

    long_score, long_reason = _aplus_direction_score(df, "LONG")
    short_score, short_reason = _aplus_direction_score(df, "SHORT")
    if long_score >= short_score:
        direction = "LONG"
        score = long_score
        reason = long_reason
    else:
        direction = "SHORT"
        score = short_score
        reason = short_reason
    price = _safe_float(df["close"].iloc[-1], 0.0)
    logger.info("A+ Hunter scored %s direction=%s score=%.1f", symbol, direction, score)
    return {
        "symbol": symbol,
        "score": round(float(score), 2),
        "direction": direction if score > 0 else "WAIT",
        "long_score": round(float(long_score), 2),
        "short_score": round(float(short_score), 2),
        "last_price": price,
        "rows_15m": int(len(df)),
        "scan_interval": interval,
        "reason": reason,
    }


def _aplus_hunter_task_text(symbols: list[str], created_msk: str, candidates: list[dict[str, Any]]) -> str:
    assets = ", ".join(symbols)
    lines = []
    for c in candidates:
        lines.append(f"- {c.get('symbol')}: screener_score={c.get('score')} direction_hint={c.get('direction')} last_price={c.get('last_price')}")
    candidates_text = "\n".join(lines) if lines else "- none"
    return f"""TASK: A+ HUNTER
Analyze this MEXC Futures A+ Hunter montage archive.

Archive created: {created_msk} UTC+3/MSK
Candidate exact symbols: {assets}
Screener hints only, NOT final confirmation:
{candidates_text}

IMPORTANT:
- The screener only found symbols that look similar to A+.
- You must verify the montage yourself.
- Give a setup ONLY if it is truly A+.
- If there is no true A+ after visual check, answer exactly:
A+ нет, лучше ещё подождать.

A+ DEFINITION:
- Higher timeframes agree or there is an exceptionally clean rejection/continuation structure.
- There is a clear entry zone and clear invalidation.
- Entry is not late.
- Stop is logical and not too wide.
- TP1/TP2/TP3 are realistic from the current structure.
- Do not force trades. If unsure, reject.

ENTRY MODEL FOR TRUE A+ ONLY:
- A+ may include MARKET entry, but only while price is still fresh and not late.
- Always include both MARKET and LIMIT plan.
- MARKET must have an invalidation/late-entry line:
  Example LONG: "Не входить MARKET, если цена уже выше <price>."
  Example SHORT: "Не входить MARKET, если цена уже ниже <price>."
- Use the anti-chase rule: MARKET is allowed only if price has not moved more than roughly 25–35% of the path from MARKET entry to TP1.
- LIMIT is the safer pullback entry from the zone.
- If price already ran toward TP1 without entry, write WAIT / do not chase.

OUTPUT RULES:
- Answer in Russian only.
- Return only final A+ setup blocks, no intro/outro.
- Allowed classes: A+ or WAIT only.
- Do NOT give A, SOFT, or weak setups in A+ Hunter.
- If multiple true A+ setups exist, give multiple blocks ranked best first.
- If no true A+ exists, answer exactly one line:
A+ нет, лучше ещё подождать.

REQUIRED FORMAT FOR EACH TRUE A+:

Setup <SYMBOL>:
Вердикт: LONG / SHORT
Класс: A+

Вход:
MARKET: <price or price range> — только пока вход не поздний
LIMIT: <price or zone>

Стоп:
SL: <price>

Тейки:
TP1: <price> — закрыть 33%, SL в б/у
TP2: <price> — закрыть 33%, SL в б/у
TP3: <price> — закрыть остаток

Отмена:
Не входить MARKET, если цена уже <выше/ниже> <price>.
Отменить идею полностью, если <условие пробоя/закрепления за SL-зоной>.

Причина:
<1–3 коротких предложения: 4H/1H контекст, 15m/1m вход, почему это именно A+>
""".strip()


async def build_aplus_hunter_archive(
    settings: Settings,
    logger: logging.Logger,
    secret_store: SecretStore,
    progress_cb: ProgressCallback | None = None,
    *,
    top_limit: int = 200,
    max_candidates: int = 3,
    score_threshold: float = 90.0,
) -> Path | None:
    utc_build_stamp = utc_stamp()
    scan_stamp = moscow_scan_stamp()
    scan_id = f"aplus-{scan_stamp}-{utc_build_stamp}"
    started_perf = time.perf_counter()
    started_utc = datetime.now(timezone.utc)
    created_msk = (started_utc + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    reporter = PercentReporter("A+ Hunter", progress_cb)
    api_mask = secret_store.load_mexc_api_mask()
    client = MexcSpotClient(settings.mexc_base_url, logger, settings.mexc_market_type)
    # A+ Hunter is a lightweight screener. Keep old 30d scan throttling untouched,
    # but do not spend minutes sleeping between 15m score requests here.
    if settings.mexc_market_type == "futures":
        client.min_request_interval_sec = 0.35
    _aplus_log(logger, scan_id, "START", version=settings.app_version, top_limit=top_limit, score_threshold=score_threshold, max_candidates=max_candidates, throttle=client.min_request_interval_sec)
    try:
        await reporter.report(0, "старт top-200 + forced screener", force=True)
        stage_t0 = time.perf_counter()
        ping_ok = await client.ping()
        server_time = await client.server_time()
        server_ms = int(server_time["serverTime"])
        _aplus_log(logger, scan_id, "API_READY", ping_ok=ping_ok, server_ms=server_ms, elapsed_sec=f"{time.perf_counter()-stage_t0:.2f}")

        await reporter.report(5, "получаю top-200 по ликвидности + forced symbols")
        stage_t0 = time.perf_counter()
        top_symbols, top_tickers, forced_report = await _aplus_top200_symbols(client, logger, top_limit)
        _aplus_log(logger, scan_id, "UNIVERSE_READY", top_tickers=len(top_tickers), universe=len(top_symbols), unique=len(set(top_symbols)), duplicates=len(top_symbols)-len(set(top_symbols)), elapsed_sec=f"{time.perf_counter()-stage_t0:.2f}")
        if not top_symbols:
            _aplus_log(logger, scan_id, "NO_UNIVERSE", elapsed_total_sec=f"{time.perf_counter()-started_perf:.2f}")
            await reporter.report(100, "top-200 + forced universe пустой, A+ candidates: 0", force=True)
            return None

        scored: list[dict[str, Any]] = []
        score_errors: list[dict[str, Any]] = []
        total = len(top_symbols)
        stage_t0 = time.perf_counter()
        _aplus_log(logger, scan_id, "SCORING_START", symbols=total)
        for idx, symbol in enumerate(top_symbols, start=1):
            try:
                item = await _aplus_score_symbol(client, symbol, server_ms, logger)
                scored.append(item)
            except Exception as exc:  # noqa: BLE001
                _aplus_log_error(logger, scan_id, "SCORE_SYMBOL", exc, symbol=symbol, index=idx)
                err_item = {"symbol": symbol, "score": 0.0, "direction": "WAIT", "error": str(exc)[:300]}
                score_errors.append(err_item)
                scored.append(err_item)
            if idx % 20 == 0 or idx == total:
                pct = 5 + (idx / max(1, total)) * 55
                best_score = max((float(x.get("score", 0.0)) for x in scored), default=0.0)
                _aplus_log(logger, scan_id, "SCORING_PROGRESS", scored=idx, total=total, best_score=f"{best_score:.1f}", errors=len(score_errors), elapsed_sec=f"{time.perf_counter()-stage_t0:.2f}")
                await reporter.report(pct, f"scored {idx}/{total}; best_score={best_score:.1f}")

        scored.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
        best_score = float(scored[0].get("score", 0.0)) if scored else 0.0
        _aplus_log(logger, scan_id, "SCORING_DONE", scored=len(scored), best_score=f"{best_score:.1f}", errors=len(score_errors), elapsed_sec=f"{time.perf_counter()-stage_t0:.2f}")
        candidates = [x for x in scored if float(x.get("score", 0.0)) >= score_threshold][:max_candidates]
        if not candidates:
            _aplus_log(logger, scan_id, "NO_CANDIDATES", best_score=f"{best_score:.1f}", threshold=score_threshold, elapsed_total_sec=f"{time.perf_counter()-started_perf:.2f}")
            await reporter.report(100, f"A+ candidates: 0; best_score={best_score:.1f}", force=True)
            return None

        candidate_symbols = [str(x["symbol"]) for x in candidates]
        _aplus_log(logger, scan_id, "CANDIDATES_FOUND", symbols=candidate_symbols, scores=[x.get("score") for x in candidates])
        await reporter.report(65, f"кандидаты найдены: {candidate_symbols}; строю montage")

        build_dir = settings.work_dir / f"scan_build_{utc_build_stamp}" / "chatgpt_scan_aplus_hunter"
        safe_rmtree(build_dir)
        charts_out = build_dir / "charts"
        meta_out = build_dir / "meta"
        charts_out.mkdir(parents=True, exist_ok=True)
        meta_out.mkdir(parents=True, exist_ok=True)

        interval_ms = INTERVAL_MS.get(settings.base_interval, 60_000)
        window = DownloadWindow.last_days_from_end_ms(settings.days_back, server_ms, interval_ms)
        stage_t0 = time.perf_counter()
        _aplus_log(logger, scan_id, "MONTAGE_STAGE_START", candidate_symbols=candidate_symbols)
        exchange_info = await client.exchange_info(candidate_symbols)
        row_counts: dict[str, int] = {}
        chart_files: list[str] = []
        built_symbols: list[str] = []
        built_candidates: list[dict[str, Any]] = []
        warnings: list[str] = []

        for idx, symbol in enumerate(candidate_symbols, start=1):
            symbol_t0 = time.perf_counter()
            await reporter.report(65 + (idx - 1) / max(1, len(candidate_symbols)) * 25, f"скачиваю 30d для montage: {symbol}")
            try:
                _aplus_log(logger, scan_id, "MONTAGE_DOWNLOAD_START", symbol=symbol, index=idx, total=len(candidate_symbols))
                df = await client.download_klines_dataframe(symbol, settings.base_interval, window)
                if df.empty:
                    msg = f"{symbol}: empty 30d data, skipped"
                    warnings.append(msg)
                    _aplus_log(logger, scan_id, "MONTAGE_SKIP_EMPTY", symbol=symbol)
                    continue
                expected_rows = max(1, int((window.end_ms - window.start_ms) // interval_ms))
                actual_days_by_rows = len(df) * interval_ms / (24 * 60 * 60 * 1000)
                if len(df) / expected_rows < settings.min_coverage_ratio and actual_days_by_rows < settings.min_effective_days:
                    msg = f"{symbol}: too little 30d coverage, skipped rows={len(df)} expected={expected_rows}"
                    warnings.append(msg)
                    _aplus_log(logger, scan_id, "MONTAGE_SKIP_COVERAGE", symbol=symbol, rows=len(df), expected=expected_rows, actual_days=f"{actual_days_by_rows:.1f}")
                    continue
                row_counts[symbol] = int(len(df))
                df_ohlcv = _downloaded_df_to_ohlcv_montage(df)
                task_hint = "A+ Hunter: confirm only true A+. MARKET + LIMIT allowed. If not true A+, answer WAIT."
                rel = await asyncio.to_thread(create_montage_for_symbol, symbol, df_ohlcv, charts_out, created_msk, task_hint, logger)
                chart_files.append(rel)
                built_symbols.append(symbol)
                built_candidates.append(next((c for c in candidates if str(c.get("symbol")) == symbol), {"symbol": symbol}))
                _aplus_log(logger, scan_id, "MONTAGE_SYMBOL_DONE", symbol=symbol, rows=len(df), file=rel, elapsed_sec=f"{time.perf_counter()-symbol_t0:.2f}")
                await reporter.report(65 + idx / max(1, len(candidate_symbols)) * 25, f"montage готов: {rel}")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{symbol}: montage failed: {str(exc)[:300]}")
                _aplus_log_error(logger, scan_id, "MONTAGE_SYMBOL", exc, symbol=symbol, index=idx)
                continue

        if not chart_files:
            _aplus_log(logger, scan_id, "NO_CHARTS", warnings=len(warnings), elapsed_total_sec=f"{time.perf_counter()-started_perf:.2f}")
            await reporter.report(100, "кандидаты были, но montage не построен из-за данных; archive skipped", force=True)
            return None

        candidate_symbols = built_symbols
        candidates = built_candidates
        _aplus_log(logger, scan_id, "MONTAGE_STAGE_DONE", charts=len(chart_files), symbols=candidate_symbols, warnings=len(warnings), elapsed_sec=f"{time.perf_counter()-stage_t0:.2f}")

        await reporter.report(93, "пишу A+ Hunter task/manifest")
        task_text = _aplus_hunter_task_text(candidate_symbols, created_msk, candidates)
        (build_dir / "task.txt").write_text(task_text + "\n", encoding="utf-8")
        write_json(build_dir / "screener_report.json", {
            "mode": "aplus_hunter",
            "top_limit": top_limit,
            "score_threshold": score_threshold,
            "max_candidates": max_candidates,
            "candidates": candidates,
            "top_scored_20": scored[:20],
            "top_tickers_count": len(top_tickers),
            "universe_symbols_count": len(top_symbols),
            "forced_symbols": forced_report,
            "score_errors": score_errors[:50],
            "scan_id": scan_id,
            "started_at_utc": started_utc.isoformat(),
            "elapsed_total_sec": round(time.perf_counter() - started_perf, 2),
            "note": "Screener candidates are hints only. ChatGPT must confirm true A+ from montage/task.txt.",
        })
        manifest = {
            "archive_type": "chatgpt_scan_30d_mexc_futures_aplus_hunter",
            "collector_version": settings.app_version,
            "scan_id": scan_id,
            "started_at_utc": started_utc.isoformat(),
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "created_at_utc_plus_3_msk": created_msk,
            "telegram_archive_stamp_utc_plus_3": scan_stamp,
            "exchange": "MEXC_FUTURES_PUBLIC",
            "base_url": settings.mexc_base_url,
            "market_type": settings.mexc_market_type,
            "preset_key": "aplus_hunter",
            "preset_title": "A+ Hunter",
            "requested_symbols": candidate_symbols,
            "symbols": candidate_symbols,
            "symbol_policy": "exact_only_no_fallback: trade the exact listed symbol only",
            "aplus_hunter_universe_policy": "top-200 most liquid USDT futures plus forced resolved symbols without duplicates",
            "forced_symbols": forced_report,
            "base_interval": settings.base_interval,
            "days_back": settings.days_back,
            "download_window": window.as_dict(),
            "min_coverage_ratio": settings.min_coverage_ratio,
            "min_effective_days": settings.min_effective_days,
            "ping_ok": ping_ok,
            "server_time": server_time,
            "api_key_saved_mask": api_mask,
            "row_counts": row_counts,
            "chart_files_count": len(chart_files),
            "chart_files": chart_files,
            "chart_set": {"montage": "one JPG per A+ Hunter candidate: 1D, 4H, 1H, 15m, 1m + info/task panel"},
            "warnings": warnings,
            "score_errors_count": len(score_errors),
            "elapsed_total_sec": round(time.perf_counter() - started_perf, 2),
            "instruction_files": ["task.txt"],
            "screener_report_file": "screener_report.json",
            "answer_rule_for_chatgpt": "Confirm only true A+. If confirmed, return A+ setup with MARKET + LIMIT and anti-chase rule. If not: A+ нет, лучше ещё подождать.",
            "montage_mode": True,
            "aplus_hunter": True,
            "storage_policy": "No parquet/candles are written into A+ Hunter montage archives; downloaded candles are used in memory only to render montage charts.",
        }
        write_json(build_dir / "manifest.json", manifest)
        write_json(meta_out / "exchange_info.json", exchange_info)
        write_json(meta_out / "api_status.json", {
            "mexc_futures_public_ping_ok": ping_ok,
            "mexc_server_time": server_time,
            "api_key_saved_mask": api_mask,
            "note": "Market data uses MEXC public futures endpoints. No place_order/cancel_order/trading endpoints exist in this bot.",
        })

        await reporter.report(98, "упаковываю A+ Hunter zip")
        zip_path = settings.exports_dir / f"chatgpt_scan-aplus_hunter-{scan_stamp}.zip"
        zip_directory(build_dir, zip_path)
        write_json(settings.exports_dir / f"chatgpt_scan-aplus_hunter-{scan_stamp}.sha256.json", {
            "file": zip_path.name,
            "sha256": file_sha256(zip_path),
            "size_bytes": zip_path.stat().st_size,
            "size_human": human_bytes(zip_path.stat().st_size),
            "created_at_utc_plus_3_msk": created_msk,
        })
        _aplus_log(logger, scan_id, "ARCHIVE_READY", file=zip_path.name, size=human_bytes(zip_path.stat().st_size), candidates=len(candidate_symbols), elapsed_total_sec=f"{time.perf_counter()-started_perf:.2f}")
        await reporter.report(100, f"архив A+ Hunter готов: {zip_path.name}, размер={human_bytes(zip_path.stat().st_size)}, candidates={len(candidate_symbols)}", force=True)
        return zip_path
    except Exception as exc:  # noqa: BLE001
        _aplus_log_error(logger, scan_id, "FATAL", exc, elapsed_total_sec=f"{time.perf_counter()-started_perf:.2f}")
        raise
    finally:
        _aplus_log(logger, scan_id, "CLIENT_CLOSE")
        await client.close()


async def _build_scan_archive_montage(
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
    charts_out = build_dir / "charts"
    meta_out = build_dir / "meta"
    charts_out.mkdir(parents=True, exist_ok=True)
    meta_out.mkdir(parents=True, exist_ok=True)

    reporter = PercentReporter(f"Scan {preset.title}", progress_cb)
    logger.info("Starting ChatGPT montage scan preset=%s symbols=%s", preset.key, preset.symbols)
    await reporter.report(0, f"старт montage. MEXC Futures, 1m за {settings.days_back} дней, symbols={preset.symbols}", force=True)

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
        await reporter.report(10, f"MEXC доступен, начинаю сбор свечей в память: {resolved_symbols}")

        row_counts: dict[str, int] = {}
        chart_files: list[str] = []
        warnings: list[str] = []
        symbols_count = max(1, len(resolved_symbols))
        download_span = 62.0
        charts_span = 20.0
        expected_charts = max(1, len(resolved_symbols))
        chart_done = 0

        async def chart_done_cb(rel_path: str) -> None:
            nonlocal chart_done
            chart_done += 1
            pct = 74 + min(charts_span, chart_done / expected_charts * charts_span)
            await reporter.report(pct, f"montage {chart_done}/{expected_charts}; последний: {rel_path}")

        for idx, symbol in enumerate(resolved_symbols):
            symbol_base = 10.0 + idx * (download_span / symbols_count)
            symbol_span = download_span / symbols_count
            await reporter.report(symbol_base, f"скачиваю {symbol} {settings.base_interval} за {settings.days_back} дней")

            async def symbol_progress(symbol_pct: float, rows: int, expected: int, symbol_name: str = symbol) -> None:
                absolute_pct = symbol_base + symbol_span * (symbol_pct / 100.0)
                await reporter.report(absolute_pct, f"{symbol_name}: {rows:,}/{expected:,} свечей")

            df = await client.download_klines_dataframe(symbol, settings.base_interval, window, progress_cb=symbol_progress)
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
                        f"примерно {actual_days_by_rows:.1f} дней. Продолжаю сбор: данных достаточно для montage."
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

            row_counts[symbol] = len(df)
            await reporter.report(74, f"строю montage для {symbol}")
            df_ohlcv = _downloaded_df_to_ohlcv_montage(df)
            task_hint = "Swing LONG + Swing SHORT, rank assets 1..N, classes A+/A/WAIT only."
            rel = await asyncio.to_thread(create_montage_for_symbol, symbol, df_ohlcv, charts_out, created_msk, task_hint, logger)
            chart_files.append(rel)
            await chart_done_cb(rel)

        await reporter.report(95, "пишу manifest/task")
        task_text = _montage_task_text(preset, created_msk)
        (build_dir / "task.txt").write_text(task_text + "\n", encoding="utf-8")

        manifest = {
            "archive_type": "chatgpt_scan_30d_mexc_futures_montage",
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
            "candle_files": {},
            "row_counts": row_counts,
            "chart_files_count": len(chart_files),
            "chart_files": chart_files,
            "chart_set": {"montage": "one JPG per asset: 1D, 4H, 1H, 15m, 1m + info/task panel"},
            "warnings": warnings,
            "request_policy": {
                "endpoint": "/api/v1/contract/kline/{symbol}",
                "base_url": settings.mexc_base_url,
                "chunk_limit_1m_candles": 2000,
                "rate_limit_handling": "On HTTP/app-level too-frequent/rate-limit errors, increase request pause and retry.",
                "symbol_policy": "Exact symbols only. No automatic fallback/substitution.",
            },
            "instruction_files": ["task.txt"],
            "answer_rule_for_chatgpt": "Return ranked swing LONG and SHORT setups using task.txt; classes A+/A/WAIT only.",
            "montage_mode": True,
            "storage_policy": "No parquet/candles are written into montage archives; downloaded candles are used in memory only to render montage charts.",
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
        logger.info("Montage scan archive ready: %s size=%s", zip_path, human_bytes(zip_path.stat().st_size))
        await reporter.report(100, f"архив montage готов: {zip_path.name}, размер={human_bytes(zip_path.stat().st_size)}, montage={len(chart_files)}", force=True)
        return zip_path
    finally:
        await client.close()


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
    available_days = len(df_1m) / 1440.0
    window_label = f"requested 30d / available ~{available_days:.1f}d"

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
    await plot(df_1d, f"{symbol} 1D — {window_label}", charts_out / symbol / f"{symbol}_1D.png", figsize=(18, 9), mav=(7, 20))

    df_4h = resample_ohlcv(df_1m, "4h")
    await plot(df_4h, f"{symbol} 4H — {window_label}", charts_out / symbol / f"{symbol}_4H.png", figsize=(18, 9), mav=(20, 50))

    df_1h = resample_ohlcv(df_1m, "1h")
    await plot(df_1h, f"{symbol} 1H — {window_label}", charts_out / symbol / f"{symbol}_1H.png", figsize=(18, 9), mav=(20, 50, 200))

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
    montage_mode: bool = False,
) -> Path:
    if montage_mode:
        return await _build_scan_archive_montage(settings, logger, secret_store, preset, progress_cb)

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
        setup_format_text = _setup_format_text(preset)
        (build_dir / "task.txt").write_text(task_text + "\n", encoding="utf-8")
        (build_dir / "setup_format.txt").write_text(setup_format_text + "\n", encoding="utf-8")

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
            "instruction_files": ["task.txt", "setup_format.txt"],
            "answer_rule_for_chatgpt": "Return only ready setup using setup_format.txt. If no setup: wait, сейчас лучше не входить, подожди и пришли новый архив.",
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
