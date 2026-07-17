# Gmail v65 — Coolify port-80 route fix

Изменения ограничены Gmail/Coolify-подключением. Режимы сканирования, Intraday, фоновые task и торговая логика не изменялись.

- Coolify magic URL заменён с общего `SERVICE_URL_GMAIL-AUTH` на явный `SERVICE_URL_GMAIL-AUTH_80`.
- Port-specific URL имеет приоритет над общими и устаревшими значениями при формировании Redirect URI.
- В Docker-образ добавлен `EXPOSE 80`, чтобы Traefik однозначно видел порт callback-сервера.
- Старые переменные оставлены только как fallback для совместимости с уже сохранёнными настройками.
- OAuth callback, health probe, Gmail API, хранение секретов и отправка ZIP не менялись.
