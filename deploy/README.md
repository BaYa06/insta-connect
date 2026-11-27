# Deploy on Ubuntu 24.04 (Docker Compose)

Stack: `caddy` (80/443, automatic HTTPS) -> `mcp-server`; `collector` -> `postgres`.
Only Caddy publishes ports; mcp-server and postgres are reachable only inside the compose network.

Prerequisites on the server: Docker + Compose v2, git, DNS name pointing to the VM, ports 22/80/443 open.

## 1. First start
```bash
git clone https://github.com/BaYa06/insta-connect.git ~/flashly   # private repo: use a deploy key or a PAT
cd ~/flashly
cp .env.example .env
nano .env        # fill: IG_ACCESS_TOKEN, META_APP_SECRET, MCP_API_KEY, POSTGRES_PASSWORD, PUBLIC_HOST
chmod 600 .env
```
Generate values on the server (do not paste secrets into chats):
```bash
openssl rand -base64 36 | tr -d '\n=+/'     # MCP_API_KEY
openssl rand -hex 24                         # POSTGRES_PASSWORD
```
```bash
docker compose up -d --build
docker compose ps                            # all services Up / healthy
```
Caddy obtains the TLS certificate on the first request (needs ports 80 and 443 open and DNS correct).

## 2. Verify
```bash
curl -s https://$PUBLIC_HOST/health                       # {"ok": true}
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://$PUBLIC_HOST/mcp   # 401 without a key
docker compose logs --tail 30 collector                   # "Collection done: {...}"
```
(`PUBLIC_HOST` is in `.env`: `export $(grep ^PUBLIC_HOST .env)` or type the host.)
From your laptop: `python scripts/smoke_http.py https://<host>` (needs the same key in your local `.env`).

## 3. Logs, update, restart
```bash
docker compose logs -f mcp-server collector     # logs are masked; the key/token are never printed
cd ~/flashly && git pull && docker compose up -d --build
docker compose restart collector
```
Migrations run automatically when the collector starts (`alembic upgrade head`).

## 4. Backups
```bash
./deploy/backup.sh                              # one-off test
crontab -e   # add:
15 3 * * * cd /home/baya/flashly && ./deploy/backup.sh >> backups/backup.log 2>&1
```
Dumps go to `backups/` (gzip, last 14 days kept; `KEEP_DAYS=30` to change). Copy them off the VM
periodically (`scp`, Azure Blob): a backup on the same disk does not survive losing the VM.

**Restore** (this replaces current data):
```bash
docker compose stop collector
docker compose exec -T postgres psql -U flashly -d postgres -c "DROP DATABASE flashly" -c "CREATE DATABASE flashly OWNER flashly"
gunzip -c backups/flashly_YYYY-MM-DD_HHMM.sql.gz | docker compose exec -T postgres psql -U flashly -d flashly
docker compose start collector
```

## 5. Add the connector
**Connector address** (the key goes in the path; the Claude custom connector cannot send headers):

```
https://flashly-mcp-bk.denmarkeast.cloudapp.azure.com/mcp/<MCP_API_KEY>
```
- **Claude:** Settings -> Connectors -> Add custom connector -> paste the URL above with your real key.
  Get the key on the server only: `grep ^MCP_API_KEY ~/flashly/.env`.
- **ChatGPT / clients that can send headers:** `https://<PUBLIC_HOST>/mcp` plus `Authorization: Bearer <MCP_API_KEY>`.
- No key, a wrong key, or `/mcp/` alone returns `401`. OAuth discovery endpoints
  (`/.well-known/oauth-protected-resource`) are intentionally not implemented.

Check from any machine (replace the placeholder):
```bash
H=https://flashly-mcp-bk.denmarkeast.cloudapp.azure.com
curl -s -o /dev/null -w "%{http_code}\n" -X POST $H/mcp                 # 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST $H/mcp/wrong-key       # 401
curl -s $H/health                                                         # {"ok": true}
```

**Verify the key is not in Caddy logs** (do this once after the first real request with the key):
```bash
docker compose run --rm caddy caddy validate --config /etc/caddy/Caddyfile   # syntax check
docker compose logs caddy | grep -c "$(grep ^MCP_API_KEY .env | cut -d= -f2)"   # must print 0
docker compose logs caddy | grep -o '"uri":"[^"]*"' | tail -3                    # expect /mcp/***
```
If the count is not 0, stop exposing the endpoint, rotate `MCP_API_KEY` and report it: the Caddyfile
log filter was written from the docs and has not been run by me.

## 6. Security notes
- `.env` has mode 600 and stays on the server. The Instagram token lasts ~60 days: check `get_token_status`
  or `docker compose logs collector | grep TOKEN`; refresh before expiry.
- Caddy logs (access and error) rewrite `/mcp/<key>` to `/mcp/***`; Authorization headers are redacted by Caddy.
- Rotate the key: edit `MCP_API_KEY`, `docker compose up -d`, update the connector.
