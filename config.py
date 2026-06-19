from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_VERSION = "v17_full_strict_lines"


@dataclass(frozen=True)
class ScanPreset:
    key: str
    title: str
    symbols: list[str]


SCAN_PRESETS: dict[str, ScanPreset] = {
    "gold": ScanPreset("gold", "Gold 30d", ["XAU_USDT"]),
    "btc": ScanPreset("btc", "BTC 30d", ["BTC_USDT"]),
    "eth": ScanPreset("eth", "ETH 30d", ["ETH_USDT"]),
    "silver": ScanPreset("silver", "Silver 30d", ["SILVER_USDT"]),
    "oil": ScanPreset("oil", "Oil 30d", ["USOIL_USDT"]),
    "multi": ScanPreset("multi", "Multi 5 assets 30d", ["XAU_USDT", "BTC_USDT", "ETH_USDT", "SILVER_USDT", "USOIL_USDT"]),
}

# Exact symbols used by scan buttons and Symbols check.
# No automatic fallback/substitution is used, because XAU vs XAUT and WTI vs Brent have different prices.
SYMBOL_CANDIDATES: dict[str, list[str]] = {
    "gold": ["XAU_USDT"],      # GOLD(XAU)USDT
    "btc": ["BTC_USDT"],
    "eth": ["ETH_USDT"],
    "silver": ["SILVER_USDT"], # SILVER(XAG)USDT
    "oil": ["USOIL_USDT"],     # OIL(WTI)USDT
}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_telegram_id: int | None
    data_root: Path
    telegram_send_limit_mb: int
    days_back: int
    base_interval: str
    mexc_base_url: str
    mexc_market_type: str
    min_coverage_ratio: float
    min_effective_days: float
    secret_encryption_key: str | None
    app_version: str

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

    settings = Settings(
        telegram_bot_token=token,
        admin_telegram_id=admin_id,
        data_root=data_root,
        telegram_send_limit_mb=int(os.getenv("TELEGRAM_SEND_LIMIT_MB", "48")),
        days_back=int(os.getenv("DAYS_BACK", "30")),
        base_interval=os.getenv("BASE_INTERVAL", "1m").strip(),
        mexc_base_url=os.getenv("MEXC_BASE_URL", "https://api.mexc.com").strip().rstrip("/"),
        mexc_market_type="futures",
        min_coverage_ratio=float(os.getenv("MIN_COVERAGE_RATIO", "0.80")),
        min_effective_days=float(os.getenv("MIN_EFFECTIVE_DAYS", "20")),
        secret_encryption_key=os.getenv("SECRET_ENCRYPTION_KEY", "").strip() or None,
        app_version=APP_VERSION,
    )
    settings.ensure_dirs()
    return settings
