import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from flashly.server import create_server
from tests.conftest import FAKE_TOKEN, err_body, make_service

EXPECTED = {"get_profile", "list_media", "get_media_details", "get_media_insights",
            "get_account_insights", "get_audience_insights", "get_token_status"}


async def test_tools_registered_and_read_only(svc):
    tools = await create_server(svc).list_tools()
    assert {t.name for t in tools} == EXPECTED
    for t in tools:
        assert t.annotations.readOnlyHint is True and t.annotations.destructiveHint is False
        assert not any(w in t.name for w in ("delete", "publish", "post", "comment_", "create", "update"))


async def test_call_tool_returns_json(svc):
    res = await create_server(svc).call_tool("get_media_insights", {"media_id": "18000000000000001"})
    payload = json.loads(res[0].text)
    assert payload["metrics"]["ig_reels_avg_watch_time"]["unit"] == "seconds"


async def test_tool_error_is_masked(settings, fast_sleep):
    h = lambda req: httpx.Response(400, json=err_body(190, f"Invalid access_token={FAKE_TOKEN}", 467))  # noqa: E731
    srv = create_server(make_service(h, settings, fast_sleep))
    with pytest.raises(Exception) as ei:
        await srv.call_tool("get_profile", {})
    assert FAKE_TOKEN not in str(ei.value) and "token" in str(ei.value).lower()


async def test_bad_args_become_tool_error(svc):
    with pytest.raises(Exception):
        await create_server(svc).call_tool("get_media_insights", {"media_id": "1", "metrics": ["bogus"]})


def test_client_exposes_only_get():
    from flashly.graph_client import GraphClient
    public = {n for n in dir(GraphClient) if not n.startswith("_")}
    assert not public & {"post", "delete", "put", "patch"}
