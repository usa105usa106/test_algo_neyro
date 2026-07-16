# v62_full_GMAIL_GATEWAY_PORT_PERSISTENCE

Изменения только в Gmail/Coolify-интеграции и хранении секретов.

## Callback без ручного Domains

- Добавлен отдельный сервис `gmail-auth-gateway` на Nginx.
- Coolify создаёт публичный URL через `SERVICE_URL_GMAIL-AUTH=/gmail/callback`.
- Gateway принимает внешний HTTPS на стандартном порту и проксирует:
  - `/gmail/callback` → `chatgpt-scan-bot:8080/gmail/callback`;
  - `/healthz` → `chatgpt-scan-bot:8080/healthz`.
- Бот получает тот же FQDN через `SERVICE_FQDN_GMAIL-AUTH` и всегда формирует HTTPS Redirect URI.
- Ручное поле Domains и ручное добавление `:8080` не требуются.

## Сохранение Gmail после Redeploy

- Основное хранилище закреплено глобальным volume `chatgpt_scan_storage`.
- Старое хранилище v61 монтируется read-only и при первом запуске автоматически переносит `secrets/` и `state/`.
- Client ID, Client Secret, refresh token и журнал отправленных архивов сохраняются между Redeploy и пересозданием ресурса Coolify.
- Добавлен стабильный Coolify-ключ шифрования `SERVICE_REALBASE64_32_GMAIL-STORE`.
- Старые зашифрованные данные v61 автоматически перешифровываются новым стабильным ключом без показа секретов.

## Безопасность отправки архивов

Логика v61 сохранена:

- Gmail вызывается только после успешной отправки ZIP в Telegram;
- проверяются имя, размер и SHA-256 ровно того ZIP;
- один архив не отправляется повторно;
- письмо ищется в Gmail → Отправленные;
- тема содержит режим и точное имя ZIP.

## Не изменено

Не изменены `archive_builder.py`, Intraday engine/task, MEXC downloader, графики, Stress Test и остальные режимы.
