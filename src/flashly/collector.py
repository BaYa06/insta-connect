"""Scheduled collector: every COLLECT_INTERVAL_SECONDS store a new snapshot of each post's
metrics plus a token health check. Read-only against Instagram; append-only in Postgres.

  python -m flashly.collector          # loop forever
  python -m flashly.collector --once   # single run (cron / manual)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from .config import Settings, get_settings
from .db import Media, MediaMetricSnapshot, TokenEvent, make_engine, make_session_factory
from .errors import GraphAPIError, TokenError
from .masking import mask, setup_logging
from .service import InstagramService, build_service, parse_ts

log = logging.getLogger("flashly.collector")


async def check_token(svc: InstagramService, Session: sessionmaker, warn_days: list[int]) -> None:
    try:
        st = await svc.get_token_status()
    except GraphAPIError as e:
        log.error("Token check failed: %s", e)
        with Session.begin() as s:
            s.add(TokenEvent(event_type="check_failed", details={"error": e.to_dict()}))
        return
    with Session.begin() as s:
        s.add(TokenEvent(
            event_type="status_check", is_valid=st.is_valid,
            expires_at=datetime.fromisoformat(st.expires_at) if st.expires_at else None,
            days_left=st.days_left, details={"scopes": st.scopes, "type": st.token_type}))
    if st.is_valid is False:
        log.error("TOKEN INVALID: re-issue the Instagram access token.")
    elif st.days_left is not None:
        hit = [d for d in sorted(warn_days) if st.days_left <= d]
        if hit:
            log.warning("TOKEN EXPIRES SOON: %.1f days left (threshold %d days).", st.days_left, hit[0])


async def collect_once(svc: InstagramService, Session: sessionmaker, settings: Settings) -> dict:
    """One collection run. Returns counters. Token/rate-limit failures abort the run."""
    captured = datetime.now(UTC)
    warn = [int(x) for x in settings.token_warn_days.split(",") if x.strip()]
    await check_token(svc, Session, warn)

    items, cursor = [], None
    while len(items) < settings.collect_max_media:
        page = await svc.list_media(limit=50, cursor=cursor, max_pages=1)
        items += page.items
        cursor = page.next_cursor
        if not cursor:
            break
    items = items[: settings.collect_max_media]

    n_snap = n_null = n_fail = 0
    for it in items:
        with Session.begin() as s:
            m = s.get(Media, it.id)
            posted = parse_ts(it.timestamp) if it.timestamp else None
            if m is None:
                s.add(Media(id=it.id, media_type=it.media_type, media_product_type=it.media_product_type,
                            caption=it.caption, permalink=it.permalink, posted_at=posted))
            else:
                m.caption, m.last_seen_at = it.caption, captured
        try:
            ins = await svc.get_media_insights(it.id)
        except TokenError:
            raise
        except GraphAPIError as e:
            n_fail += 1
            log.warning("Insights failed for media %s: %s", it.id, e)
            continue
        with Session.begin() as s:
            for name, mv in ins.metrics.items():
                s.add(MediaMetricSnapshot(
                    media_id=it.id, metric=name, value=mv.raw_value if mv.available else None,
                    raw_unit=mv.raw_unit, reason=None if mv.available else mv.reason,
                    captured_at=captured))
                n_snap += 1
                n_null += not mv.available
    stats = {"media": len(items), "snapshots": n_snap, "null_snapshots": n_null, "failed_media": n_fail}
    log.info("Collection done: %s", stats)
    return stats


async def run(once: bool) -> None:
    settings = get_settings()
    svc = build_service(settings)
    Session = make_session_factory(make_engine(settings))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set():
            try:
                await collect_once(svc, Session, settings)
            except TokenError as e:
                log.error("Aborting run, token problem: %s", e)
            except GraphAPIError as e:
                log.error("Run failed: %s", e)
            except Exception as e:  # keep the daemon alive; never leak internals
                log.error("Unexpected error: %s", mask(f"{type(e).__name__}: {e}"))
            if once:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.collect_interval_seconds)
            except TimeoutError:
                pass
    finally:
        await svc.client.aclose()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    setup_logging()
    asyncio.run(run(ap.parse_args().once))


if __name__ == "__main__":
    main()
