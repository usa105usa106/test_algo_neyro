# Gmail v71 — Coolify Docker health status fix

## Причина сбоя v70

Dockerfile содержал `HEALTHCHECK NONE`. Coolify распознал наличие инструкции
`HEALTHCHECK`, начал ждать `.State.Health.Status`, но Docker не создаёт объект
`State.Health`, когда healthcheck отключён через `NONE`. В результате rolling
update завершался ошибкой `map has no entry for key "Health"`.

## Исправление

- `HEALTHCHECK NONE` заменён на реальную Docker-проверку `GET /healthz` на
  `127.0.0.1:80` через стандартную библиотеку Python.
- Порт приложения остаётся `80`.
- Telegram-бот, Gmail OAuth, сканеры и остальные task-файлы не изменялись.
- `/log_mail` сохранён.

## Coolify

Встроенный Healthcheck в UI можно оставить выключенным: Dockerfile теперь сам
создаёт корректный `.State.Health.Status`. После обновления нужен полный Redeploy.
