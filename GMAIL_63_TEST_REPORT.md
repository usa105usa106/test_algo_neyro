# Gmail v63 test report

Result: **25/25 tests passed**.

Verified:

- Coolify magic route is declared directly on the bot service as `SERVICE_URL_GMAIL-AUTH_8080`.
- Public callback is normalized to HTTPS and does not expose internal port 8080 in the Google redirect URI.
- `/healthz` and `/gmail/callback` are served by the bot process on port 8080.
- Fixed VPS bind directory `/data/chatgpt-scan-bot-storage` is mounted at `/app/storage`.
- Client ID and Client Secret remain readable after SecretStore is recreated against the same storage directory.
- OAuth token and Gmail deduplication ledger use the same persistent storage.
- Duplicate, parallel, changed-file, wrong-name, timeout, 401, 4xx and 5xx Gmail paths remain covered.
- Gmail sending happens only after the exact ZIP has been delivered to Telegram.
- `archive_builder.py`, `intraday_archive.py`, `intraday_engine.py`, `mexc.py`, `charts.py`, `file_utils.py`, `logging_setup.py`, and `run.py` are unchanged from v62.

Not possible to prove locally: the external Coolify proxy route on the user's VPS. This must be confirmed after deployment by opening the generated `/healthz` URL. The compose syntax now follows Coolify's documented `_PORT` magic variable pattern rather than the broken v62 gateway arrangement.
