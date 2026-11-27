"""Run explicitly: pytest -m live   (needs MODE-independent real IG_ACCESS_TOKEN in .env)"""
import pytest

from flashly.config import Settings
from flashly.service import build_service

pytestmark = pytest.mark.live


@pytest.fixture
async def live():
    s = Settings(mode="live")
    s.register_secrets()
    svc = build_service(s)
    yield svc
    await svc.client.aclose()


async def test_profile(live):
    assert (await live.get_profile()).username == "flashly_official"


async def test_token_valid(live):
    assert (await live.get_token_status()).is_valid


async def test_reel_insights(live):
    reels = (await live.list_media(limit=10, media_type="REELS")).items
    assert reels, "no reels found"
    r = await live.get_media_insights(reels[0].id)
    assert r.metrics["views"].available
