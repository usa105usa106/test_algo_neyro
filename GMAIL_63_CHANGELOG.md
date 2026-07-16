# Gmail v63 — direct 8080 route and fixed VPS storage

- Removed the extra nginx gateway introduced in v62.
- Coolify magic URL is now declared on the bot service as `SERVICE_URL_GMAIL-AUTH_8080`.
- The generated public domain is routed directly to the bot callback server on container port 8080.
- `/healthz` and `/gmail/callback` are served by the same process.
- Gmail credentials, OAuth token, encryption key, and deduplication ledger are stored in the fixed VPS directory `/data/chatgpt-scan-bot-storage` mounted at `/app/storage`.
- Removed the changing magic encryption key. The encryption key is generated once inside persistent storage.
- Removed guessed legacy-volume migration and the v62 gateway dependency.
- Trading modes, task files, archive creation, and Intraday logic are unchanged.
