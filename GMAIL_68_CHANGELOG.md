# Gmail 68 changelog

- Восстановлен исходный runtime Telegram-бота версии 64: `bot.py`, `run.py`, сканеры, Intraday, MEXC, графики и task-логика не изменены.
- Удалён nginx из контейнера бота.
- Удалён healthcheck с сервиса бота, чтобы Gmail route не мог остановить Telegram polling.
- Добавлен отдельный сервис `gmail-auth-gateway` на стандартном `nginx:alpine`.
- Публичный Coolify URL создаётся на gateway через `SERVICE_URL_GMAILAUTH_80`.
- Бот получает тот же домен через `SERVICE_FQDN_GMAILAUTH` и слушает callback только внутри сети на `8080`.
- Gateway имеет независимый `/gateway-healthz` и проксирует только `/healthz` и `/gmail/callback`.
- Сохранены прежние bind/volume хранилища Gmail и защита от повторной отправки ZIP.
- Новый identifier не содержит дефисов или подчёркиваний, что исключает неоднозначность magic-variable имени и port suffix.
