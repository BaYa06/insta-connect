import pytest
from flashly.server import create_server


async def test_demographics_country(svc):
    r = await svc.get_audience_insights("follower_demographics", "country")
    assert r.available and {"dimension": "RU", "value": 9} in r.results


async def test_engaged_requires_timeframe(svc):
    with pytest.raises(ValueError):
        await svc.get_audience_insights("engaged_audience_demographics", "age")
    r = await svc.get_audience_insights("engaged_audience_demographics", "age", "this_month")
    assert r.available and r.timeframe == "this_month"


async def test_breakdown_required_and_timeframe_rejected(svc):
    with pytest.raises(ValueError):
        await svc.get_audience_insights("follower_demographics")
    with pytest.raises(ValueError):
        await svc.get_audience_insights("follower_demographics", "age", "this_month")
    with pytest.raises(ValueError):
        await svc.get_audience_insights("online_followers", "age")


async def test_online_followers_hourly_without_time_series(svc):
    r = await svc.get_audience_insights("online_followers")
    assert r.available and len(r.hourly) == 24 and r.hourly["10"] == 30


async def test_empty_data_is_null_with_reason(settings, fast_sleep):
    import httpx
    from tests.conftest import make_service
    h = lambda req: httpx.Response(200, json={"data": []})  # noqa: E731
    s = make_service(h, settings, fast_sleep)
    for m, b in (("follower_demographics", "country"), ("online_followers", None)):
        r = await s.get_audience_insights(m, b)
        assert not r.available and r.results is None and r.hourly is None
        assert "empty" in r.reason and "100" in r.notes[0]


async def test_api_error_becomes_reason(settings, fast_sleep):
    import httpx
    from tests.conftest import err_body, make_service
    h = lambda req: httpx.Response(400, json=err_body(100, "(#100) nope"))  # noqa: E731
    r = await make_service(h, settings, fast_sleep).get_audience_insights("online_followers")
    assert not r.available and "nope" in r.reason


async def test_tool_registered_read_only(svc):
    tools = {t.name: t for t in await create_server(svc).list_tools()}
    assert tools["get_audience_insights"].annotations.readOnlyHint is True
