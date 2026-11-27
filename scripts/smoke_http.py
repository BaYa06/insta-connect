#!/usr/bin/env python
"""Smoke-test a running MCP endpoint end to end (as a connector would).

  python scripts/smoke_http.py http://127.0.0.1:8000
  python scripts/smoke_http.py https://xxxx.trycloudflare.com

Reads MCP_AUTH_TOKEN from .env. Checks: 401 without key, 401 with wrong key,
MCP handshake + tool list + get_profile with the key. Never prints the key.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import httpx  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from flashly.config import get_settings  # noqa: E402
from flashly.masking import mask  # noqa: E402


async def main(base: str) -> int:
    key = get_settings().mcp_auth_token.get_secret_value()
    base = base.rstrip("/")
    ok = True
    async with httpx.AsyncClient(timeout=20) as h:
        for label, hdr in (("без ключа", {}), ("неверный ключ", {"Authorization": "Bearer wrong"})):
            r = await h.post(f"{base}/mcp", headers=hdr, json={})
            good = r.status_code == 401
            ok &= good
            print(f"[{'OK' if good else 'FAIL'}] {label}: HTTP {r.status_code} (ожидается 401)")
        r = await h.get(f"{base}/health")
        print(f"[{'OK' if r.status_code == 200 else 'FAIL'}] /health: HTTP {r.status_code}")
    async with streamablehttp_client(f"{base}/mcp", headers={"Authorization": f"Bearer {key}"}) as (rd, wr, _):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print(f"[OK] с ключом: {len(tools)} tools: {', '.join(tools)}")
            res = await s.call_tool("get_profile", {})
            print(f"[{'FAIL' if res.isError else 'OK'}] get_profile: {mask(res.content[0].text)[:120].replace(chr(10), ' ')}")
            ok &= not res.isError
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")))
