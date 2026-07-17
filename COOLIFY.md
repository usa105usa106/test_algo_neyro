# Coolify — версия 68

## Что исправлено

Telegram-бот и публичный Gmail callback теперь разделены на два сервиса:

```text
chatgpt-scan-bot     — исходный runtime бота, внутренний порт 8080
gmail-auth-gateway   — отдельный nginx, публичный порт 80
```

У бота нет публичного Traefik-маршрута и нет Docker healthcheck. Поэтому проблема Gmail больше не может остановить Telegram polling, `/start`, `/ping`, сканеры или фоновые режимы.

Coolify направляет внешний HTTPS только на `gmail-auth-gateway`. Gateway проксирует:

```text
/healthz         -> chatgpt-scan-bot:8080/healthz
/gmail/callback  -> chatgpt-scan-bot:8080/gmail/callback
```

Его собственный `/gateway-healthz` не зависит от запуска Telegram и используется Docker healthcheck.

## Обязательные переменные

```env
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
```

Gmail Client ID и Client Secret вводятся через Telegram. Порт, домен и `GMAIL_PUBLIC_BASE_URL` вручную не задаются.

## Coolify magic URL

Gateway объявляет:

```text
SERVICE_URL_GMAILAUTH_80
```

Бот получает домен через:

```text
SERVICE_FQDN_GMAILAUTH
```

И формирует:

```text
https://<generated-domain>/healthz
https://<generated-domain>/gmail/callback
```

Идентификатор `GMAILAUTH` специально не содержит дефисов и подчёркиваний.

## Deploy

1. Загрузить архив версии 68 в существующий Docker Compose resource.
2. Сделать полный Redeploy/Rebuild.
3. Убедиться, что запущены оба сервиса:
   - `chatgpt-scan-bot`
   - `gmail-auth-gateway`
4. В Telegram проверить `/start` и `/ping`.
5. Нажать `📧 Подключить Gmail` и открыть новую кнопку проверки сервера.
6. В браузере должен появиться JSON с `"ok": true` и `"probe_confirmed": true`.
7. В Google OAuth Client добавить новый Redirect URI, который покажет бот.

Старую ссылку проверки и старый Redirect URI использовать нельзя: после смены magic identifier публичный домен изменится.

## Сертификат

TLS завершается в Coolify/Traefik. Контейнеры внутри общаются по HTTP. Архив не устанавливает сертификат вручную.

При корректной работе Coolify ссылка открывается без предупреждения браузера. Если всё ещё показывается самоподписанный сертификат, это уже проблема ACME/Traefik на VPS: должны быть доступны входящие порты 80 и 443, а proxy должен успешно выпустить сертификат для нового generated domain.

## Хранилища

Основное:

```text
/data/chatgpt-scan-bot-storage -> /app/storage
```

Резервное:

```text
chatgpt_scan_storage -> /app/storage_backup
```

Существующие Gmail credentials, token и журнал отправленных ZIP сохраняются.

## Ожидаемые логи бота

```text
v69 primary storage: /app/storage
v69 backup storage: /app/storage_backup
v69 OAuth listen: 0.0.0.0:8080
v69 Gmail public FQDN: <generated-domain>
Bot started. Version: 69_full_GMAIL_LOG_MAIL_DIAGNOSTICS
```

## Gmail diagnostic command — v69

После Redeploy команда `/log_mail` отправляет отдельный диагностический TXT.
Он объединяет безопасный журнал самого OAuth-сервера и nginx gateway.

Для gateway используется volume `gmail_mail_logs`. В query string находятся одноразовые
OAuth/probe значения, поэтому gateway намеренно пишет только `$uri`, без `$args` и `$request_uri`.
