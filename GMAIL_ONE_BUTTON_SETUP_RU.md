# Gmail OAuth через Telegram — версия 61

Свой домен не нужен. Coolify автоматически создаёт публичный HTTPS callback через:

```text
SERVICE_URL_GMAIL-AUTH_8080=/gmail/callback
```

Google `Client ID` и `Client Secret` в Coolify больше не добавляются. Они вводятся только через личный Telegram-чат с ботом и сохраняются зашифрованно в постоянном volume `/app/storage`.

## Порядок подключения

1. Загрузить версию 61 в Coolify и нажать Deploy. Старый volume `chatgpt_scan_storage` не удалять.
2. В Telegram открыть `/start` → **📧 Подключить Gmail**.
3. Бот покажет точный `Redirect URI`, созданный Coolify.
4. В Google Cloud включить Gmail API и создать OAuth Client типа **Web application**.
5. В `Authorized redirect URIs` вставить точный адрес, показанный ботом.
6. В Telegram нажать **🔑 Ввести Client ID и Secret**.
7. Отправить Client ID отдельным сообщением, затем Client Secret. Бот попытается сразу удалить оба сообщения и сохранит значения зашифрованно.
8. Нажать **🔐 Войти через Google**, выбрать Gmail и разрешить отправку писем.
9. После сообщения `✅ Gmail подключён` нажать **🧪 Отправить тест** и проверить папку **Gmail → Отправленные**.

Redeploy после ввода Client ID/Secret не нужен.

## Как отправляются архивы

- Сначала ZIP успешно отправляется в Telegram.
- До Telegram-отправки бот фиксирует имя, размер и SHA-256 ZIP.
- После Telegram-отправки бот повторно проверяет тот же файл и его имя.
- Только после совпадения ZIP отправляется через Gmail API на тот же Gmail.
- Письмо всегда сохраняется в **Отправленных**; анализ следует брать оттуда.
- Повтор одного и того же ZIP блокируется постоянным журналом по Gmail-адресу, имени, размеру и SHA-256.
- Если соединение оборвалось и результат Gmail неизвестен, автоматический повтор блокируется, чтобы не создать дубль.

Telegram показывает подтверждение отдельным сообщением сразу после архива:

```text
📧 Архив отправлен один раз на example@gmail.com: intraday_multi-1430_1607.zip
Gmail → Отправленные · SHA-256: 12ab34cd56ef…
```

## Хранение секретов

Файлы в persistent volume:

```text
/app/storage/secrets/gmail_client.enc.json
/app/storage/secrets/gmail_oauth.enc.json
/app/storage/state/fernet.key
/app/storage/state/gmail_sent_archives.json
```

Client Secret и refresh token не пишутся в обычные логи. `/reset` их не удаляет. Кнопка **Отключить Gmail** удаляет refresh token, но оставляет Client ID/Secret для быстрого повторного подключения.
