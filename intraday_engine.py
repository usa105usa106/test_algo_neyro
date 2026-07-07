from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd


MIN_INTRADAY_GREEN_QUALITY = 68


def _fmt(value: float, digits: int = 2) -> str:
    if not np.isfinite(value):
        return "n/a"
    if abs(value) >= 1000:
        return f"{value:,.{digits}f}"
    if abs(value) >= 100:
        return f"{value:.{digits}f}"
    if abs(value) >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}"


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if not np.isfinite(value):
        return lo
    return float(max(lo, min(hi, value)))


def _safe_pct(a: float, b: float) -> float:
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return 0.0
    return float((a - b) / b * 100.0)


def _norm_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize downloaded MEXC dataframe to OHLCV with UTC datetime index."""
    if df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "QuoteVolume"])
    out = df.copy()
    if "datetime_utc" in out.columns:
        idx = pd.to_datetime(out["datetime_utc"], utc=True)
    elif "open_time" in out.columns:
        idx = pd.to_datetime(out["open_time"], unit="ms", utc=True)
    else:
        idx = pd.to_datetime(out.index, utc=True)
    out.index = idx
    out = out[["open", "high", "low", "close", "volume", "quote_volume"]].sort_index()
    out.columns = ["Open", "High", "Low", "Close", "Volume", "QuoteVolume"]
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
        "QuoteVolume": "sum",
    }
    return df.resample(rule).agg(agg).dropna(subset=["Open", "High", "Low", "Close"])


def _vwap(df: pd.DataFrame) -> float:
    if df.empty:
        return float("nan")
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].replace(0, np.nan)
    denom = float(vol.sum())
    if not np.isfinite(denom) or denom <= 0:
        return float(df["Close"].iloc[-1])
    return float((typical * vol).sum() / denom)


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < 2:
        return 0.0
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    value = tr.tail(period).mean()
    return float(value) if np.isfinite(value) else 0.0


def _close_location(df: pd.DataFrame) -> pd.Series:
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    loc = (df["Close"] - df["Low"]) / rng
    return loc.clip(0, 1).fillna(0.5)


def _pressure_scores(df_1m: pd.DataFrame, df_15m: pd.DataFrame, vwap: float) -> tuple[int, int, int]:
    recent_1m = df_1m.tail(60)
    recent_15m = df_15m.tail(32)
    if recent_1m.empty:
        return 50, 50, 50

    loc_1m = _close_location(recent_1m)
    loc_15m = _close_location(recent_15m) if not recent_15m.empty else loc_1m
    green = recent_1m["Close"] > recent_1m["Open"]
    red = recent_1m["Close"] < recent_1m["Open"]
    vol = recent_1m["Volume"].replace(0, np.nan)
    green_vol = float(vol[green].sum()) if green.any() else 0.0
    red_vol = float(vol[red].sum()) if red.any() else 0.0
    vol_total = green_vol + red_vol
    green_vol_share = green_vol / vol_total if vol_total > 0 else 0.5
    red_vol_share = red_vol / vol_total if vol_total > 0 else 0.5

    price = float(df_1m["Close"].iloc[-1])
    ret_60 = _safe_pct(price, float(recent_1m["Open"].iloc[0]))
    above_vwap_bonus = 10 if np.isfinite(vwap) and price > vwap else 0
    below_vwap_bonus = 10 if np.isfinite(vwap) and price < vwap else 0

    buyer = 25 + loc_1m.mean() * 25 + loc_15m.mean() * 15 + green_vol_share * 20 + max(0.0, ret_60) * 3 + above_vwap_bonus
    seller = 25 + (1 - loc_1m.mean()) * 25 + (1 - loc_15m.mean()) * 15 + red_vol_share * 20 + max(0.0, -ret_60) * 3 + below_vwap_bonus

    # Absorption: high volume / strong candle attempts, but small net result or rejection tails.
    volume_z = 0.0
    if len(recent_1m) >= 20:
        vmean = recent_1m["Volume"].tail(60).mean()
        vstd = recent_1m["Volume"].tail(60).std()
        if np.isfinite(vstd) and vstd > 0:
            volume_z = float((recent_1m["Volume"].tail(10).mean() - vmean) / vstd)
    upper_wick = ((recent_1m["High"] - recent_1m[["Open", "Close"]].max(axis=1)) / (recent_1m["High"] - recent_1m["Low"]).replace(0, np.nan)).tail(20).mean()
    lower_wick = ((recent_1m[["Open", "Close"]].min(axis=1) - recent_1m["Low"]) / (recent_1m["High"] - recent_1m["Low"]).replace(0, np.nan)).tail(20).mean()
    wick_pressure = max(float(upper_wick if np.isfinite(upper_wick) else 0), float(lower_wick if np.isfinite(lower_wick) else 0)) * 35
    low_progress = max(0.0, 10.0 - abs(ret_60) * 10.0)
    absorption = 25 + max(0.0, volume_z) * 10 + wick_pressure + low_progress

    return int(round(_clamp(buyer))), int(round(_clamp(seller))), int(round(_clamp(absorption)))


def _structure_score(df: pd.DataFrame, direction: str) -> int:
    if len(df) < 8:
        return 0
    recent = df.tail(12)
    closes = recent["Close"].to_numpy(dtype=float)
    highs = recent["High"].to_numpy(dtype=float)
    lows = recent["Low"].to_numpy(dtype=float)
    x = np.arange(len(closes), dtype=float)
    slope = np.polyfit(x, closes, 1)[0] if len(closes) >= 2 else 0.0
    atr = max(_atr(recent, 8), 1e-12)
    norm_slope = slope / atr
    if direction == "long":
        hh = highs[-1] >= np.nanmax(highs[: max(1, len(highs) // 2)])
        hl = lows[-1] >= np.nanmin(lows[: max(1, len(lows) // 2)])
        return int(_clamp(norm_slope * 30 + (15 if hh else 0) + (15 if hl else 0), 0, 40))
    ll = lows[-1] <= np.nanmin(lows[: max(1, len(lows) // 2)])
    lh = highs[-1] <= np.nanmax(highs[: max(1, len(highs) // 2)])
    return int(_clamp(-norm_slope * 30 + (15 if ll else 0) + (15 if lh else 0), 0, 40))


def _closed_frame(df: pd.DataFrame, latest_ts: pd.Timestamp, rule: str) -> pd.DataFrame:
    if df.empty:
        return df
    latest = pd.Timestamp(latest_ts)
    delta = pd.Timedelta(rule)
    return df[df.index + delta <= latest]


def _rejection_confirmation(df_1m: pd.DataFrame, df_15m: pd.DataFrame, direction: str, vwap: float, latest_ts: pd.Timestamp | None = None) -> bool:
    """Lightweight confirmation gate for Intraday MANUAL_REVIEW.

    It prevents passive limit ideas from being promoted to green just because price is
    near a zone. A green candidate must show at least a small rejection/hold on
    closed 1m/15m candles. Otherwise the decision stays WAIT_CONFIRMATION.
    """
    if df_1m.empty or df_15m.empty or not np.isfinite(vwap):
        return False
    if latest_ts is not None:
        df_15m = _closed_frame(df_15m, latest_ts, "15min")
    if df_15m.empty:
        return False
    recent_1m = df_1m.tail(10)
    last15 = df_15m.iloc[-1]
    rng15 = float(last15["High"] - last15["Low"])
    if rng15 <= 0 or not np.isfinite(rng15):
        return False
    body_top = float(max(last15["Open"], last15["Close"]))
    body_bottom = float(min(last15["Open"], last15["Close"]))
    upper_wick = float(last15["High"] - body_top) / rng15
    lower_wick = float(body_bottom - last15["Low"]) / rng15
    close = float(last15["Close"])
    open_ = float(last15["Open"])

    if direction == "short":
        touched_zone = bool(last15["High"] >= vwap or recent_1m["High"].max() >= vwap)
        red_or_below = close < open_ or close < vwap
        return touched_zone and red_or_below and (upper_wick >= 0.20 or close < vwap)

    touched_zone = bool(last15["Low"] <= vwap or recent_1m["Low"].min() <= vwap)
    green_or_above = close > open_ or close > vwap
    return touched_zone and green_or_above and (lower_wick >= 0.20 or close > vwap)


def _symbol_base(symbol: str) -> str:
    return symbol.upper().replace("_USDT", "")


def _trend_pullback_min_edge(symbol: str) -> int:
    base = _symbol_base(symbol)
    if base in {"BTC", "ETH", "SOL"}:
        return 10
    if base in {"XAU", "XAUT", "GOLD", "SILVER"}:
        return 14
    if base in {"USOIL", "OIL"}:
        return 12
    if base in {"XRP", "ADA", "BCH", "POL", "MATIC", "DOT", "LINK", "AVAX", "BNB", "LTC", "DOGE"}:
        return 12
    return 13


def _day_edge_room_ok(symbol: str, direction: str, entry_ref: float, day_high: float, day_low: float, atr15: float) -> tuple[bool, str | None]:
    """Soft RR sanity check for green Intraday A.

    It blocks only obvious middle-of-range entries where the nearest day edge is
    very close compared with structural risk to the opposite edge. It is deliberately
    softer than the first fix so Intraday is not killed.
    """
    if not all(np.isfinite(x) for x in [entry_ref, day_high, day_low]) or day_high <= day_low:
        return True, None
    base = _symbol_base(symbol)
    if base in {"BTC", "ETH", "SOL"}:
        min_rr_to_edge = 0.25
    elif base in {"XAU", "XAUT", "GOLD", "SILVER"}:
        min_rr_to_edge = 0.35
    else:
        min_rr_to_edge = 0.30
    buffer = max(_data_tolerance(symbol, entry_ref), float(atr15 or 0.0) * 0.10)
    if direction == "long":
        risk = entry_ref - day_low
        room = day_high - entry_ref
        if risk > buffer and room <= buffer:
            return False, f"entry {_fmt(entry_ref)} уже у day high {_fmt(day_high)}; нужен breakout+retest, не догонять"
        if risk > buffer and room / risk < min_rr_to_edge:
            return False, f"day high {_fmt(day_high)} слишком близко к entry {_fmt(entry_ref)} относительно риска до day low {_fmt(day_low)}"
    else:
        risk = day_high - entry_ref
        room = entry_ref - day_low
        if risk > buffer and room <= buffer:
            return False, f"entry {_fmt(entry_ref)} уже у day low {_fmt(day_low)}; нужен breakdown+retest, не догонять"
        if risk > buffer and room / risk < min_rr_to_edge:
            return False, f"day low {_fmt(day_low)} слишком близко к entry {_fmt(entry_ref)} относительно риска до day high {_fmt(day_high)}"
    return True, None


def _htf_trend_ok(symbol: str, direction: str, df_4h: pd.DataFrame) -> tuple[bool, str | None]:
    """Do not promote Trend Pullback to green directly against strong 4H structure.

    This is a soft higher-timeframe sanity gate, not a trade killer: it only blocks
    obvious 4H opposition. Neutral/mixed 4H still passes so Intraday does not go dry.
    """
    if df_4h.empty or len(df_4h) < 8:
        return True, None
    long4 = _structure_score(df_4h, "long")
    short4 = _structure_score(df_4h, "short")
    if direction == "long" and short4 >= long4 + 10 and short4 >= 25:
        return False, f"4H против LONG: 4H short_score {short4} > long_score {long4}; нужен новый reclaim/слом структуры"
    if direction == "short" and long4 >= short4 + 10 and long4 >= 25:
        return False, f"4H против SHORT: 4H long_score {long4} > short_score {short4}; нужен breakdown/ретест, не ранний шорт"
    return True, None


def _data_tolerance(symbol: str, price: float) -> float:
    base = symbol.upper().replace("_USDT", "")
    if base in {"XAU", "XAUT", "GOLD"}:
        return 1.0
    if base in {"USOIL", "OIL"}:
        return 0.05
    if base == "SILVER":
        return 0.02
    if base == "BTC":
        return max(5.0, price * 0.00015)
    if base == "ETH":
        return max(0.5, price * 0.0002)
    return max(1e-8, abs(price) * 0.0002)


def _sweep_penetration_tolerance(symbol: str, price: float, atr15: float) -> float:
    """Minimum real penetration beyond a prior edge for a sweep.

    Data tolerance is intentionally large for some symbols because it is also used
    to compare chart/report levels. For sweep detection we need a smaller, but
    still non-zero, buffer so equal highs/lows or tiny tick touches do not become
    green reversal candidates.
    """
    base_tick = _data_tolerance(symbol, price) * 0.25
    atr_part = float(atr15 or 0.0) * 0.01
    return max(1e-8, base_tick, atr_part)


@dataclass(frozen=True)
class IntradayReport:
    symbol: str
    price: float
    regime: str
    allowed_direction: str
    decision: str
    playbook: str
    buyer_pressure: int
    seller_pressure: int
    absorption: int
    trap_risk: int
    late_risk: int
    long_score: int
    short_score: int
    quality_score: int
    vwap: float
    day_open: float
    day_high: float
    day_low: float
    high_24h: float
    low_24h: float
    distance_to_vwap_pct: float
    distance_to_24h_high_pct: float
    distance_to_24h_low_pct: float
    comment: str
    archive_reason: str | None = None
    day_high_msk: float = float("nan")
    day_low_msk: float = float("nan")
    rolling_24h_high: float = float("nan")
    rolling_24h_low: float = float("nan")
    visible_1m_high: float = float("nan")
    visible_1m_low: float = float("nan")
    visible_15m_high: float = float("nan")
    visible_15m_low: float = float("nan")
    data_warning: bool = False
    data_warning_reason: str | None = None
    session_age_min: int = 0
    low_liquidity_session: bool = False

    @property
    def is_green(self) -> bool:
        return self.decision == "MANUAL_REVIEW"

    @property
    def color_emoji(self) -> str:
        if self.decision == "MANUAL_REVIEW":
            return "🟢"
        if self.decision in {"NO_TRADE"}:
            return "🔴"
        return "🟡"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def short_line(self) -> str:
        rank = f" / rank {self.quality_score}" if self.is_green else ""
        return f"{self.color_emoji} {self.symbol.replace('_USDT', '')}: {self.regime} / {self.decision}{rank}"

    def details_text(self) -> str:
        return (
            f"{self.color_emoji} {self.symbol}\n"
            f"Режим: {self.regime}\n"
            f"Разрешено: {self.allowed_direction}\n"
            f"Давление: buyers {self.buyer_pressure} / sellers {self.seller_pressure}\n"
            f"Риск: trap {self.trap_risk} / late {self.late_risk}\n"
            f"Rank score: {self.quality_score}\n"
            f"Сценарий: {self.playbook}\n"
            f"Комментарий: {self.comment}"
            + (f"\nDATA_WARNING: {self.data_warning_reason}" if self.data_warning else "")
            + ("\nSESSION_WARNING: first MSK hour / low-liquidity gate" if self.low_liquidity_session else "")
        )


def analyze_intraday_symbol(symbol: str, raw_df_1m: pd.DataFrame) -> tuple[IntradayReport, pd.DataFrame, dict[str, pd.DataFrame]]:
    df_1m = _norm_df(raw_df_1m)
    if df_1m.empty or len(df_1m) < 120:
        report = IntradayReport(
            symbol=symbol,
            price=float("nan"),
            regime="NO_DATA",
            allowed_direction="WAIT",
            decision="NO_TRADE",
            playbook="none",
            buyer_pressure=0,
            seller_pressure=0,
            absorption=0,
            trap_risk=100,
            late_risk=100,
            long_score=0,
            short_score=0,
            quality_score=0,
            vwap=float("nan"),
            day_open=float("nan"),
            day_high=float("nan"),
            day_low=float("nan"),
            high_24h=float("nan"),
            low_24h=float("nan"),
            distance_to_vwap_pct=0,
            distance_to_24h_high_pct=0,
            distance_to_24h_low_pct=0,
            comment="Недостаточно 1m свечей для интрадей-оценки.",
        )
        return report, df_1m, {}

    frames = {
        "1m": df_1m.tail(24 * 60),
        "15m": resample_ohlcv(df_1m, "15min"),
        "1h": resample_ohlcv(df_1m, "1h"),
        "4h": resample_ohlcv(df_1m, "4h"),
        "1D": resample_ohlcv(df_1m, "1d"),
    }
    df_15m = frames["15m"]
    df_1h = frames["1h"]
    df_4h = frames["4h"]
    price = float(df_1m["Close"].iloc[-1])
    latest_ts = df_1m.index[-1]
    df_15m_closed = _closed_frame(df_15m, latest_ts, "15min")
    if df_15m_closed.empty:
        df_15m_closed = df_15m
    latest_msk = latest_ts.tz_convert("Europe/Moscow")
    day_start_msk = latest_msk.normalize()
    day_start_utc = day_start_msk.tz_convert("UTC")
    day_df = df_1m[df_1m.index >= day_start_utc]
    if day_df.empty:
        day_df = df_1m.tail(24 * 60)
    session_age_min = int(max(0, (latest_msk - day_start_msk).total_seconds() // 60))
    low_liquidity_session = session_age_min < 60
    day_open = float(day_df["Open"].iloc[0])
    day_high = float(day_df["High"].max())
    day_low = float(day_df["Low"].min())
    # One source of truth: all levels come from normalized 1m candles.
    # The montage can show different windows (1m last 24h, 15m last ~7d), so report
    # stores these levels separately instead of mixing them. DATA_WARNING compares
    # only the SAME 24h window after resampling, not 24h vs visible 7d chart lows.
    visible_1m = frames["1m"]
    visible_15m = df_15m[df_15m.index >= latest_ts - pd.Timedelta(days=7)]
    if visible_15m.empty:
        visible_15m = df_15m
    high_24h = float(visible_1m["High"].max())
    low_24h = float(visible_1m["Low"].min())
    visible_1m_high = high_24h
    visible_1m_low = low_24h
    visible_15m_high = float(visible_15m["High"].max()) if not visible_15m.empty else high_24h
    visible_15m_low = float(visible_15m["Low"].min()) if not visible_15m.empty else low_24h
    vwap = _vwap(day_df)

    tolerance = _data_tolerance(symbol, price)
    data_warning_reason = None
    check_15m_24h = resample_ohlcv(visible_1m, "15min")
    if not check_15m_24h.empty:
        check_15m_24h_high = float(check_15m_24h["High"].max())
        check_15m_24h_low = float(check_15m_24h["Low"].min())
        if abs(high_24h - check_15m_24h_high) > tolerance or abs(low_24h - check_15m_24h_low) > tolerance:
            data_warning_reason = (
                f"same-window 1m/15m 24h high/low mismatch: "
                f"1m_24h={_fmt(high_24h)}/{_fmt(low_24h)} "
                f"15m_from_1m_24h={_fmt(check_15m_24h_high)}/{_fmt(check_15m_24h_low)} "
                f"tolerance={_fmt(tolerance)}"
            )
    data_warning = data_warning_reason is not None

    buyer, seller, absorption = _pressure_scores(df_1m, df_15m_closed, vwap)
    atr15 = _atr(df_15m, 14)
    atr_pct = atr15 / price * 100 if price else 0.0
    dist_vwap_pct = _safe_pct(price, vwap) if np.isfinite(vwap) else 0.0
    dist_high_pct = abs(_safe_pct(high_24h, price))
    dist_low_pct = abs(_safe_pct(price, low_24h))

    near_24h_high = dist_high_pct <= max(0.10, atr_pct * 0.8)
    near_24h_low = dist_low_pct <= max(0.10, atr_pct * 0.8)
    extended_up = dist_vwap_pct > max(0.25, atr_pct * 1.2)
    extended_down = dist_vwap_pct < -max(0.25, atr_pct * 1.2)

    recent_15 = df_15m_closed.tail(4)
    impulse_up = False
    impulse_down = False
    if len(recent_15) >= 2 and atr15 > 0:
        last_body = float(abs(recent_15["Close"].iloc[-1] - recent_15["Open"].iloc[-1]))
        impulse_up = recent_15["Close"].iloc[-1] > recent_15["Open"].iloc[-1] and last_body > atr15 * 0.9
        impulse_down = recent_15["Close"].iloc[-1] < recent_15["Open"].iloc[-1] and last_body > atr15 * 0.9

    late_long = (35 if near_24h_high else 0) + (25 if extended_up else 0) + (20 if impulse_up else 0)
    late_short = (35 if near_24h_low else 0) + (25 if extended_down else 0) + (20 if impulse_down else 0)
    late_risk = int(_clamp(max(late_long, late_short) + max(0, absorption - 65) * 0.4))

    # Sweep detection: use only a CLOSED 15m candle and require real penetration.
    # Do not promote a live/unfinished 15m wick or an equal-high/equal-low touch
    # to Intraday A before the candle closes.
    last_15 = df_15m_closed.tail(2)
    sweep_up = False
    sweep_down = False
    if len(last_15) >= 1:
        candle = last_15.iloc[-1]
        candle_start = pd.Timestamp(candle.name)
        hist_before_sweep = frames["1m"][frames["1m"].index < candle_start]
        if len(hist_before_sweep) > 2:
            prev_24h_high = float(hist_before_sweep["High"].max())
            prev_24h_low = float(hist_before_sweep["Low"].min())
        else:
            prev_24h_high = high_24h
            prev_24h_low = low_24h
        sweep_buffer = _sweep_penetration_tolerance(symbol, price, atr15)
        sweep_up = bool(candle["High"] > prev_24h_high + sweep_buffer and candle["Close"] < prev_24h_high)
        sweep_down = bool(candle["Low"] < prev_24h_low - sweep_buffer and candle["Close"] > prev_24h_low)

    trap_risk = int(_clamp(absorption * 0.55 + late_risk * 0.35 + (25 if sweep_up or sweep_down else 0)))

    long_score = 0
    short_score = 0
    if np.isfinite(vwap):
        long_score += 20 if price > vwap else 0
        short_score += 20 if price < vwap else 0
    long_score += 15 if price > day_open else 0
    short_score += 15 if price < day_open else 0
    long_score += _structure_score(df_1h, "long")
    short_score += _structure_score(df_1h, "short")
    long_score += _structure_score(df_15m, "long") // 2
    short_score += _structure_score(df_15m, "short") // 2
    long_score += 15 if buyer >= seller + 12 else 0
    short_score += 15 if seller >= buyer + 12 else 0
    long_score -= 10 if late_long >= 60 else 0
    short_score -= 10 if late_short >= 60 else 0
    long_score = int(_clamp(long_score))
    short_score = int(_clamp(short_score))

    range_width_pct = _safe_pct(day_high, day_low)
    score_gap = abs(long_score - short_score)
    if sweep_up or sweep_down:
        regime = "SWEEP"
    elif long_score >= 70 and long_score >= short_score + 20:
        regime = "TREND_LONG"
    elif short_score >= 70 and short_score >= long_score + 20:
        regime = "TREND_SHORT"
    elif score_gap <= 15:
        regime = "TRANSITION"
    elif trap_risk >= 75:
        regime = "CHOP"
    else:
        regime = "RANGE"

    allowed = "WAIT"
    playbook = "none"
    decision = "WAIT"
    comment = "Направление не подтверждено. Ждать новый режим."
    archive_reason = None

    near_vwap = abs(dist_vwap_pct) <= max(0.08, atr_pct * 0.5)
    strict_trap_ok = trap_risk <= 35
    strict_late_ok = late_risk <= 35
    long_pressure_edge = buyer - seller
    short_pressure_edge = seller - buyer
    long_confirmed = _rejection_confirmation(df_1m, df_15m, "long", vwap, latest_ts)
    short_confirmed = _rejection_confirmation(df_1m, df_15m, "short", vwap, latest_ts)
    long_edge_min = _trend_pullback_min_edge(symbol)
    short_edge_min = _trend_pullback_min_edge(symbol)
    sweep_edge_min = max(10, _trend_pullback_min_edge(symbol) - 2)
    long_htf_ok, long_htf_reason = _htf_trend_ok(symbol, "long", df_4h)
    short_htf_ok, short_htf_reason = _htf_trend_ok(symbol, "short", df_4h)
    long_room_ok, long_room_reason = _day_edge_room_ok(symbol, "long", price, day_high, day_low, atr15)
    short_room_ok, short_room_reason = _day_edge_room_ok(symbol, "short", price, day_high, day_low, atr15)
    pullback_watch_long = regime == "TREND_LONG" and near_vwap and long_score >= 70 and long_pressure_edge >= 8 and trap_risk <= 45 and late_risk <= 45
    pullback_watch_short = regime == "TREND_SHORT" and near_vwap and short_score >= 70 and short_pressure_edge >= 8 and trap_risk <= 45 and late_risk <= 45
    pullback_ok_long = pullback_watch_long and long_score >= 75 and long_score >= short_score + 25 and long_pressure_edge >= long_edge_min and strict_trap_ok and strict_late_ok and long_confirmed and long_room_ok and long_htf_ok and not data_warning
    pullback_ok_short = pullback_watch_short and short_score >= 75 and short_score >= long_score + 25 and short_pressure_edge >= short_edge_min and strict_trap_ok and strict_late_ok and short_confirmed and short_room_ok and short_htf_ok and not data_warning

    if regime == "TREND_LONG":
        allowed = "LONG_ONLY"
        playbook = "Trend Pullback"
        if pullback_ok_long:
            decision = "MANUAL_REVIEW"
            comment = f"Лонг Intraday A: тренд + откат к VWAP/зоне + подтверждение rejection/hold. Цена {_fmt(price)}."
            archive_reason = "Intraday A Trend Pullback LONG для ручной проверки"
        elif pullback_watch_long:
            decision = "WAIT_CONFIRMATION"
            if long_pressure_edge < long_edge_min:
                comment = f"Лонг-сценарий есть, но pressure edge слабый (+{long_pressure_edge}, нужно >= {long_edge_min}). Ордер заранее не ставить."
            elif not long_room_ok:
                comment = f"Лонг-сценарий есть, но RR/room слабый: {long_room_reason}. Ждать пробой/ретест, не входить из середины."
            elif not long_htf_ok:
                comment = f"Лонг-сценарий есть, но старший 4H фильтр против: {long_htf_reason}. Ордер заранее не ставить."
            else:
                comment = "Лонг-сценарий есть, но подтверждения недостаточно. Ордер заранее не ставить: ждать 5m/15m rejection/hold."
        else:
            decision = "WAIT_PULLBACK"
            comment = "Лонг-направление есть, но вход сейчас не созрел/может быть поздним. Ждать откат и подтверждение."
    elif regime == "TREND_SHORT":
        allowed = "SHORT_ONLY"
        playbook = "Trend Pullback"
        if pullback_ok_short:
            decision = "MANUAL_REVIEW"
            comment = f"Шорт Intraday A: тренд + откат к VWAP/зоне + подтверждение rejection/hold. Цена {_fmt(price)}."
            archive_reason = "Intraday A Trend Pullback SHORT для ручной проверки"
        elif pullback_watch_short:
            decision = "WAIT_CONFIRMATION"
            if short_pressure_edge < short_edge_min:
                comment = f"Шорт-сценарий есть, но pressure edge слабый (+{short_pressure_edge}, нужно >= {short_edge_min}). Ордер заранее не ставить."
            elif not short_room_ok:
                comment = f"Шорт-сценарий есть, но RR/room слабый: {short_room_reason}. Ждать пробой/ретест, не входить из середины."
            elif not short_htf_ok:
                comment = f"Шорт-сценарий есть, но старший 4H фильтр против: {short_htf_reason}. Ордер заранее не ставить."
            else:
                comment = "Шорт-сценарий есть, но подтверждения недостаточно. Ордер заранее не ставить: ждать 5m/15m rejection/hold."
        else:
            decision = "WAIT_PULLBACK"
            comment = "Шорт-направление есть, но вход сейчас не созрел/может быть поздним. Ждать откат и подтверждение."
    elif regime == "SWEEP":
        playbook = "Sweep Reversal"
        if sweep_down:
            allowed = "LONG_ONLY_AFTER_CONFIRMATION"
            if buyer >= seller + sweep_edge_min and trap_risk <= 35 and late_risk <= 35 and long_confirmed and not data_warning:
                decision = "MANUAL_REVIEW"
                comment = "Sweep down Intraday A: low сняли, цена вернулась, выкуп подтверждён. Проверить только LIMIT после подтверждения."
                archive_reason = "Intraday A Sweep down reversal для ручной проверки"
            else:
                decision = "WAIT_SWEEP_CONFIRMATION"
                comment = f"Похоже на sweep вниз, но подтверждения/pressure ещё мало (edge +{buyer - seller}, нужно >= {sweep_edge_min}). Ордер заранее не ставить, ждать закрытие/закрепление."
        elif sweep_up:
            allowed = "SHORT_ONLY_AFTER_CONFIRMATION"
            if seller >= buyer + sweep_edge_min and trap_risk <= 35 and late_risk <= 35 and short_confirmed and not data_warning:
                decision = "MANUAL_REVIEW"
                comment = "Sweep up Intraday A: high сняли, цена вернулась, продавец подтверждён. Проверить только LIMIT после подтверждения."
                archive_reason = "Intraday A Sweep up reversal для ручной проверки"
            else:
                decision = "WAIT_SWEEP_CONFIRMATION"
                comment = f"Похоже на sweep вверх, но подтверждения/pressure ещё мало (edge +{seller - buyer}, нужно >= {sweep_edge_min}). Ордер заранее не ставить, ждать закрытие/закрепление."
    elif regime == "TRANSITION":
        allowed = "WAIT"
        playbook = "none"
        decision = "WAIT"
        comment = "Переходный режим: long/short scores близко или тренд меняется. Сделку не открывать, ждать подтверждение."
    elif regime == "RANGE":
        allowed = "BOTH_FROM_EDGES"
        playbook = "Range Edge"
        edge_low = dist_low_pct <= max(0.12, atr_pct * 0.8)
        edge_high = dist_high_pct <= max(0.12, atr_pct * 0.8)
        if edge_low and buyer >= seller + 15 and trap_risk <= 35 and late_risk <= 35 and long_confirmed and not data_warning:
            decision = "MANUAL_REVIEW"
            comment = "Range Edge Intraday A: нижний край диапазона + подтверждённый выкуп. Проверить range-long вручную."
            archive_reason = "Intraday A Range Edge LONG для ручной проверки"
        elif edge_high and seller >= buyer + 15 and trap_risk <= 35 and late_risk <= 35 and short_confirmed and not data_warning:
            decision = "MANUAL_REVIEW"
            comment = "Range Edge Intraday A: верхний край диапазона + подтверждённый продавец. Проверить range-short вручную."
            archive_reason = "Intraday A Range Edge SHORT для ручной проверки"
        elif edge_low or edge_high:
            decision = "WAIT_CONFIRMATION"
            comment = "Цена у края диапазона, но подтверждения недостаточно. Ордер заранее не ставить, ждать rejection/hold."
        else:
            decision = "WAIT_EDGE"
            comment = "Диапазон. В середине не торговать, ждать верх/низ диапазона."
    else:
        allowed = "WAIT"
        playbook = "none"
        decision = "NO_TRADE"
        comment = "Грязная пила/ловушка. Нет нормального преимущества."

    if data_warning and decision == "MANUAL_REVIEW":
        decision = "WAIT"
        archive_reason = None
        comment = f"DATA_WARNING: {data_warning_reason}. Архив можно смотреть, но сделку не давать до проверки данных."

    if low_liquidity_session and decision == "MANUAL_REVIEW":
        decision = "WAIT_CONFIRMATION"
        archive_reason = None
        comment = f"Первый час MSK-сессии ({session_age_min} мин): high/low дня ещё нестабильны, возможны выносы. Ордер заранее не ставить, ждать следующий стабильный скан/rejection."

    if decision == "MANUAL_REVIEW":
        if "LONG" in allowed:
            pressure_edge = buyer - seller
            direction_score = long_score
        elif "SHORT" in allowed:
            pressure_edge = seller - buyer
            direction_score = short_score
        else:
            pressure_edge = abs(buyer - seller)
            direction_score = max(long_score, short_score)
        playbook_bonus = {"Sweep Reversal": 8, "Trend Pullback": 6, "Range Edge": 4}.get(playbook, 0)
        quality_score = int(_clamp(
            direction_score * 0.40
            + max(0, pressure_edge) * 0.30
            + (100 - trap_risk) * 0.15
            + (100 - late_risk) * 0.15
            + playbook_bonus,
            0,
            100,
        ))
    else:
        quality_score = 0

    if decision == "MANUAL_REVIEW" and quality_score < MIN_INTRADAY_GREEN_QUALITY:
        decision = "WAIT_CONFIRMATION"
        archive_reason = None
        comment = (
            f"Кандидат есть, но rank {quality_score} ниже зелёного порога {MIN_INTRADAY_GREEN_QUALITY}. "
            "Ордер заранее не ставить, ждать следующий скан/дополнительное подтверждение."
        )
        quality_score = 0

    report = IntradayReport(
        symbol=symbol,
        price=price,
        regime=regime,
        allowed_direction=allowed,
        decision=decision,
        playbook=playbook,
        buyer_pressure=buyer,
        seller_pressure=seller,
        absorption=absorption,
        trap_risk=trap_risk,
        late_risk=late_risk,
        long_score=long_score,
        short_score=short_score,
        quality_score=quality_score,
        vwap=vwap,
        day_open=day_open,
        day_high=day_high,
        day_low=day_low,
        high_24h=high_24h,
        low_24h=low_24h,
        distance_to_vwap_pct=dist_vwap_pct,
        distance_to_24h_high_pct=dist_high_pct,
        distance_to_24h_low_pct=dist_low_pct,
        comment=comment,
        archive_reason=archive_reason,
        day_high_msk=day_high,
        day_low_msk=day_low,
        rolling_24h_high=high_24h,
        rolling_24h_low=low_24h,
        visible_1m_high=visible_1m_high,
        visible_1m_low=visible_1m_low,
        visible_15m_high=visible_15m_high,
        visible_15m_low=visible_15m_low,
        data_warning=data_warning,
        data_warning_reason=data_warning_reason,
        session_age_min=session_age_min,
        low_liquidity_session=low_liquidity_session,
    )
    return report, df_1m, frames
