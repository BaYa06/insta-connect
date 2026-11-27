import pytest
from sqlalchemy import create_engine, func, select

from flashly.collector import collect_once
from flashly.config import Settings
from flashly.db import Base, Media, MediaMetricSnapshot, TokenEvent, make_session_factory
from flashly.service import build_service


@pytest.fixture
def Session():
    e = create_engine("sqlite://")
    Base.metadata.create_all(e)
    return make_session_factory(e)


@pytest.fixture
def cfg():
    return Settings(mode="mock", _env_file=None)


async def test_collect_stores_snapshots(Session, cfg):
    svc = build_service(cfg)
    stats = await collect_once(svc, Session, cfg)
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Media)) == 4
        # ms stored exactly as returned by the API, unit label kept
        row = s.scalar(select(MediaMetricSnapshot).where(
            MediaMetricSnapshot.media_id == "18000000000000001",
            MediaMetricSnapshot.metric == "ig_reels_avg_watch_time"))
        assert float(row.value) == 5400 and row.raw_unit == "milliseconds"
        # missing metric -> NULL value + reason, never a placeholder
        miss = s.scalar(select(MediaMetricSnapshot).where(
            MediaMetricSnapshot.media_id == "18000000000000002", MediaMetricSnapshot.metric == "shares"))
        assert miss.value is None and miss.reason
        assert s.scalar(select(func.count()).select_from(TokenEvent)) == 1
    assert stats["media"] == 4 and stats["null_snapshots"] > 0
    await svc.client.aclose()


async def test_snapshots_are_appended_not_overwritten(Session, cfg):
    svc = build_service(cfg)
    await collect_once(svc, Session, cfg)
    with Session() as s:
        first = s.scalar(select(func.count()).select_from(MediaMetricSnapshot))
    await collect_once(svc, Session, cfg)
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(MediaMetricSnapshot)) == 2 * first
        assert s.scalar(select(func.count()).select_from(Media)) == 4  # media upserted, not duplicated
        assert s.scalar(select(func.count()).select_from(TokenEvent)) == 2
    await svc.client.aclose()


def test_token_event_has_no_token_column():
    assert not {"token", "access_token"} & set(TokenEvent.__table__.columns.keys())
