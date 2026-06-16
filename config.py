from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


def _split_csv(value: str) -> list[str]:
    return [x.strip().upper() for x in value.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_telegram_id: int | None
    data_root: Path
    telegram_send_limit_mb: int
    symbols: list[str]
    days_back: int
    base_interval: str
    mexc_base_url: str
    mexc_market_type: str
    min_coverage_ratio: float
    secret_encryption_key: str | None

    @property
    def candles_dir(self) -> Path:
        return self.data_root / "candles"

    @property
    def charts_dir(self) -> Path:
        return self.data_root / "charts"

    @property
    def meta_dir(self) -> Path:
        return self.data_root / "meta"

    @property
    def exports_dir(self) -> Path:
        return self.data_root / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def state_dir(self) -> Path:
        return self.data_root / "state"

    @property
    def secrets_dir(self) -> Path:
        return self.data_root / "secrets"

    @property
    def work_dir(self) -> Path:
        return self.data_root / "work"

    def ensure_dirs(self) -> None:
        for path in [
            self.candles_dir,
            self.charts_dir,
            self.meta_dir,
            self.exports_dir,
            self.logs_dir,
            self.state_dir,
            self.secrets_dir,
            self.work_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    admin_raw = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
    admin_id = int(admin_raw) if admin_raw.isdigit() else None
    data_root = Path(os.getenv("DATA_ROOT", "./storage")).expanduser().resolve()
    # Hardcoded defaults for this collector.
    # Do not require Coolify env variables for these two settings.
    # This bot is a DATA COLLECTOR only; it uses MEXC futures market-data endpoints
    # because spot 1m klines may return only a short recent slice in this setup.
    market_type = "futures"
    mexc_base_url = "https://api.mexc.com"
    min_coverage_ratio = 0.80

    settings = Settings(
        telegram_bot_token=token,
        admin_telegram_id=admin_id,
        data_root=data_root,
        telegram_send_limit_mb=int(os.getenv("TELEGRAM_SEND_LIMIT_MB", "48")),
        symbols=_split_csv(os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT")),
        days_back=int(os.getenv("DAYS_BACK", "365")),
        base_interval=os.getenv("BASE_INTERVAL", "1m").strip(),
        mexc_base_url=mexc_base_url.rstrip("/"),
        mexc_market_type=market_type,
        min_coverage_ratio=min_coverage_ratio,
        secret_encryption_key=os.getenv("SECRET_ENCRYPTION_KEY", "").strip() or None,
    )
    settings.ensure_dirs()
    return settings
