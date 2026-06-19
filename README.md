# BTC/ETH Research Collector Bot — v15-3y

No-folders версия для GitHub + Coolify. Все файлы лежат в корне репозитория.

## Цель v15-3y

Собрать новые архивы для продолжения исследования NSM/новых стратегий уже на **3 последних годах BTC/ETH**, а не на одном году.

## Источник данных

- Parquet скачивает **Binance Spot public klines** через `https://api.binance.com/api/v3/klines`.
- **Binance Futures не используется**.
- **MEXC Futures не используется**.
- В коде нет функций открытия/отмены ордеров.
- API ключ не нужен для свечей.

## Кнопки

- **Api** — опционально сохранить MEXC API key/secret в encrypted storage. Для скачивания свечей ключ не нужен.
- **Parquet** — создать `research_input_BTC_ETH_data_*.zip` со свечами BTC/ETH 1m за **1095 дней / 3 года**.
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

Опционально можно явно поставить:

```env
DAYS_BACK=1095
SYMBOLS=BTCUSDT,ETHUSDT
BASE_INTERVAL=1m
TELEGRAM_SEND_LIMIT_MB=48
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

Ориентир по строкам: около **1,576,800 1m-свечей на символ** за 1095 дней.

Charts archive:

```text
research_input_BTC_ETH_charts_*.zip
├── manifest.json
└── charts/
    ├── overview/                  # 1D full 3 years
    ├── monthly_4h/                # 4H по месяцам, последние 36 месяцев
    ├── monthly_1h_recent/         # 1H последние ~180 дней
    └── weekly_15m_recent/         # 15m последние 56 дней, 8 недель
```

## Порядок

1. Deploy в Coolify.
2. `/start`.
3. `Ping` — проверить `version: v15-3y`.
4. `Reset`.
5. `Parquet`.
6. После 100% — `Charts`.
7. Скинуть сюда:
   - `research_input_BTC_ETH_data_*.zip`
   - `research_input_BTC_ETH_charts_*.zip` опционально

## v15-3y changes

- Default `DAYS_BACK=1095` вместо 730: теперь собираются 3 последних года.
- Тексты бота и manifest обновлены под 3 года.
- Charts расширены под 3 года: 36 месяцев 4H, 180 дней 1H, 56 дней 15m.
- Сохранилась безопасность: бот не содержит торговых endpoints.


## v15-3y-mexc-fee-test changes

Добавлены реальные MEXC futures fee-test кнопки:

- `Limit Price`
- `Market Price`
- `/log_mexc`

Тест открывает BTCUSDT + ETHUSDT long, 10% от total USDT equity на каждую сделку, leverage 2x, isolated, и закрывает через 5 минут. Подробности: `README_MEXC_FEE_TEST.md`.
