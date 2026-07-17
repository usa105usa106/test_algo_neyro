# Gmail v65 — test report

## Проверки

- Полный набор unit/integration тестов проекта.
- Приоритет `SERVICE_URL_GMAIL-AUTH_80` над устаревшим общим URL.
- Поддержка альтернативного port-specific имени `SERVICE_URL_GMAILAUTH_80`.
- Нормализация внешнего URL в HTTPS и удаление внутреннего `:80`.
- Наличие точной magic-переменной `SERVICE_URL_GMAIL-AUTH_80` в Compose.
- Отсутствие общего `SERVICE_URL_GMAIL-AUTH` как активной Compose-переменной.
- Наличие `EXPOSE 80` в Dockerfile.

## Результат

`python -m unittest discover -s tests -v` → **32 tests, OK**.

## Ограничение среды

Локально проверяются конфигурация, listener и весь Gmail flow. Выпуск публичного TLS-сертификата выполняет Coolify/Traefik уже на VPS после Redeploy.
