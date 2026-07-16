# v60_full_GMAIL_COOLIFY_AUTO_DOMAIN

- Убран обязательный ручной `GMAIL_REDIRECT_URI`.
- Coolify сам создаёт публичный callback URL через `SERVICE_URL_GMAIL-AUTH_8080=/gmail/callback`.
- Бот показывает точный redirect URI в Telegram до создания Google OAuth клиента.
- Вручную вводятся только `GMAIL_CLIENT_ID` и `GMAIL_CLIENT_SECRET`.
- `GMAIL_SEND_TO`, автоотправка, лимит вложения, host и port зафиксированы в Docker Compose.
- Callback/health сервер запускается даже до ввода Google credentials, чтобы адрес можно было узнать заранее.
