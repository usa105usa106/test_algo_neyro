# Gmail v70 test report

## Evidence from the deployed v69 `/log_mail`

- Runtime callback listener: `0.0.0.0:80`.
- v69 Dockerfile proxy target: `EXPOSE 8080`.
- No gateway access/error log files were mounted or written.
- Result: Coolify/Traefik had no working backend for the generated HTTPS URL.

## v70 checks

- Python compile: successful.
- Pytest: 37 passed.
- Docker Compose YAML: parsed successfully.
- Compose services: one service (`chatgpt-scan-bot`).
- Compose exposed port: `80`.
- Runtime environment port: `GMAIL_OAUTH_LISTEN_PORT=80`.
- Dockerfile: `EXPOSE 80`, no `EXPOSE 8080`.
- Separate nginx gateway: removed.
- Local integration request to the Python callback server:
  - `GET /healthz` -> HTTP 200
  - body contains `{"ok": true, "service": "gmail-oauth-callback"}`.
- Scanner/task runtime hashes verified byte-identical to v64 for:
  `archive_builder.py`, `charts.py`, `file_utils.py`, `intraday_archive.py`,
  `intraday_engine.py`, `logging_setup.py`, `mexc.py`, and `run.py`.

## Not locally verifiable

The external TLS certificate and Traefik labels on the user's VPS can only be checked
after Coolify performs the redeploy. The application-side port mismatch found in v69
is removed in v70.
