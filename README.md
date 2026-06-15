# MEXC BTC/ETH Research Collector Telegram Bot

Бот делает только сбор данных для дальнейшей передачи в ChatGPT staged research pipeline.
Он **не умеет торговать**: в коде нет `place_order`, `cancel_order`, `order`, изменения плеча или других live-trading функций.

## Кнопки

Минимальный набор кнопок:

- **Api** — через Telegram сохраняет MEXC API KEY/SECRET в зашифрованном виде. Для скачивания свечей ключ не обязателен: свечи и exchangeInfo берутся из public market data endpoints. Ключ сохраняется только для будущей meta/status совместимости.
- **Parquet** — создаёт `research_input_BTC_ETH_data_*.zip`:
  - `manifest.json`
  - `candles/BTCUSDT_1m.parquet`
  - `candles/ETHUSDT_1m.parquet`
  - `meta/exchange_info.json`
  - `meta/fees.json`
  - `meta/api_status.json`
- **Charts** — создаёт `research_input_BTC_ETH_charts_*.zip` из уже скачанных Parquet:
  - `1D full year` по BTC/ETH
  - `4H monthly` за последние 12 месяцев по BTC/ETH
  - `1H recent` за последние ~90 дней, разбито по месяцам
  - `15m recent` за последние 28 дней, разбито на 4 недели
- **Log_full** — создаёт архив логов `log_full_*.zip`:
  - `logs/full.log`
  - `logs/errors.log`
  - индекс готовых архивов
  - runtime snapshot
- **Reset** — останавливает фоновую задачу, чистит runtime state, удаляет сохранённый API key/secret и временную рабочую папку. Готовые архивы `exports/` и логи не удаляет.


## Прогресс выполнения

При нажатии **Parquet** и **Charts** бот отправляет живой прогресс в Telegram:

```text
Parquet: 0% — старт
Parquet: 10% — MEXC доступен, meta получена
Parquet: 20% — BTCUSDT скачивается
...
Parquet: 100% — архив готов
```

То же самое для **Charts**: бот пишет 0/10/20/.../100% и количество уже отрисованных графиков. Это сделано специально, чтобы было видно, что процесс живой и не завис.

## Установка

```bash
cd mexc_research_collector_bot
./install.sh
nano .env
source venv/bin/activate
python run.py
```

В `.env` обязательно заполнить:

```bash
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
```

`ADMIN_TELEGRAM_ID` нужен, чтобы бот отвечал только тебе.

## Что отправлять в ChatGPT

Сначала отправляй data archive:

```text
research_input_BTC_ETH_data_*.zip
```

Потом, если нужно, charts archive:

```text
research_input_BTC_ETH_charts_*.zip
```

Графики второстепенны. Главный файл для анализа — Parquet-архив.

## Telegram file limit

Стандартный Telegram Bot API обычно не даёт отправить файл больше ~50 MB через `sendDocument`.
Бот автоматически режет большие архивы на `.part001`, `.part002`, ... и отправляет README для склейки.
Если у тебя есть SSH/SFTP доступ к серверу, лучше скачать оригинальный `.zip` из папки `storage/exports/`.

Склеить на Linux/macOS:

```bash
cat research_input_BTC_ETH_data_*.zip.part* > research_input_BTC_ETH_data.zip
```

## Systemd

Пример systemd unit лежит в:

```text
systemd/mexc-research-collector.service
```

Пример установки в `/opt`:

```bash
sudo mkdir -p /opt/mexc_research_collector_bot
sudo cp -r . /opt/mexc_research_collector_bot/
cd /opt/mexc_research_collector_bot
./install.sh
nano .env
sudo cp systemd/mexc-research-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mexc-research-collector
sudo systemctl start mexc-research-collector
sudo systemctl status mexc-research-collector
```

## Формат свечей Parquet

Колонки:

```text
open_time
            Unix ms open time
datetime_utc
            UTC datetime
open/high/low/close
            float OHLC
volume
            base asset volume
close_time
            Unix ms close time
quote_volume
            quote asset volume
symbol
            BTCUSDT / ETHUSDT
interval
            1m
source_exchange
            MEXC_SPOT
```

## Настройки

В `.env` можно менять:

```bash
SYMBOLS=BTCUSDT,ETHUSDT
DAYS_BACK=365
BASE_INTERVAL=1m
TELEGRAM_SEND_LIMIT_MB=48
```

Для нашего research-процесса лучше оставить именно BTCUSDT/ETHUSDT и 1m за 365 дней.

## Coolify + GitHub

Для Coolify я добавил готовые файлы:

```text
Dockerfile
docker-entrypoint.sh
docker-compose.yml
.dockerignore
.gitignore
.env.coolify.example
COOLIFY.md
```

Рекомендуемый режим в Coolify: **Docker Compose** из GitHub repo.

Обязательные переменные окружения в Coolify:

```bash
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
```

Папка данных внутри контейнера:

```text
/app/storage
```

Она вынесена в persistent volume `mexc_research_storage`, чтобы после redeploy не потерять свечи, архивы, логи и encrypted API state.

Подробная инструкция: `COOLIFY.md`.
