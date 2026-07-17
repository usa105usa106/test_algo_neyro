# Подключение Gmail — v70

1. Развернуть архив в Coolify с container port `80`.
2. Убедиться, что `/start` и `/ping` отвечают.
3. Нажать «Подключить Gmail» → «Проверить сервер».
4. В браузере получить JSON `{"ok": true, ...}` без предупреждения TLS.
5. В Google Cloud добавить Redirect URI, который показывает бот:
   `https://<домен>/gmail/callback`.
6. Ввести Client ID и Client Secret в Telegram.
7. Открыть ссылку Google и подтвердить доступ.
8. Проверить `/log_mail`: должны быть события health request, OAuth callback, token exchange и сохранение аккаунта.

В URL не добавляются `:80`, `:8080` или дополнительные gateway-пути.
