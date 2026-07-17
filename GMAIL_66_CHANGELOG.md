# Gmail 66 — stable Coolify port-80 router

## Исправлено

- Порт `80`, на который направляет Coolify/Traefik, теперь всегда занимает отдельный nginx-router.
- Docker healthcheck проверяет независимый `/router-healthz`, поэтому Telegram initialization больше не может оставить Traefik без healthy backend.
- `/healthz` и `/gmail/callback` проксируются во внутренний Gmail OAuth listener на `127.0.0.1:8080`.
- TLS/сертификат по-прежнему завершается в Coolify/Traefik; приложение не хранит и не выпускает сертификаты.

## Не изменялось

Сканеры, режимы, intraday-задачи, MEXC, построение архивов, графики и логика отправки архивов не менялись.
