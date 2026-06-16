# MEXC BTC/ETH Research Collector Bot — v10

Telegram-бот для подготовки архивов данных под staged research в ChatGPT.

## Кнопки

- **Api** — сохранить MEXC API key/secret в encrypted storage. Для market data ключ не обязателен.
- **Parquet** — скачать BTC/ETH 1m за 365 дней с MEXC futures и создать `research_input_BTC_ETH_data_*.zip`.
- **Charts** — построить читаемые графики из локальных Parquet и создать `research_input_BTC_ETH_charts_*.zip`.
- **Log_full** — собрать полный лог, индекс архивов и state.
- **Status** — состояние задач, market type, последние архивы.
- **Ping** — время отклика, uptime, RAM/CPU/disk, версия `v10`.
- **Reset** — остановить текущую фоновую задачу, очистить runtime/API state и временную рабочую папку.

## Важно

В коде нет торговых endpoints: нет `place_order`, `cancel_order`, withdraw/transfer. Бот не умеет открывать реальные сделки.

## Coolify

В Coolify нужны только переменные:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
ADMIN_TELEGRAM_ID=123456789
```

Остальное уже прописано в коде:

- market type: `futures`
- MEXC base URL: `https://api.mexc.com`
- min coverage: `0.80`
- version: `v10`

## Файлы в GitHub

Версия no-folders: все файлы кладутся прямо в корень репозитория.

Главные файлы:

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
README.md
COOLIFY.md
```

## Порядок работы

1. Redeploy в Coolify.
2. В Telegram: `/start`.
3. Нажать **Ping**, проверить что версия `v10`.
4. Нажать **Reset** после обновления версии.
5. Нажать **Parquet**.
6. После успешного архива нажать **Charts**.
7. Если ошибка — нажать **Log_full** и скачать лог.
