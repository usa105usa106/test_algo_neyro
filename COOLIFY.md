# Coolify deploy — BTC/ETH Research Collector v12

## Важно

v12 использует **только Binance Spot public klines** для годовых свечей BTC/ETH.

Не используются:

- Binance Futures
- MEXC Futures
- любые торговые endpoints

## 1. GitHub

Загрузи все файлы из архива в корень репозитория. Папки создавать не нужно.

## 2. Coolify

Создай новый ресурс из GitHub repo и выбери Docker Compose или Dockerfile.

## 3. Environment Variables

Добавь только:

```env
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
```

Не добавляй `MEXC_MARKET_TYPE`, `MEXC_BASE_URL`, `MIN_COVERAGE_RATIO`: они не нужны.

## 4. Проверка

После deploy:

1. Напиши боту `/start`.
2. Нажми `Ping`. Должно быть `version: v12`.
3. Нажми `Reset`.
4. Нажми `Parquet`.

Нормальный прогресс должен идти с ростом строк, например:

```text
BTCUSDT: 10,000/525,600
BTCUSDT: 100,000/525,600
...
ETHUSDT: 100,000/525,600
```

## 5. Persistent storage

В `docker-compose.yml` есть volume:

```text
mexc_research_storage:/app/storage
```

В storage лежат свечи, графики, архивы, логи и encrypted state.
