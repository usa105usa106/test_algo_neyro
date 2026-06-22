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

# --- v33 montage-only helpers. Standard v17 chart functions above are unchanged. ---


def _pillow_montage():
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


def _compute_macd_montage(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return macd, signal, hist


def _plot_candles_montage(
    df: pd.DataFrame,
    title: str,
    output: Path,
    figsize=(10, 6),
    mav=(7, 25, 99),
    current_time_msk: str | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(df) < 2:
        raise ValueError(f"Not enough rows for chart {title}")

    plot_df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    latest_close = float(plot_df["Close"].iloc[-1])
    window_high = float(plot_df["High"].max())
    window_low = float(plot_df["Low"].min())
    latest_ts = plot_df.index[-1]

    macd, signal, hist = _compute_macd_montage(plot_df["Close"])
    addplots = [
        mpf.make_addplot(macd, panel=1, ylabel="MACD"),
        mpf.make_addplot(signal, panel=1),
        mpf.make_addplot(hist, type="bar", panel=1, alpha=0.5),
    ]

    style = mpf.make_mpf_style(base_mpf_style="charles", y_on_right=True)
    msk_label = current_time_msk or latest_ts.tz_convert("Europe/Moscow").strftime("%Y-%m-%d %H:%M MSK")
    title_with_info = (
        f"{title}\n"
        f"price: {_format_price(latest_close)} | high: {_format_price(window_high)} | low: {_format_price(window_low)} | {msk_label}"
    )

    kwargs = {
        "type": "candle",
        "volume": False,
        "style": style,
        "title": title_with_info,
        "figsize": figsize,
        "tight_layout": True,
        "savefig": {"fname": str(output), "dpi": 150, "bbox_inches": "tight"},
        "warn_too_much_data": 5000,
        "hlines": {
            "hlines": [latest_close, window_high, window_low],
            "linestyle": ["--", ":", ":"],
            "linewidths": [0.8, 0.6, 0.6],
        },
        "addplot": addplots,
        "panel_ratios": (4, 1),
    }
    if mav is not None:
        mav_values = (mav,) if isinstance(mav, int) else tuple(mav)
        if mav_values and len(df) > max(mav_values):
            kwargs["mav"] = mav_values

    mpf.plot(plot_df, **kwargs)


def _timeframe_dataframes_montage(df_1m: pd.DataFrame) -> dict[str, pd.DataFrame]:
    latest_ts = df_1m.index.max()
    df_1d = resample_ohlcv(df_1m, "1d")
    df_4h = resample_ohlcv(df_1m, "4h")
    df_1h = resample_ohlcv(df_1m, "1h")
    df_15m = resample_ohlcv(df_1m, "15min")
    recent_15m = df_15m[df_15m.index >= latest_ts - pd.Timedelta(days=7)]
    recent_1m = df_1m[df_1m.index >= latest_ts - pd.Timedelta(hours=24)]
    return {"1D": df_1d, "4H": df_4h, "1H": df_1h, "15m": recent_15m, "1m": recent_1m}


def _default_font_montage(size: int):
    _, _, ImageFont = _pillow_montage()
    for name in ["DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"]:
        try:
            return ImageFont.truetype(name, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit_image_montage(img, target_w: int, target_h: int, bg=(255, 255, 255)):
    Image, _, _ = _pillow_montage()
    src = img.convert("RGB")
    src.thumbnail((target_w, target_h))
    canvas = Image.new("RGB", (target_w, target_h), color=bg)
    x = (target_w - src.width) // 2
    y = (target_h - src.height) // 2
    canvas.paste(src, (x, y))
    return canvas


def create_montage_for_symbol(
    symbol: str,
    df_1m: pd.DataFrame,
    out_root: Path,
    current_time_msk: str,
    task_hint: str,
    logger: logging.Logger,
) -> str:
    Image, ImageDraw, _ = _pillow_montage()
    out_dir = out_root / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    frames = _timeframe_dataframes_montage(df_1m)
    available_days = len(df_1m) / 1440.0
    window_label = f"available ~{available_days:.1f}d"
    titles = {
        "1D": f"{symbol} 1D — {window_label}",
        "4H": f"{symbol} 4H — {window_label}",
        "1H": f"{symbol} 1H — {window_label}",
        "15m": f"{symbol} 15m — last 7 days",
        "1m": f"{symbol} 1m — last 24 hours",
    }

    img_paths: dict[str, Path] = {}
    for tf, frame in frames.items():
        out = tmp_dir / f"{symbol}_{tf}.png"
        _plot_candles_montage(frame, titles[tf], out, current_time_msk=current_time_msk)
        img_paths[tf] = out

    canvas_w, canvas_h = 2560, 2160
    margin = 40
    gap = 30
    cell_w = (canvas_w - margin * 2 - gap) // 2
    cell_h = (canvas_h - margin * 2 - gap * 2) // 3
    canvas = Image.new("RGB", (canvas_w, canvas_h), color=(250, 250, 250))
    positions = {
        "1D": (margin, margin),
        "4H": (margin + cell_w + gap, margin),
        "1H": (margin, margin + cell_h + gap),
        "15m": (margin + cell_w + gap, margin + cell_h + gap),
        "1m": (margin, margin + (cell_h + gap) * 2),
        "info": (margin + cell_w + gap, margin + (cell_h + gap) * 2),
    }

    for tf in ["1D", "4H", "1H", "15m", "1m"]:
        fitted = _fit_image_montage(Image.open(img_paths[tf]), cell_w, cell_h)
        canvas.paste(fitted, positions[tf])

    info = Image.new("RGB", (cell_w, cell_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(info)
    title_font = _default_font_montage(38)
    body_font = _default_font_montage(28)
    small_font = _default_font_montage(24)
    price = float(df_1m["Close"].iloc[-1])
    high24 = float(frames["1m"]["High"].max()) if not frames["1m"].empty else price
    low24 = float(frames["1m"]["Low"].min()) if not frames["1m"].empty else price
    lines = [
        symbol,
        f"Current price: {_format_price(price)}",
        f"24h high / low: {_format_price(high24)} / {_format_price(low24)}",
        f"MSK+3: {current_time_msk}",
        "Montage mode: ON",
        "Charts: 1D / 4H / 1H / 15m / 1m",
        "Indicators: MA7 / MA25 / MA99 / MACD",
        "Task:",
        task_hint,
    ]
    legend_text = "MA7 orange | MA25 blue | MA99 purple | dashed = current price"
    y = 30
    for i, line in enumerate(lines):
        font = title_font if i == 0 else (small_font if line.startswith(("Task:", "Charts:", "Indicators:")) else body_font)
        draw.text((28, y), line, fill=(25, 25, 25), font=font)
        y += 52 if i == 0 else 40
    draw.text((28, cell_h - 44), legend_text, fill=(25, 25, 25), font=small_font)
    canvas.paste(info, positions["info"])

    out_path = out_dir / f"{symbol}_montage.jpg"
    canvas.save(out_path, quality=90)
    for p in img_paths.values():
        try:
            p.unlink()
        except Exception:
            pass
    try:
        tmp_dir.rmdir()
    except Exception:
        pass
    rel = str(out_path.relative_to(out_root.parent))
    logger.info("Created montage chart for %s: %s", symbol, rel)
    return rel
