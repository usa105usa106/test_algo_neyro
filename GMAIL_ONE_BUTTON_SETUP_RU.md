# Gmail OAuth — версия 68

## Главное изменение

Gmail callback вынесен из контейнера Telegram-бота в отдельный публичный gateway. Поэтому неисправность маршрута Gmail больше не может остановить самого бота.

Схема:

```text
браузер / Google
       |
    HTTPS 443
       |
Coolify / Traefik
       |
 gmail-auth-gateway:80
       |
 chatgpt-scan-bot:8080
```

## Подключение

1. После Redeploy отправить боту `/start` и `/ping`.
2. Нажать `📧 Подключить Gmail`.
3. Нажать `🌐 1. Открыть проверку сервера`.
4. В браузере должен появиться ответ:

```json
{"ok": true, "service": "gmail-oauth-callback", "probe_confirmed": true}
```

5. Вернуться в Telegram и нажать `✅ 2. Проверить результат`.
6. В Google Cloud создать OAuth Client типа **Web application**.
7. В `Authorized redirect URIs` вставить точный новый URI, показанный ботом:

```text
https://<generated-domain>/gmail/callback
```

8. Ввести Client ID и Client Secret через Telegram.
9. Нажать `🔐 Войти через Google`.
10. Нажать `🧪 Отправить тест` и проверить Gmail → Отправленные.

## Важно после обновления

Версия 68 использует новый Coolify identifier `GMAILAUTH`, поэтому generated domain может отличаться от старого. В Google Cloud нужно добавить именно новый Redirect URI из сообщения бота.

Никакие `:80`, `:8080`, `/gateway-healthz` или ручной Domains в URI добавлять не нужно.

## Если проверка не проходит

- `no available server` не должен появляться, потому что публичный gateway имеет независимый healthcheck;
- `502` означает, что gateway работает, но бот ещё не слушает внутренний `8080` — проверить логи `chatgpt-scan-bot`;
- предупреждение о недоверенном сертификате означает проблему Coolify/Traefik ACME, а не Python-кода.

## Сохранение данных

Основное хранилище:

```text
/data/chatgpt-scan-bot-storage
```

Резервное:

```text
chatgpt_scan_storage
```

Client ID, Client Secret, refresh token и журнал защиты от дублей сохраняются между Redeploy.
