# Проверка версии 61

Версия: `61_full_GMAIL_TELEGRAM_SETUP_SENT_DEDUP`
База: `60_full_GMAIL_COOLIFY_AUTO_DOMAIN`

## Что изменено

- `bot.py`: Telegram-ввод Google Client ID/Secret, удаление чувствительных сообщений, порядок Telegram → Gmail, подтверждения и обработка ошибок.
- `gmail_oauth.py`: динамические зашифрованные credentials, точная идентификация ZIP, idempotency ledger, защита от неопределённого повтора, отключён access-log OAuth callback.
- `security.py`: зашифрованное хранение Google OAuth client и постоянный журнал отправленных архивов.
- `config.py`: номер версии.
- `docker-compose.yml` и документация: Gmail Client ID/Secret больше не требуются в Coolify.

## Что не изменено

SHA-256 файлов версии 60 и 61 полностью совпал:

- `archive_builder.py` — `2f500ca55a47a8c46af8b6e0d75983071346364bb218adbcd96772d2b3d60a80`
- `intraday_archive.py` — `f39679da2be3589e1068a32f2d8b09e18fc7dfef0df49f5dc2fb096c73e7a975`
- `intraday_engine.py` — `55269d86cec56c20a5e86893f665ab3bee1ab632436fcba9b646298e1f8a87ae`
- `mexc.py` — `789fea7f8c8b4c3c44ae3bcdcdb957ee12dae99ecd07ac2928bca6e4777b44ff`
- `charts.py` — `558a565c7726cc7d99978b4ef5e4b29b199f8bd5958fcd2a6329d00b0bcaf145`
- `file_utils.py` — `1664a9a5c66d418b489a28ff1273a2f55608123f71cde223e1fe7f00aa01ad46`

То есть task/setup содержимое, логика анализа, загрузка свечей и прочие режимы не менялись.

## Автоматические тесты

Пройдено `18/18` тестов:

- Client ID/Secret вводятся через Telegram, оба сообщения удаляются, значения не отражаются в ответах.
- Credentials сохраняются зашифрованно и работают без Gmail-переменных Coolify.
- Неверные Client ID/Secret отклоняются.
- Замена OAuth client удаляет старый refresh token.
- OAuth callback сохраняет refresh token и уведомляет Telegram.
- MIME-вложение имеет ровно то же имя и байты ZIP.
- Subject, служебные заголовки и SHA-256 содержат точное имя архива.
- ZIP, изменённый после Telegram, не отправляется.
- Несовпадение имени файла, возвращённого Telegram, блокирует Gmail.
- Gmail вызывается только после успешного `send_document` Telegram.
- При ошибке Telegram Gmail вообще не вызывается.
- Standard, A+ Hunter и Intraday используют один и тот же безопасный путь отправки.
- Повторный вызов того же архива не создаёт второе письмо.
- Два параллельных вызова создают только одно письмо.
- Защита от дублей сохраняется после перезапуска manager/container.
- Явная ошибка Gmail `4xx` снимает reservation и разрешает повтор после исправления.
- `401` обновляет access token и делает один безопасный повтор.
- `408`, `5xx`, timeout и обрыв соединения отмечаются как uncertain; автоматический повтор блокируется.

## Дополнительные проверки

- Все Python-файлы компилируются и проходят синтаксис Python 3.11.
- `docker-compose.yml` корректно разбирается как YAML.
- В Compose отсутствуют `GMAIL_CLIENT_ID` и `GMAIL_CLIENT_SECRET`.
- Присутствует Coolify magic URL `SERVICE_URL_GMAIL-AUTH_8080=/gmail/callback`.
- Публичный `/healthz` не раскрывает Gmail-адрес.
- OAuth callback access-log отключён, чтобы authorization code не попадал в `full.log`.

## Ограничение проверки

Реальный вход в Google и реальная отправка через Gmail API требуют пользовательского Google OAuth client и доступа к Google. В этой сборочной среде они не выполнялись. Полный callback/token/send/error flow проверен имитацией ответов Google и Gmail API, включая `401`, `4xx`, `5xx`, timeout и успешную отправку.
