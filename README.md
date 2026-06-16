# BTC/ETH Research Collector Bot — v12

No-folders версия для GitHub + Coolify. Все файлы лежат в корне репозитория.

## Источник данных

- Parquet скачивает **Binance Spot public klines** через `https://api.binance.com/api/v3/klines`.
- **Binance Futures не используется**.
- **MEXC Futures не используется**.
- В коде нет функций открытия/отмены ордеров.

## Кнопки

- **Api** — опционально сохранить MEXC API key/secret в encrypted storage. Для скачивания свечей ключ не нужен.
- **Parquet** — создать `research_input_BTC_ETH_data_*.zip` со свечами BTC/ETH 1m за 365 дней.
- **Charts** — создать `research_input_BTC_ETH_charts_*.zip` с графиками из локальных Parquet.
- **Log_full** — забрать полный лог и индекс файлов.
- **Status** — состояние задач и последние архивы.
- **Ping** — версия, отклик, uptime, RAM/CPU/disk.
- **Reset** — остановить фоновые задачи и очистить runtime/temp/API state.

## Coolify env

Нужны только:

```env
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
```

`MEXC_MARKET_TYPE`, `MIN_COVERAGE_RATIO`, `MEXC_BASE_URL` добавлять не надо. Источник данных уже зашит в коде: Binance Spot.

## Что должно получиться

Parquet archive:

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

Нормальный размер — ориентировочно 60–250 MB. Нормальное количество строк — около 525,600 свечей на символ.

## Порядок

1. Deploy в Coolify.
2. `/start`.
3. `Ping` — проверить `version: v13`.
4. `Reset`.
5. `Parquet`.
6. После 100% — `Charts`.


## v13 fix

- Исправлены pandas frequency aliases для Charts: используются `1d`, `4h`, `1h`, `15min`, чтобы не падать на ошибке `Invalid frequency: 4H`.
- Если Parquet уже создан успешно, заново Parquet нажимать не нужно — можно сразу нажать Charts после redeploy.
