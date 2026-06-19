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


def _format_price(value: float) -> str:
    """Compact price formatting for chart titles/labels."""
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 100:
        return f"{value:.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}"


def _plot_candles(df: pd.DataFrame, title: str, output: Path, figsize=(16, 8), mav=(20, 50)) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(df) < 2:
        raise ValueError(f"Not enough rows for chart {title}")

    plot_df = df[["Open", "High", "Low", "Close", "Volume"]]
    latest_close = float(plot_df["Close"].iloc[-1])
    latest_ts = plot_df.index[-1]
    title_with_price = f"{title} | last close: {_format_price(latest_close)} @ {latest_ts.strftime('%Y-%m-%d %H:%M UTC')}"

    kwargs = {
        "type": "candle",
        "volume": True,
        "style": "charles",
        "title": title_with_price,
        "figsize": figsize,
        "tight_layout": True,
        "savefig": {"fname": str(output), "dpi": 150, "bbox_inches": "tight"},
        "warn_too_much_data": 5000,
        # Draw a current-price reference line. The exact price is also written in the title.
        "hlines": {"hlines": [latest_close], "linestyle": "--", "linewidths": 0.8},
    }
    # mplfinance rejects mav=None. Add mav only when every MA period fits into the chart.
    if mav is not None:
        mav_values = (mav,) if isinstance(mav, int) else tuple(mav)
        if mav_values and len(df) > max(mav_values):
            kwargs["mav"] = mav

    mpf.plot(plot_df, **kwargs)


def make_charts_for_symbol(symbol: str, candle_path: Path, out_root: Path, logger: logging.Logger) -> ChartJobResult:
    """Build exactly the 5 ChatGPT scan charts used by v17_full.

    Kept as a public helper for compatibility, but the old multi-month chart logic was removed
    so every code path matches the new 30d scan format:
    1D, 4H, 1H, 15m, 1m.
    """
    chart_files: list[str] = []
    warnings: list[str] = []
    df_1m = load_ohlcv(candle_path)
    if df_1m.empty:
        raise RuntimeError(f"No candle data in {candle_path}")

    latest_ts = df_1m.index.max()
    available_days = len(df_1m) / 1440.0
    window_label = f"requested 30d / available ~{available_days:.1f}d"

    def plot(df: pd.DataFrame, title: str, output: Path, figsize=(16, 8), mav=(20, 50)) -> None:
        if len(df) < 2:
            warnings.append(f"{title}: too few rows")
            return
        _plot_candles(df, title, output, figsize, mav)
        chart_files.append(str(output.relative_to(out_root.parent)))

    df_1d = resample_ohlcv(df_1m, "1d")
    plot(df_1d, f"{symbol} 1D — {window_label}", out_root / symbol / f"{symbol}_1D.png", figsize=(18, 9), mav=(7, 20))

    df_4h = resample_ohlcv(df_1m, "4h")
    plot(df_4h, f"{symbol} 4H — {window_label}", out_root / symbol / f"{symbol}_4H.png", figsize=(18, 9), mav=(20, 50))

    df_1h = resample_ohlcv(df_1m, "1h")
    plot(df_1h, f"{symbol} 1H — {window_label}", out_root / symbol / f"{symbol}_1H.png", figsize=(18, 9), mav=(20, 50, 200))

    df_15m = resample_ohlcv(df_1m, "15min")
    recent_15m = df_15m[df_15m.index >= latest_ts - pd.Timedelta(days=7)]
    plot(recent_15m, f"{symbol} 15m — last 7 days", out_root / symbol / f"{symbol}_15m.png", figsize=(18, 9), mav=(20, 50, 200))

    recent_1m = df_1m[df_1m.index >= latest_ts - pd.Timedelta(hours=24)]
    plot(recent_1m, f"{symbol} 1m — last 24 hours", out_root / symbol / f"{symbol}_1m.png", figsize=(18, 9), mav=(20, 50, 200))

    logger.info("Created %s chart files for %s", len(chart_files), symbol)
    return ChartJobResult(chart_files=chart_files, warnings=warnings)
