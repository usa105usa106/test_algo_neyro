# Gmail 72 — email-only `.jpg` suffix for ZIP attachments

## Changed

- Gmail attachment name is now `<original>.zip.jpg`.
- Gmail attachment MIME type is `image/jpeg` so the ChatGPT Gmail connector can download it.
- Attachment bytes remain the exact validated ZIP; no recompression or mutation occurs.
- Telegram delivery is unchanged and still uses the original `.zip` filename.
- Added `X-ChatGPT-Archive-Email-Name` metadata and recovery instructions in the email body.
- Updated app version to `72_full_GMAIL_EMAIL_ZIP_JPG_ATTACHMENT`.

## Unchanged

- Scan modes, Intraday, A+ Hunter, stress tests, task scheduling, archive generation, Gmail OAuth, port 80 route, and healthcheck behavior.
