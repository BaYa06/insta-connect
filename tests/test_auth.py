import httpx
import pytest

from flashly.auth import BearerAuthMiddleware

KEY = "k" * 40


async def dummy(scope, receive, send):
    if scope["type"] == "lifespan":
        return
    body = scope["path"].encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


@pytest.fixture
def client():
    app = BearerAuthMiddleware(dummy, KEY)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_no_key_401(client):
    r = await client.post("/mcp")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize("hdr", ["Bearer wrong" + "x" * 30, "Basic " + KEY, "Bearer ", KEY, "bearer"])
async def test_bad_header_401(client, hdr):
    assert (await client.post("/mcp", headers={"Authorization": hdr})).status_code == 401


async def test_valid_bearer_200(client):
    r = await client.post("/mcp", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200 and r.text == "/mcp"


async def test_bearer_scheme_case_insensitive(client):
    assert (await client.get("/mcp", headers={"Authorization": f"bearer {KEY}"})).status_code == 200


async def test_path_key_fallback_strips_prefix(client):
    r = await client.post(f"/k/{KEY}/mcp")
    assert r.status_code == 200 and r.text == "/mcp"
    assert (await client.post("/k/wrong/mcp")).status_code == 401


async def test_path_key_can_be_disabled():
    app = BearerAuthMiddleware(dummy, KEY, allow_path_key=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    assert (await c.post(f"/k/{KEY}/mcp")).status_code == 401


@pytest.mark.parametrize("path", ["/health", "/healthz"])
async def test_health_open_and_data_free(client, path):
    r = await client.get(path)
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_401_log_hides_path_key(client, caplog):
    with caplog.at_level("WARNING"):
        await client.post("/k/SUPERSECRETGUESS/mcp")
    assert "SUPERSECRETGUESS" not in caplog.text and "/k/***" in caplog.text


def test_short_key_rejected():
    with pytest.raises(ValueError):
        BearerAuthMiddleware(dummy, "short")
