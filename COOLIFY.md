# Запуск в Coolify — no-folders version

Эта версия сделана для случая, когда в GitHub неудобно создавать папки. Все файлы лежат в корне репозитория.

## 1. Загрузка в GitHub

Загрузи все файлы из архива в корень репозитория. Не нужно создавать `src` или `systemd`.

Обязательные файлы:

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
```

Остальные файлы желательно тоже загрузить:

```text
README.md
COOLIFY.md
.env.coolify.example
.dockerignore
.gitignore
mexc-research-collector.service
```

## 2. Coolify resource

В Coolify:

1. New Resource → GitHub repository.
2. Выбери репозиторий.
3. Build Pack: Docker Compose, если Coolify видит `docker-compose.yml`.
4. Если Docker Compose не выбирается, используй Dockerfile.

## 3. Environment Variables

Минимум:

```text
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
```

Рекомендуемые значения:

```text
DATA_ROOT=/app/storage
TELEGRAM_SEND_LIMIT_MB=48
SYMBOLS=BTCUSDT,ETHUSDT
DAYS_BACK=365
BASE_INTERVAL=1m
MEXC_BASE_URL=https://api.mexc.com
TZ=UTC
```

## 4. Persistent storage

В `docker-compose.yml` уже есть volume:

```text
mexc_research_storage:/app/storage
```

Это нужно, чтобы после redeploy не терялись свечи, архивы, логи и encrypted API state.

## 5. Проверка

После Deploy:

1. Открой Telegram.
2. Напиши боту `/start`.
3. Нажми `Parquet`.
4. Дождись архива `research_input_BTC_ETH_data_*.zip`.
5. После этого нажми `Charts`.

Если что-то упало — нажми `Log_full`.
