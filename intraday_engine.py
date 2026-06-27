from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd


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
    price = float(df_1m["Close"].iloc[-1])
    latest_ts = df_1m.index[-1]
    latest_msk = latest_ts.tz_convert("Europe/Moscow")
    day_start_msk = latest_msk.normalize()
    day_start_utc = day_start_msk.tz_convert("UTC")
    day_df = df_1m[df_1m.index >= day_start_utc]
    if day_df.empty:
        day_df = df_1m.tail(24 * 60)
    day_open = float(day_df["Open"].iloc[0])
    day_high = float(day_df["High"].max())
    day_low = float(day_df["Low"].min())
    high_24h = float(frames["1m"]["High"].max())
    low_24h = float(frames["1m"]["Low"].min())
    vwap = _vwap(day_df)

    buyer, seller, absorption = _pressure_scores(df_1m, df_15m, vwap)
    atr15 = _atr(df_15m, 14)
    atr_pct = atr15 / price * 100 if price else 0.0
    dist_vwap_pct = _safe_pct(price, vwap) if np.isfinite(vwap) else 0.0
    dist_high_pct = abs(_safe_pct(high_24h, price))
    dist_low_pct = abs(_safe_pct(price, low_24h))

    near_24h_high = dist_high_pct <= max(0.10, atr_pct * 0.8)
    near_24h_low = dist_low_pct <= max(0.10, atr_pct * 0.8)
    extended_up = dist_vwap_pct > max(0.25, atr_pct * 1.2)
    extended_down = dist_vwap_pct < -max(0.25, atr_pct * 1.2)

    recent_15 = df_15m.tail(4)
    impulse_up = False
    impulse_down = False
    if len(recent_15) >= 2 and atr15 > 0:
        last_body = float(abs(recent_15["Close"].iloc[-1] - recent_15["Open"].iloc[-1]))
        impulse_up = recent_15["Close"].iloc[-1] > recent_15["Open"].iloc[-1] and last_body > atr15 * 0.9
        impulse_down = recent_15["Close"].iloc[-1] < recent_15["Open"].iloc[-1] and last_body > atr15 * 0.9

    late_long = (35 if near_24h_high else 0) + (25 if extended_up else 0) + (20 if impulse_up else 0)
    late_short = (35 if near_24h_low else 0) + (25 if extended_down else 0) + (20 if impulse_down else 0)
    late_risk = int(_clamp(max(late_long, late_short) + max(0, absorption - 65) * 0.4))

    # Sweep detection: pierce day/24h extremum and return back inside.
    last_15 = df_15m.tail(2)
    sweep_up = False
    sweep_down = False
    if len(last_15) >= 1:
        candle = last_15.iloc[-1]
        prev_24h_high = float(frames["1m"].iloc[:-1]["High"].max()) if len(frames["1m"]) > 2 else high_24h
        prev_24h_low = float(frames["1m"].iloc[:-1]["Low"].min()) if len(frames["1m"]) > 2 else low_24h
        sweep_up = bool(candle["High"] >= prev_24h_high and candle["Close"] < prev_24h_high)
        sweep_down = bool(candle["Low"] <= prev_24h_low and candle["Close"] > prev_24h_low)

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
    if sweep_up or sweep_down:
        regime = "SWEEP"
    elif long_score >= 65 and long_score >= short_score + 15:
        regime = "TREND_LONG"
    elif short_score >= 65 and short_score >= long_score + 15:
        regime = "TREND_SHORT"
    elif trap_risk >= 75 or abs(long_score - short_score) <= 10:
        regime = "CHOP" if range_width_pct < max(0.3, atr_pct * 5) or trap_risk >= 75 else "RANGE"
    else:
        regime = "RANGE"

    allowed = "WAIT"
    playbook = "none"
    decision = "WAIT"
    comment = "Направление не подтверждено. Ждать новый режим."
    archive_reason = None

    near_vwap = abs(dist_vwap_pct) <= max(0.08, atr_pct * 0.5)
    pullback_ok_long = regime == "TREND_LONG" and near_vwap and buyer >= seller + 10 and trap_risk < 65 and late_risk < 55
    pullback_ok_short = regime == "TREND_SHORT" and near_vwap and seller >= buyer + 10 and trap_risk < 65 and late_risk < 55

    if regime == "TREND_LONG":
        allowed = "LONG_ONLY"
        playbook = "Trend Pullback"
        if pullback_ok_long:
            decision = "MANUAL_REVIEW"
            comment = f"Лонг по тренду после отката к VWAP/зоне, buyers сильнее, late риск не высокий. Цена {_fmt(price)}."
            archive_reason = "Trend Pullback LONG созрел для ручной проверки"
        else:
            decision = "WAIT_PULLBACK"
            comment = "Лонг-направление есть, но вход сейчас не созрел/может быть поздним. Ждать откат и подтверждение."
    elif regime == "TREND_SHORT":
        allowed = "SHORT_ONLY"
        playbook = "Trend Pullback"
        if pullback_ok_short:
            decision = "MANUAL_REVIEW"
            comment = f"Шорт по тренду после отката к VWAP/зоне, sellers сильнее, late риск не высокий. Цена {_fmt(price)}."
            archive_reason = "Trend Pullback SHORT созрел для ручной проверки"
        else:
            decision = "WAIT_PULLBACK"
            comment = "Шорт-направление есть, но вход сейчас не созрел/может быть поздним. Ждать откат и подтверждение."
    elif regime == "SWEEP":
        playbook = "Sweep Reversal"
        if sweep_down:
            allowed = "LONG_ONLY_AFTER_CONFIRMATION"
            if buyer >= seller and trap_risk < 75:
                decision = "MANUAL_REVIEW"
                comment = "Сняли low и вернули цену обратно. Проверить лонг после подтверждения возврата."
                archive_reason = "Sweep down reversal для ручной проверки"
            else:
                decision = "WAIT_SWEEP_CONFIRMATION"
                comment = "Похоже на sweep вниз, но выкуп ещё слабый. Ждать закрытие/закрепление."
        elif sweep_up:
            allowed = "SHORT_ONLY_AFTER_CONFIRMATION"
            if seller >= buyer and trap_risk < 75:
                decision = "MANUAL_REVIEW"
                comment = "Сняли high и вернули цену обратно. Проверить шорт после подтверждения возврата."
                archive_reason = "Sweep up reversal для ручной проверки"
            else:
                decision = "WAIT_SWEEP_CONFIRMATION"
                comment = "Похоже на sweep вверх, но продавец ещё слабый. Ждать закрытие/закрепление."
    elif regime == "RANGE":
        allowed = "BOTH_FROM_EDGES"
        playbook = "Range Edge"
        edge_low = dist_low_pct <= max(0.12, atr_pct * 0.8)
        edge_high = dist_high_pct <= max(0.12, atr_pct * 0.8)
        if edge_low and buyer >= seller + 10 and trap_risk < 65:
            decision = "MANUAL_REVIEW"
            comment = "Цена у нижнего края диапазона, есть выкуп. Проверить range-long вручную."
            archive_reason = "Range Edge LONG созрел для ручной проверки"
        elif edge_high and seller >= buyer + 10 and trap_risk < 65:
            decision = "MANUAL_REVIEW"
            comment = "Цена у верхнего края диапазона, есть продавец. Проверить range-short вручную."
            archive_reason = "Range Edge SHORT созрел для ручной проверки"
        else:
            decision = "WAIT_EDGE"
            comment = "Диапазон. В середине не торговать, ждать верх/низ диапазона."
    else:
        allowed = "WAIT"
        playbook = "none"
        decision = "NO_TRADE"
        comment = "Грязная пила/ловушка. Нет нормального преимущества."

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
    )
    return report, df_1m, frames
