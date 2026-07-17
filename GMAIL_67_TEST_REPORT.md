# Gmail 67 test report

Проверено:

1. Entrypoint заканчивается `exec "$@"`, поэтому Telegram-бот является основным процессом контейнера.
2. Ошибка nginx/config не блокирует запуск Telegram-бота.
3. Блокирующая синхронная проверка `/router-healthz` до старта Python удалена.
4. Coolify route и healthcheck остаются на порту `80`.
5. Gmail `/healthz` и `/gmail/callback` остаются проксированы на `8080`.
6. Негmail-логика побайтно совпадает с версией 66.
