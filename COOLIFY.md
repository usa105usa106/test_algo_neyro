# Coolify deploy — ChatGPT Scan Bot 62

## Что вводить в Coolify

Только существующие обязательные переменные:

```text
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
```

Gmail Client ID и Client Secret вводятся через Telegram. Домен, callback и порт вручную не прописываются.

## Deploy

1. Загрузи версию 62 в существующий ресурс Coolify и нажми Deploy/Redeploy.
2. В стеке появятся два сервиса:
   - `chatgpt-scan-bot` — Telegram-бот и callback на внутреннем порту `8080`;
   - `gmail-auth-gateway` — публичный вход на порту `80`.
3. Coolify автоматически создаёт URL с тем же идентификатором `GMAIL-AUTH`.
4. При первом запуске настройки из старого volume v61 переносятся в глобальный `chatgpt_scan_storage`.

В логах бота должна появиться строка:

```text
v62 storage migration: ...
```

## Проверка

Открой прежний адрес, заменив callback на healthz:

```text
https://ТВОЙ-COOLIFY-ДОМЕН/healthz
```

Ожидается JSON с `"ok": true`.

Для текущего домена пользователя:

```text
https://n1tsckrun1zjl962g41cxzar.2.27.62.210.sslip.io/healthz
```

Google Cloud оставь с прежним Redirect URI:

```text
https://n1tsckrun1zjl962g41cxzar.2.27.62.210.sslip.io/gmail/callback
```

OAuth-клиент заново создавать не нужно.

## После Deploy

В Telegram нажми `📧 Подключить Gmail`.

- Если Client ID/Secret перенеслись, сразу появится `🔐 Войти через Google`.
- Если старое хранилище не найдено, бот попросит ввести Client ID и Secret один раз.

После подключения нажми `🧪 Отправить тест`.
