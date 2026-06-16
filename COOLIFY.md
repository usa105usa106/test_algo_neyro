# Coolify deploy — MEXC Research Collector v10

## 1. GitHub

Загрузи все файлы из архива прямо в корень репозитория. Папки создавать не нужно.

## 2. Coolify

Создай новый ресурс из GitHub repo.

Рекомендуемый режим: **Docker Compose**.

## 3. Environment Variables

Добавь только:

```env
TELEGRAM_BOT_TOKEN=токен_бота
ADMIN_TELEGRAM_ID=твой_telegram_id
```

Не нужно добавлять `MEXC_MARKET_TYPE` и `MIN_COVERAGE_RATIO`: они уже прописаны в коде.

## 4. Persistent storage

В `docker-compose.yml` уже есть volume:

```text
mexc_research_storage:/app/storage
```

Там хранятся candles, exports, logs, state, secrets.

## 5. После deploy

В Telegram:

1. `/start`
2. **Ping** — проверить `version: v10`.
3. **Reset** — после обновления версии.
4. **Parquet**.
5. **Charts**.

## 6. Ping

Кнопка **Ping** показывает:

- version
- response ms
- uptime
- started UTC
- current task
- process RAM
- system RAM
- process CPU
- disk usage

## 7. Если MEXC ограничивает запросы

В v9/v10 добавлены throttle и retry для futures `code=510` / `Requests are too frequent`. Поэтому сбор Parquet может идти дольше, но не должен падать сразу от rate limit.
