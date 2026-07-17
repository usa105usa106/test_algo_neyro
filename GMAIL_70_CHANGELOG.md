# Gmail v70 — direct port 80 route fix

## Root cause proven by `/log_mail`

The deployed v69 process reported `listen_port: 80`, while the v69 Dockerfile exposed
port `8080`. The separate gateway also produced no logs, showing that the deployment
was running as a single Dockerfile application rather than the intended two-service
Compose stack. Coolify/Traefik therefore targeted a different port/service than the
actual Gmail callback server and returned `no available server`.

## Changes

- Restored a single-container deployment compatible with both Coolify Dockerfile and
  Docker Compose build packs.
- `Dockerfile` now exposes port `80`, matching the runtime callback listener.
- Compose now routes the generated Coolify URL directly to the bot service on port 80.
- Removed the separate nginx gateway and its healthcheck.
- Added `HEALTHCHECK NONE` so an inherited image healthcheck cannot suppress routing.
- Retained `/log_mail` diagnostics and secret redaction.
- No scanner, trading mode, Intraday, Stress Test, chart, MEXC, archive, or task logic
  was changed.
