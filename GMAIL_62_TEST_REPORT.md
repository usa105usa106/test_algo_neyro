# Gmail/Coolify v62 — отчёт проверки

Версия: `62_full_GMAIL_GATEWAY_PORT_PERSISTENCE`

## Результат

Пройдено: **27/27 автоматических тестов**.

Проверено:

- FQDN Coolify преобразуется в точный HTTPS callback `/gmail/callback`;
- callback-сервер реально слушает TCP-порт и отвечает на `/healthz`;
- Nginx-конфигурация синтаксически валидна;
- gateway проксирует порт 80 на порт 8080 бота;
- старые Client ID/Secret и Gmail token мигрируют вместе с исходным `fernet.key`;
- старые секреты перешифровываются стабильным ключом v62;
- новое хранилище не перезаписывается старыми данными;
- крупные candles/charts/logs/exports при миграции не копируются;
- один и тот же ZIP отправляется один раз, включая параллельные вызовы;
- изменение ZIP или несовпадение имени Telegram блокирует Gmail;
- Gmail не вызывается, если отправка ZIP в Telegram завершилась ошибкой;
- тема и имя вложения совпадают с точным именем Telegram ZIP;
- неопределённый сетевой результат блокирует автоматический повтор.

## Регрессия

Побайтово не изменены:

- `archive_builder.py`;
- `intraday_archive.py`;
- `intraday_engine.py`;
- `mexc.py`;
- `charts.py`;
- `file_utils.py`;
- `logging_setup.py`;
- `run.py`;
- `requirements.txt`;
- `Dockerfile`.

Реальный внешний callback Coolify/Google можно окончательно подтвердить только после Deploy на VPS открытием `/healthz` и завершением OAuth-входа.
