# Gmail v69 — `/log_mail`

Изменения только в диагностике подключения Gmail и маршруте логов gateway.
Сканеры, Intraday, Stress Test, архивы, MEXC и task-логика не изменялись.

## Новая команда

`/log_mail` создаёт и отправляет отдельный текстовый отчёт:

- текущая Gmail-конфигурация и состояние callback-сервера;
- наличие OAuth-клиента и токена без вывода их значений;
- подтверждение публичной проверки для текущего Telegram-чата;
- пошаговые события: создание probe, достижение `/healthz`, ввод OAuth-клиента,
  создание Google URL, приход callback, обмен кода, userinfo, сохранение токена,
  тестовое письмо и отправка архивов;
- безопасный access/error лог nginx gateway с upstream status и временем ответа.

## Защита секретов

В отчёт не записываются:

- Google Client Secret;
- authorization code;
- OAuth state и probe-token целиком;
- access token и refresh token;
- query string gateway-запросов.

Для state/probe сохраняется только короткий SHA-256 fingerprint, чтобы сопоставить этапы.

## Gateway logs

Добавлен отдельный Docker volume `gmail_mail_logs`:

- gateway пишет безопасные логи в `/var/log/gmail`;
- бот читает их read-only из `/app/storage/gateway_logs`;
- healthcheck `/gateway-healthz` исключён из access log, чтобы не засорять отчёт.
