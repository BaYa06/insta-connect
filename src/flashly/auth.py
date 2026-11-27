"""Bearer-key protection for the MCP endpoint (pure ASGI middleware).

Accepted credentials (either one):
  1. `Authorization: Bearer <key>`            (preferred)
  2. URL path prefix `/k/<key>/mcp`           (fallback for connector UIs that only take a URL)

Everything else gets 401. `/healthz` is open and returns no data.
The key is never logged; failures log only the client address and path *without* the key.
"""
from __future__ import annotations

import hmac
import json
import logging

log = logging.getLogger("flashly.auth")
MIN_KEY_LENGTH = 32
PATH_PREFIX = "/k/"


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


async def _send_json(send, status: int, body: dict, extra: list[tuple[bytes, bytes]] | None = None):
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode()), *(extra or [])]})
    await send({"type": "http.response.body", "body": payload})


class BearerAuthMiddleware:
    def __init__(self, app, key: str, *, allow_path_key: bool = True) -> None:
        if len(key) < MIN_KEY_LENGTH:
            raise ValueError(f"MCP_AUTH_TOKEN must be at least {MIN_KEY_LENGTH} characters")
        self.app, self.key, self.allow_path_key = app, key, allow_path_key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "lifespan":
                return await self.app(scope, receive, send)
            return  # websockets etc. are not part of this server
        path: str = scope["path"]
        if path == "/healthz":
            return await _send_json(send, 200, {"ok": True})

        headers = {k.lower(): v for k, v in scope["headers"]}
        authorized = False
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if auth[:7].lower() == "bearer ":
            authorized = _eq(auth[7:].strip(), self.key)
        elif self.allow_path_key and path.startswith(PATH_PREFIX):
            _, _, supplied, *rest = path.split("/", 3)
            if _eq(supplied, self.key):
                authorized = True
                new_path = "/" + (rest[0] if rest else "")
                scope = {**scope, "path": new_path, "raw_path": new_path.encode()}

        if not authorized:
            client = (scope.get("client") or ("?",))[0]
            shown = path if not path.startswith(PATH_PREFIX) else PATH_PREFIX + "***"
            log.warning("401 from %s for %s", client, shown)
            return await _send_json(send, 401, {"error": "unauthorized",
                                                "message": "Missing or invalid bearer key."},
                                    [(b"www-authenticate", b'Bearer realm="flashly-mcp"')])
        await self.app(scope, receive, send)
