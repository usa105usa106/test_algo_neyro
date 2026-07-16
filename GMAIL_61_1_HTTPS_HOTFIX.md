# Gmail OAuth 61.1 HTTPS hotfix

Исправлена ошибка `400: redirect_uri_mismatch`.

Coolify мог передавать внутрь контейнера технический URL с `http://`, хотя снаружи сервис работает через HTTPS. Версия 61 использовала это значение без исправления, поэтому Google получал `redirect_uri=http://...`, а в OAuth Client был зарегистрирован `https://...`.

В 61.1:

- любой внешний callback принудительно нормализуется в `https://`;
- `http://localhost` оставлен только для локальной разработки;
- один и тот же HTTPS Redirect URI применяется и в ссылке авторизации, и при обмене кода на токены;
- Client ID, Client Secret и токены в persistent volume не удаляются при Redeploy;
- task-файлы, Intraday и остальные режимы не изменены.
