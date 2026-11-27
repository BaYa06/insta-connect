import httpx

from tests.conftest import FAKE_TOKEN, client_with, make_service


async def test_paginate_uses_cursor_not_next_url(settings, fast_sleep):
    seen = []

    def h(req):
        seen.append(dict(req.url.params))
        assert req.url.host == "graph.facebook.com" and "EAAFAKELEAK" not in str(req.url)
        if "after" not in req.url.params:
            return httpx.Response(200, json={"data": [{"id": "a"}], "paging": {
                "cursors": {"after": "C1"}, "next": "https://x/?access_token=EAAFAKELEAKED&after=C1"}})
        return httpx.Response(200, json={"data": [{"id": "b"}], "paging": {"cursors": {"after": "C2"}}})
    c = client_with(h, settings, fast_sleep)
    pages = [p async for p in c.paginate("1/media", {"fields": "id"})]
    assert [p.data for p in pages] == [[{"id": "a"}], [{"id": "b"}]]
    assert pages[0].next_cursor == "C1" and pages[1].next_cursor is None
    assert seen[1]["after"] == "C1"


async def test_max_pages_respected(settings, fast_sleep):
    h = lambda req: httpx.Response(200, json={"data": [{"id": "x"}], "paging": {"cursors": {"after": "C"}, "next": "n"}})  # noqa: E731
    c = client_with(h, settings, fast_sleep)
    assert len([p async for p in c.paginate("m", max_pages=3)]) == 3


async def test_service_list_media_pagination_on_fixtures(svc):
    first = await svc.list_media(limit=3, max_pages=1)
    assert [i.id for i in first.items] == ["18000000000000001", "18000000000000002", "18000000000000003"]
    assert first.has_more and first.next_cursor
    second = await svc.list_media(limit=3, cursor=first.next_cursor, max_pages=1)
    assert [i.id for i in second.items] == ["18000000000000004"]
    assert not second.has_more and second.next_cursor is None


async def test_list_media_follows_pages_until_limit(svc):
    r = await svc.list_media(limit=4)
    assert len(r.items) == 4 and r.pages_fetched == 2 and not r.has_more


async def test_filter_by_type(svc):
    reels = await svc.list_media(limit=10, media_type="REELS")
    assert {i.id for i in reels.items} == {"18000000000000001", "18000000000000004"}
    imgs = await svc.list_media(limit=10, media_type="IMAGE")
    assert [i.id for i in imgs.items] == ["18000000000000002"]
    car = await svc.list_media(limit=10, media_type="CAROUSEL_ALBUM")
    assert [i.id for i in car.items] == ["18000000000000003"]


async def test_filter_by_date_range_and_early_stop(svc):
    r = await svc.list_media(limit=10, since="2026-11-01")
    assert [i.id for i in r.items] == ["18000000000000001", "18000000000000002"]
    assert not r.has_more  # reached older-than-since, so nothing more can match
    r = await svc.list_media(limit=10, until="2026-10-31")
    assert [i.id for i in r.items] == ["18000000000000003", "18000000000000004"]


async def test_invalid_media_type(svc):
    import pytest
    with pytest.raises(ValueError):
        await svc.list_media(media_type="POST")
