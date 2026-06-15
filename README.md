# MEXC Research Collector Bot — no-folders version

Telegram-бот для подготовки архивов под staged research:

- `Parquet` — создаёт архив `research_input_BTC_ETH_data_*.zip` со свечами BTC/ETH 1m за 365 дней.
- `Charts` — создаёт архив `research_input_BTC_ETH_charts_*.zip` с обзорными графиками.
- `Api` — сохранить MEXC API через Telegram, read-only; торговых функций в коде нет.
- `Log_full` — собрать полный лог работы.
- `Reset` — остановить фоновые задачи и очистить runtime-состояние.

Эта версия специально сделана **без папок**: все файлы лежат в корне GitHub-репозитория.

## Файлы, которые нужно загрузить в GitHub

Загрузи все файлы из архива прямо в корень репозитория:

```text
archive_builder.py
bot.py
charts.py
config.py
file_utils.py
logging_setup.py
mexc.py
security.py
run.py
Dockerfile
docker-compose.yml
docker-entrypoint.sh
requirements.txt
COOLIFY.md
README.md
.env.coolify.example
.dockerignore
.gitignore
mexc-research-collector.service
```

Для Coolify важны: `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`, `requirements.txt`, `run.py` и все `.py` файлы.

## Coolify

В Coolify добавь Environment Variables:

```text
TELEGRAM_BOT_TOKEN=токен_твоего_бота
ADMIN_TELEGRAM_ID=твой_telegram_id
```

Опционально:

```text
SYMBOLS=BTCUSDT,ETHUSDT
DAYS_BACK=365
BASE_INTERVAL=1m
TELEGRAM_SEND_LIMIT_MB=48
MEXC_BASE_URL=https://api.mexc.com
TZ=UTC
```

После Deploy открой Telegram и напиши боту `/start`.

## Что создаёт Parquet

```text
research_input_BTC_ETH_data_*.zip
├── manifest.json
├── candles/
│   ├── BTCUSDT_1m.parquet
│   └── ETHUSDT_1m.parquet
└── meta/
    ├── exchange_info.json
    ├── fees.json
    └── api_status.json
```

## Что создаёт Charts

```text
research_input_BTC_ETH_charts_*.zip
└── charts/
    ├── overview/
    ├── monthly_4h/
    ├── monthly_1h_recent/
    └── weekly_15m_recent/
```

Примерно 40–42 графика.

## Прогресс

При запуске `Parquet` и `Charts` бот отправляет прогресс:

```text
Start...
10%...
20%...
...
100% archive ready
```

Если прогресс долго не меняется — нажми `Log_full`. Если процесс завис — `Reset`.

## Безопасность

В коде нет функций live-trading: нет `place_order`, `cancel_order` и торговых endpoint'ов. Свечи берутся из публичного market-data MEXC.
