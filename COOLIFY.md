# Coolify — версия 65

## Обязательные переменные

```env
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
```

Gmail Client ID, Client Secret, Redirect URI, порт и домен вручную в Coolify не добавляются.

## Deploy

1. Загрузить проект версии 65 в существующий ресурс.
2. Нажать Deploy/Redeploy.
3. Убедиться, что контейнер `chatgpt-scan-bot` healthy.
4. Открыть Telegram → `📧 Подключить Gmail`.
5. Выполнить встроенную двухшаговую проверку сервера.

## Маршрутизация

Compose объявляет:

```text
SERVICE_URL_GMAIL-AUTH_80
GMAIL_OAUTH_LISTEN_PORT=80
expose: 80
```

Сервис слушает контейнерный порт 80. Coolify получает его явно через `SERVICE_URL_GMAIL-AUTH_80`, а Docker-образ объявляет `EXPOSE 80`; отдельный gateway, ручной Domains и видимый `:8080` не используются.

## Хранилища

Основное:

```text
/data/chatgpt-scan-bot-storage -> /app/storage
```

Резервное:

```text
chatgpt_scan_storage -> /app/storage_backup
```

Gmail-секреты, OAuth token и журнал ZIP атомарно собираются в резервный `gmail_bundle_backup.json`. При пустом основном хранилище бот восстанавливает связку ключа и encrypted files из этого bundle; существующий MEXC API key при восстановлении не повреждается.

## Диагностика

В Deploy logs должны быть строки:

```text
v65 primary storage: /app/storage
v65 backup storage: /app/storage_backup
v65 OAuth listen: 0.0.0.0:80
SERVICE_URL_GMAIL-AUTH_80=<generated>
```

Если Telegram не показывает публичный URL, значит Coolify не создал magic URL. Если кнопка проверки открывает `no available server`, контейнер или healthcheck не стал healthy; Client ID/Secret бот в этом состоянии не принимает.
