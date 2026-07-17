# Gmail 68 — отчёт проверки

Дата сборки: 2026-07-17

## Автотесты

```text
31 passed
```

Проверено:

1. Новый `SERVICE_FQDN_GMAILAUTH` формирует HTTPS callback `/gmail/callback`.
2. OAuth health probe и callback server работают на внутреннем порту 8080.
3. OAuth URL, token exchange, encrypted credentials, persistent backup и Gmail dedup проходят прежние тесты.
4. Compose содержит два изолированных сервиса.
5. У `chatgpt-scan-bot` нет healthcheck и публичного URL route.
6. `gmail-auth-gateway` имеет route на port 80 и независимый healthcheck.
7. Nginx proxy сохраняет query string для `probe`, `state` и `code`.
8. Неизвестные пути gateway возвращают 404.
9. Docker entrypoint всегда выполняет переданную команду бота через `exec`.
10. Docker Compose YAML корректно разбирается.
11. Все Python-файлы проходят `compileall`.
12. Shell entrypoint проходит `sh -n`.

## Локальная интеграционная проверка gateway

На реальном nginx локально проверено:

```text
/gateway-healthz  -> 200
/healthz?probe=abc -> 200, query сохранён
/gmail/callback?state=s1&code=c1 -> 200, query сохранён
/other -> 404
```

## Регрессия остальных режимов

Следующие runtime-файлы побайтно совпадают с пользовательским архивом v64:

```text
archive_builder.py
bot.py
charts.py
file_utils.py
intraday_archive.py
intraday_engine.py
logging_setup.py
mexc.py
run.py
```

Следовательно, сканеры, Stress Test, Intraday, архивы, MEXC и остальные task-режимы этой правкой не изменялись.

## Ограничение

Внешний сертификат выпускает установленный на VPS Coolify/Traefik. Локально проверены приложение, gateway и проксирование; доступность ACME и входящих портов 80/443 можно подтвердить только после Redeploy на сервере.
