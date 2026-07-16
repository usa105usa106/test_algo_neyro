# Gmail 64 changelog

- OAuth callback перенесён с нестандартного внутреннего порта 8080 на стандартный порт 80.
- Удалена зависимость от `SERVICE_URL_GMAIL-AUTH_8080`; используется `SERVICE_URL_GMAIL-AUTH`.
- Callback-сервер запускается всегда, даже если Coolify не передал публичный URL. Это исключает скрытый отказ listener и даёт локальный healthcheck.
- В Telegram добавлена готовая кнопка `/healthz` с одноразовым probe-token.
- Ввод Client ID/Secret и создание Google authorization URL запрещены до подтверждённого внешнего probe.
- Добавлено основное bind-хранилище и резервный глобальный Docker volume.
- Резервная копия записывается одним атомарным `gmail_bundle_backup.json`, чтобы ключ и ciphertext не расходились при сбое.
- При пустом основном хранилище восстанавливается целая зашифрованная Gmail-связка; при существующем основном ключе Gmail пере-шифровывается под него без повреждения MEXC API credentials.
- Добавлен постоянный Storage ID и счётчик запусков для видимой проверки Redeploy.
- Логика отправки точного ZIP, SHA-256 и dedup не менялась.
- `archive_builder.py`, Intraday engine/task/data downloader, графики и остальные режимы не менялись.
