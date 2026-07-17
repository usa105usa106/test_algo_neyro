# Coolify deployment — v70

## Причина предыдущего `no available server`

В фактическом runtime `/log_mail` показал, что callback-сервер слушал `0.0.0.0:80`,
а Dockerfile v69 объявлял `EXPOSE 8080`. Отдельный nginx gateway не был запущен,
потому что ресурс разворачивался как обычное Dockerfile-приложение, а не как
двухсервисный Compose stack.

## Текущая схема

```text
Firefox / Google OAuth
        | HTTPS :443
        v
Coolify / Traefik
        | HTTP :80
        v
chatgpt-scan-bot
        |- Telegram polling
        |- GET /healthz
        `- GET /gmail/callback
```

Контейнер и callback-сервер используют один и тот же порт `80`. Отдельного nginx
gateway и отдельного gateway-healthcheck больше нет.

## Вариант A — Dockerfile build pack

1. Port Exposes: `80`.
2. Domain: HTTPS-домен приложения.
3. Healthcheck path: `/healthz` либо healthcheck выключен.
4. Полный Redeploy без кэша.

Приложение автоматически читает `COOLIFY_URL` и строит:

```text
https://<domain>/healthz
https://<domain>/gmail/callback
```

## Вариант B — Docker Compose build pack

Compose содержит один сервис и magic variable `SERVICE_URL_GMAILAUTH_80`. Coolify
создаёт HTTPS URL и направляет его на container port 80.

## Проверка

1. `/start` и `/ping` отвечают.
2. «Подключить Gmail» → «Проверить сервер».
3. В браузере должен открыться JSON с `"ok": true`.
4. `/log_mail` должен содержать `event=health_request_received`.
5. Затем ввести Client ID и Client Secret и завершить Google OAuth.

Если снова показан `no available server`, в Coolify проверить, что текущая версия
действительно собрана без кэша, Port Exposes равен `80`, а старый контейнер остановлен.
