import httpx
import pytest

from flashly.errors import TokenError
from tests.conftest import err_body, make_service


async def test_reel_insights_all_metrics_and_units(svc):
    r = await svc.get_media_insights("18000000000000001")
    assert all(m.available for m in r.metrics.values())
    assert r.metrics["ig_reels_avg_watch_time"].value == 5.4
    assert r.metrics["ig_reels_avg_watch_time"].raw_value == 5400
    assert r.metrics["ig_reels_video_view_total_time"].value == 99360.0
    assert any("retention" in n for n in r.notes) and any("milliseconds" in n for n in r.notes)


async def test_image_reels_metrics_null_with_reason_and_not_requested(settings, fast_sleep):
    asked = []

    def h(req):
        asked.append(req.url.params.get("metric"))
        if req.url.path.endswith("/insights"):
            ms = req.url.params["metric"].split(",")
            return httpx.Response(200, json={"data": [{"name": m, "values": [{"value": 1}]} for m in ms]})
        return httpx.Response(200, json={"id": "9", "media_type": "IMAGE", "media_product_type": "FEED"})
    s = make_service(h, settings, fast_sleep)
    r = await s.get_media_insights("9")
    assert r.metrics["ig_reels_avg_watch_time"].value is None
    assert "reels-only" in r.metrics["ig_reels_avg_watch_time"].reason
    assert not any("ig_reels" in (a or "") for a in asked)


async def test_unsupported_metric_isolated_via_fallback(svc):
    r = await svc.get_media_insights("18000000000000002")
    assert r.metrics["views"].value == 900
    sh = r.metrics["shares"]
    assert sh.value is None and not sh.available and "not supported" in sh.reason


async def test_empty_value_is_null_not_zero(svc):
    r = await svc.get_media_insights("18000000000000004")
    assert r.metrics["shares"].value is None and r.metrics["shares"].reason
    assert r.metrics["saved"].value == 0 and r.metrics["saved"].available


async def test_unknown_metric_rejected(svc):
    with pytest.raises(ValueError):
        await svc.get_media_insights("18000000000000001", ["impressions"])


async def test_token_error_propagates_not_swallowed(settings, fast_sleep):
    h = lambda req: httpx.Response(400, json=err_body(190, "expired", 463))  # noqa: E731
    with pytest.raises(TokenError):
        await make_service(h, settings, fast_sleep).get_media_insights("1")


async def test_media_details(svc):
    d = await svc.get_media_details("18000000000000001")
    assert d.media_url.endswith(".mp4") and d.thumbnail_url and d.like_count == 1520
    assert d.comments_count == 48 and any("temporary" in n for n in d.notes)
    img = await svc.get_media_details("18000000000000002")
    assert img.like_count is None and img.like_count_reason
    car = await svc.get_media_details("18000000000000003")
    assert len(car.children) == 2


async def test_profile(svc):
    p = await svc.get_profile()
    assert p.username == "flashly_official" and p.followers_count == 1234


async def test_account_insights_total_and_partial(svc):
    r = await svc.get_account_insights(["reach", "likes"])
    assert r.metrics["reach"].value == 4200
    assert r.metrics["likes"].value is None and r.metrics["likes"].reason


async def test_account_insights_series_and_dates(svc):
    r = await svc.get_account_insights(["follower_count"], since="2026-11-20", until="2026-11-26",
                                       metric_type="time_series")
    assert len(r.metrics["follower_count"].series) == 2


async def test_token_status_no_token_in_output(svc, settings):
    st = await svc.get_token_status()
    dump = st.model_dump_json()
    assert st.is_valid and 44 < st.days_left < 46 and st.warning is None
    assert "EAATEST" not in dump and "s3cr3t" not in dump


async def test_token_status_warns_when_close_to_expiry(settings, fast_sleep):
    import time
    exp = int(time.time() + 5 * 86400)
    h = lambda req: httpx.Response(200, json={"data": {"is_valid": True, "expires_at": exp, "scopes": []}})  # noqa: E731
    st = await make_service(h, settings, fast_sleep).get_token_status()
    assert st.warning and "refresh" in st.warning


async def test_token_status_invalid_token(settings, fast_sleep):
    h = lambda req: httpx.Response(200, json={"data": {"is_valid": False, "expires_at": 0}})  # noqa: E731
    st = await make_service(h, settings, fast_sleep).get_token_status()
    assert st.is_valid is False and st.warning
