# Gmail v69 test report

Дата: 2026-07-17

## Результат

- `pytest -q`: **34 passed**
- `python -m py_compile`: успешно для `bot.py`, `gmail_oauth.py`, `config.py`
- `docker-compose.yml`: YAML успешно разобран
- `gmail-gateway.conf`: `nginx -t` успешно

## Проверено

- команда `/log_mail` зарегистрирована;
- отчёт содержит пошаговые Gmail-события;
- Client Secret, OAuth code/state/probe и токены не попадают в отчёт;
- gateway access/error logs добавляются в отчёт;
- gateway log format не содержит `$request_uri`, `$args` или полного `$request`;
- gateway и bot используют общий отдельный log-volume;
- scan/task/MEXC/chart/archive файлы остаются побайтно идентичны v64.
