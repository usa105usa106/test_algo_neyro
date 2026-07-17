# Gmail 67 — non-blocking Telegram startup

## Исправлено

- Удалена блокирующая проверка `/router-healthz` из `docker-entrypoint.sh`, которая могла завершить контейнер до запуска `python run.py`.
- Telegram-бот снова запускается как основной PID 1 через `exec`, а не как фоновый дочерний процесс shell.
- Ошибка запуска nginx теперь отключает только публичный Gmail callback и не останавливает `/start`, `/ping`, сканеры и остальные task.
- Маршрут Coolify остаётся на контейнерный порт `80`; nginx проксирует Gmail endpoints на внутренний listener `8080`.

## Не изменялось

`bot.py`, сканеры, intraday, MEXC, архивы, графики, persistence/security и остальные режимы побайтно не изменялись относительно версии 66.
