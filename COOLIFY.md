# Coolify deploy — BTC/ETH Research Collector v14-2y

Эта версия собирает **BTC/ETH 1m Parquet за 730 дней / 2 года** и расширенный charts archive.

## Источник

- Binance Spot public klines: `https://api.binance.com/api/v3/klines`
- Futures не используются.
- Trading endpoints отсутствуют.

## Env в Coolify

Минимально:

```env
TELEGRAM_BOT_TOKEN=xxx
ADMIN_TELEGRAM_ID=123456789
```

Опционально:

```env
DAYS_BACK=730
SYMBOLS=BTCUSDT,ETHUSDT
BASE_INTERVAL=1m
TELEGRAM_SEND_LIMIT_MB=48
DATA_ROOT=/app/storage
```

## Deploy

1. Залей все файлы из архива в GitHub repo без подпапки.
2. Создай Coolify service из repo.
3. Укажи env.
4. Deploy.
5. В Telegram: `/start`.
6. Нажми `Ping`; должно быть `version: v14-2y`.
7. Нажми `Reset`.
8. Нажми `Parquet` и дождись 100%.
9. Нажми `Charts`.

## Важные замечания

- 2 года 1m данных — это около 1,051,200 свечей на символ.
- Архив может быть больше лимита Telegram Bot API. Бот умеет отправлять part-файлы и README_REASSEMBLE.
- Если есть доступ к серверу/Coolify volume, лучше скачать оригинальный `.zip` напрямую из `storage/exports`.

## Что отправить в ChatGPT после сбора

Обязательно:

```text
research_input_BTC_ETH_data_*.zip
```

Опционально:

```text
research_input_BTC_ETH_charts_*.zip
log_full_*.zip, если была ошибка
```

После загрузки data archive можно просить: проверить NSM v2 на 2-летних данных и продолжить research.
