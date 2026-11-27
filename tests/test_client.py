import httpx
import pytest

from flashly.errors import (GraphPermissionError, InvalidRequestError, NetworkError, RateLimitError,
                            TokenError, TransientError)
from flashly.graph_client import parse_app_usage
from tests.conftest import FAKE_TOKEN, client_with, err_body


async def test_success_sends_token_as_param(settings, fast_sleep):
    seen = {}

    def h(req):
        seen["q"] = dict(req.url.params)
        return httpx.Response(200, json={"id": "1"})
    c = client_with(h, settings.model_copy(update={"mode": "live"}), fast_sleep)
    assert await c.get("1", {"fields": "id"}) == {"id": "1"}
    assert seen["q"]["access_token"] == FAKE_TOKEN and seen["q"]["fields"] == "id"


@pytest.mark.parametrize("sub,word", [(463, "expired"), (467, "invalid"), (460, "password")])
async def test_code_190_token_errors_no_retry(settings, fast_sleep, sleeps, sub, word):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(400, json=err_body(190, "Error validating access token", sub))
    with pytest.raises(TokenError) as ei:
        await client_with(h, settings, fast_sleep).get("x")
    assert len(calls) == 1 and sleeps == [] and word in str(ei.value) and ei.value.subcode == sub


@pytest.mark.parametrize("code", [4, 17, 32, 613])
async def test_rate_limit_codes_retry_then_raise(settings, fast_sleep, sleeps, code):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(400, json=err_body(code, "limit"))
    with pytest.raises(RateLimitError):
        await client_with(h, settings, fast_sleep).get("x")
    assert len(calls) == 1 + settings.max_retries
    assert len(sleeps) == settings.max_retries


async def test_exponential_backoff_growth(settings, fast_sleep, sleeps):
    h = lambda req: httpx.Response(400, json=err_body(17))  # noqa: E731
    with pytest.raises(RateLimitError):
        await client_with(h, settings, fast_sleep).get("x")
    assert 1 <= sleeps[0] < 1.3 and 2 <= sleeps[1] < 2.6 and 4 <= sleeps[2] < 5.2


async def test_retry_recovers(settings, fast_sleep):
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return httpx.Response(500, json=err_body(2, "temp")) if n["i"] < 3 else httpx.Response(200, json={"ok": 1})
    assert await client_with(h, settings, fast_sleep).get("x") == {"ok": 1}
    assert n["i"] == 3


async def test_retry_after_header_respected_and_capped(settings, fast_sleep, sleeps):
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, json=err_body(4), headers={"retry-after": "9"})
        return httpx.Response(200, json={})
    await client_with(h, settings, fast_sleep).get("x")
    assert sleeps == [9.0]


async def test_permission_and_invalid_request_not_retried(settings, fast_sleep, sleeps):
    for code, exc in ((10, GraphPermissionError), (200, GraphPermissionError), (100, InvalidRequestError)):
        h = lambda req, c=code: httpx.Response(400, json=err_body(c))  # noqa: E731
        with pytest.raises(exc):
            await client_with(h, settings, fast_sleep).get("x")
    assert sleeps == []


async def test_5xx_without_body_is_transient(settings, fast_sleep):
    h = lambda req: httpx.Response(503, text="<html>down</html>")  # noqa: E731
    with pytest.raises(TransientError):
        await client_with(h, settings, fast_sleep).get("x")


async def test_network_error_does_not_leak_token(settings, fast_sleep):
    def h(req):
        raise httpx.ConnectError(f"cannot connect {req.url}")
    with pytest.raises(NetworkError) as ei:
        await client_with(h, settings, fast_sleep).get("x")
    assert FAKE_TOKEN not in str(ei.value)
    assert ei.value.__suppress_context__ and ei.value.__cause__ is None


async def test_error_inside_200_body(settings, fast_sleep):
    h = lambda req: httpx.Response(200, json=err_body(190, "x", 463))  # noqa: E731
    with pytest.raises(TokenError):
        await client_with(h, settings, fast_sleep).get("x")


def test_app_usage_parsing():
    u = parse_app_usage({"x-app-usage": '{"call_count": 91, "total_time": 12, "total_cputime": 40}'})
    assert u.call_count == 91 and u.max_percent == 91
    b = parse_app_usage({"x-business-use-case-usage": '{"17841": [{"call_count": 55, "total_time": 3, "total_cputime": 2, "type": "instagram"}]}'})
    assert b.call_count == 55
    assert parse_app_usage({"x-app-usage": "garbage"}) is None
    assert parse_app_usage({}) is None


async def test_high_usage_logged_and_stored(settings, fast_sleep, caplog):
    h = lambda req: httpx.Response(200, json={}, headers={"x-app-usage": '{"call_count": 95}'})  # noqa: E731
    c = client_with(h, settings, fast_sleep)
    with caplog.at_level("WARNING"):
        await c.get("x")
    assert c.last_app_usage.call_count == 95 and "usage high" in caplog.text
