# Flashly Growth AI

Read-only MCP server for Instagram analytics (@flashly_official) via the official Graph API.
Status: stages 1-4 (core, tools, live verification, auth + access).

## Setup
```bash
uv sync --python 3.12
cp .env.example .env     # fill IG_ACCESS_TOKEN (and META_APP_SECRET) yourself; never paste secrets into chats
```
`.env` keys accepted: `INSTAGRAM_ACCESS_TOKEN`/`IG_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`/`IG_USER_ID`,
`INSTAGRAM_MODE`/`MODE` (`mock` | `live`), `META_APP_SECRET`/`APP_SECRET`, `MCP_AUTH_TOKEN`.

## Tests and verification
```bash
.venv/bin/python -m pytest                          # unit tests on mock data, no network
.venv/bin/python scripts/verify_live.py             # every endpoint/metric on the real API (read-only)
.venv/bin/python -m pytest -m live                  # live integration tests, only when asked
```

## Tools (all read-only)
`get_profile`, `list_media`, `get_media_details`, `get_media_insights`, `get_account_insights`,
`get_audience_insights`, `get_token_status`.

## Authentication
The endpoint refuses to start without `MCP_AUTH_TOKEN` (>= 32 chars) in `.env`.
Generate one: `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`.

Two ways to present the key:
1. **Header (preferred):** `Authorization: Bearer <key>`.
2. **URL path (fallback):** `https://HOST/k/<key>/mcp`, for connector UIs that only accept a URL.
   Weaker: URLs end up in browser history, proxies and tunnel logs. Server logs and uvicorn access
   logs hide it. Disable with `--no-path-key`. Rotate the key if the URL leaks.

Without a valid key: `401` + `WWW-Authenticate: Bearer`. `GET /healthz` is open and returns only `{"ok": true}`.
Rotate: change `MCP_AUTH_TOKEN` in `.env`, restart, update the connector.

## Run locally
```bash
scripts/run_local.sh                   # http://127.0.0.1:8000/mcp
.venv/bin/python scripts/smoke_http.py http://127.0.0.1:8000   # 401 checks + handshake + get_profile
```

## Public HTTPS URL with cloudflared
```bash
brew install cloudflared               # once
scripts/tunnel.sh                      # prints https://<random>.trycloudflare.com
.venv/bin/python scripts/smoke_http.py https://<random>.trycloudflare.com
```
Quick-tunnel URLs change on every start and anyone who knows the URL can reach the endpoint,
which is why the key is mandatory. For a stable URL use a named tunnel with your own domain
(`cloudflared tunnel login`, `tunnel create`, `tunnel route dns`), or the Caddy deployment in stage 8.
The laptop must stay awake and both processes (server and tunnel) running.

## Add the connector (check the current UI; Anthropic and OpenAI change it often)
**Claude** (claude.ai): Settings -> Connectors -> Add custom connector.
Name: `Flashly Instagram`. URL: the fallback form `https://<host>/k/<key>/mcp`. As far as I know the
dialog only offers a URL and optional OAuth fields, not a custom header. If your version has an
API-key/header field, use the plain `https://<host>/mcp` URL with the Bearer header instead.
Then enable the connector in a chat (the "+" / tools menu).

**ChatGPT:** Settings -> Connectors -> Advanced -> enable Developer mode (availability depends on plan),
then create a connector: MCP server URL as above. If it offers "No authentication"/OAuth only, use the
`/k/<key>/` URL. Workspace admins may need to allow custom connectors.

If a client cannot connect, run `scripts/smoke_http.py` against the same URL first: it separates
server/tunnel problems from client-UI problems.

## Data rules
- Watch-time metrics come in **milliseconds**; output has `value` in seconds plus `raw_value` (ms).
- `ig_reels_avg_watch_time` is an average, **not** a retention curve (the API has none).
- Missing data is `null` with a `reason`; nothing is estimated.
- Only GET requests exist in the client; all tools are annotated read-only.
- Audience metrics are typically empty under 100 followers; `timeframe` accepts only `this_week`/`this_month`.
