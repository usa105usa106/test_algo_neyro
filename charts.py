from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf
import pandas as pd


@dataclass(frozen=True)
class ChartJobResult:
    chart_files: list[str]
    warnings: list[str]


def load_ohlcv(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "datetime_utc" in df.columns:
        dt = pd.to_datetime(df["datetime_utc"], utc=True)
    else:
        dt = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.copy()
    df.index = dt
    df = df[["open", "high", "low", "close", "volume", "quote_volume"]].sort_index()
    df.columns = ["Open", "High", "Low", "Close", "Volume", "QuoteVolume"]
    return df


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
        "QuoteVolume": "sum",
    }
    out = df.resample(rule).agg(agg).dropna(subset=["Open", "High", "Low", "Close"])
    return out


def _plot_candles(df: pd.DataFrame, title: str, output: Path, figsize=(16, 8), mav=(20, 50)) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(df) < 2:
        raise ValueError(f"Not enough rows for chart {title}")

    plot_df = df[["Open", "High", "Low", "Close", "Volume"]]
    kwargs = {
        "type": "candle",
        "volume": True,
        "style": "charles",
        "title": title,
        "figsize": figsize,
        "tight_layout": True,
        "savefig": {"fname": str(output), "dpi": 150, "bbox_inches": "tight"},
        "warn_too_much_data": 5000,
    }
    # mplfinance rejects mav=None. Add mav only when every MA period fits into the chart.
    if mav is not None:
        mav_values = (mav,) if isinstance(mav, int) else tuple(mav)
        if mav_values and len(df) > max(mav_values):
            kwargs["mav"] = mav

    mpf.plot(plot_df, **kwargs)


def make_charts_for_symbol(symbol: str, candle_path: Path, out_root: Path, logger: logging.Logger) -> ChartJobResult:
    chart_files: list[str] = []
    warnings: list[str] = []
    df_1m = load_ohlcv(candle_path)
    if df_1m.empty:
        raise RuntimeError(f"No candle data in {candle_path}")

    latest_ts = df_1m.index.max()

    df_1d = resample_ohlcv(df_1m, "1d")
    p = out_root / "overview" / f"{symbol}_1D_full_2y.png"
    _plot_candles(df_1d, f"{symbol} 1D full 2 years", p, figsize=(18, 9), mav=(20, 50, 200))
    chart_files.append(str(p.relative_to(out_root.parent)))

    df_4h = resample_ohlcv(df_1m, "4h")
    months = sorted(df_4h.index.to_period("M").unique())[-24:]
    for month in months:
        month_df = df_4h[df_4h.index.to_period("M") == month]
        if len(month_df) < 5:
            continue
        p = out_root / "monthly_4h" / f"{symbol}_4H_{month}.png"
        _plot_candles(month_df, f"{symbol} 4H {month}", p, figsize=(16, 8), mav=(20, 50))
        chart_files.append(str(p.relative_to(out_root.parent)))

    df_1h = resample_ohlcv(df_1m, "1h")
    recent_1h = df_1h[df_1h.index >= latest_ts - pd.Timedelta(days=180)]
    recent_months = sorted(recent_1h.index.to_period("M").unique())
    for month in recent_months:
        month_df = recent_1h[recent_1h.index.to_period("M") == month]
        if len(month_df) < 24:
            continue
        p = out_root / "monthly_1h_recent" / f"{symbol}_1H_{month}.png"
        _plot_candles(month_df, f"{symbol} 1H recent {month}", p, figsize=(18, 9), mav=(20, 50))
        chart_files.append(str(p.relative_to(out_root.parent)))

    df_15m = resample_ohlcv(df_1m, "15min")
    start_recent = latest_ts - pd.Timedelta(days=56)
    recent_15m = df_15m[df_15m.index >= start_recent]
    for i in range(8):
        start = start_recent + pd.Timedelta(days=7 * i)
        end = start + pd.Timedelta(days=7)
        chunk = recent_15m[(recent_15m.index >= start) & (recent_15m.index < end)]
        if len(chunk) < 24:
            warnings.append(f"{symbol} 15m week {i+1}: too few rows")
            continue
        p = out_root / "weekly_15m_recent" / f"{symbol}_15m_week_{i+1}.png"
        title = f"{symbol} 15m week {i+1}: {start.date()} to {end.date()}"
        _plot_candles(chunk, title, p, figsize=(18, 9), mav=(20, 50))
        chart_files.append(str(p.relative_to(out_root.parent)))

    logger.info("Created %s chart files for %s", len(chart_files), symbol)
    return ChartJobResult(chart_files=chart_files, warnings=warnings)
