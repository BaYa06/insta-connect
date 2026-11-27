"""MCP server (read-only). Streamable HTTP for connectors, stdio for local use."""
from __future__ import annotations

import argparse
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .auth import MIN_KEY_LENGTH, BearerAuthMiddleware
from .config import get_settings
from .errors import ConfigError, GraphAPIError
from .masking import mask, setup_logging
from .service import InstagramService, build_service

log = logging.getLogger("flashly.server")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                            openWorldHint=True)
MediaTypeFilter = Literal["IMAGE", "VIDEO", "CAROUSEL_ALBUM", "REELS", "FEED", "STORY"]


def _safe(fn: Callable[..., Awaitable[Any]]):
    """Convert any failure into a masked ToolError; dump pydantic results to JSON."""

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            res = await fn(*a, **kw)
            return res.model_dump(mode="json") if hasattr(res, "model_dump") else res
        except GraphAPIError as e:
            raise ToolError(str(e)) from None
        except (ConfigError, ValueError) as e:
            raise ToolError(mask(e)) from None
        except Exception as e:  # never leak internals/secrets
            log.exception("tool %s failed", fn.__name__)
            raise ToolError(f"Internal error: {type(e).__name__}") from None

    return wrapper


def create_server(service: InstagramService | None = None, *, host: str = "127.0.0.1",
                  port: int = 8000, behind_auth: bool = False) -> FastMCP:
    svc: InstagramService | None = service

    def S() -> InstagramService:
        nonlocal svc
        if svc is None:
            svc = build_service()
        return svc

    # The SDK's DNS-rebinding guard only trusts localhost Host headers, which would
    # reject tunnel/proxy hostnames. With a bearer key in front it is redundant.
    sec = TransportSecuritySettings(enable_dns_rebinding_protection=False) if behind_auth else None
    mcp = FastMCP("flashly-growth-ai", host=host, port=port, transport_security=sec,
                  instructions=("Read-only analytics for the Instagram account @flashly_official. "
                                "Watch-time values are in seconds (raw API milliseconds kept in "
                                "raw_value). ig_reels_avg_watch_time is an average, not a retention "
                                "curve. null means no data; see the `reason` field."))
    tool = functools.partial(mcp.tool, annotations=READ_ONLY)

    @tool(description="Instagram account profile: username, bio, followers, follows, media count.")
    @_safe
    async def get_profile():
        return await S().get_profile()

    @tool(description=("List recent posts and Reels (newest first) with caption, permalink, cover, "
                       "likes, comments. Filter by media_type and ISO dates (since/until, e.g. "
                       "2026-10-01). Paginate by passing the returned next_cursor as cursor."))
    @_safe
    async def list_media(limit: int = 25, cursor: str | None = None,
                         media_type: MediaTypeFilter | None = None,
                         since: str | None = None, until: str | None = None):
        return await S().list_media(limit=limit, cursor=cursor, media_type=media_type,
                                    since=since, until=until)

    @tool(description=("Full details of one media item: caption, cover, likes, comments count, "
                       "carousel children and the temporary MP4 link for videos/Reels."))
    @_safe
    async def get_media_details(media_id: str):
        return await S().get_media_details(media_id)

    @tool(description=("Per-media insights: views, reach, saved, shares, total_interactions, "
                       "ig_reels_avg_watch_time (average, seconds), ig_reels_video_view_total_time "
                       "(total, seconds). Unavailable metrics are null with a reason."))
    @_safe
    async def get_media_insights(media_id: str, metrics: list[str] | None = None):
        return await S().get_media_insights(media_id, metrics)

    @tool(description=("Account-level insights (reach, views, accounts_engaged, total_interactions, "
                       "...) for a period. since/until are ISO dates. metric_type: total_value or "
                       "time_series."))
    @_safe
    async def get_account_insights(metrics: list[str] | None = None, period: str = "day",
                                   since: str | None = None, until: str | None = None,
                                   metric_type: Literal["total_value", "time_series"] = "total_value"):
        return await S().get_account_insights(metrics, period, since, until, metric_type)

    @tool(description=("Audience data. metric: follower_demographics | engaged_audience_demographics | "
                       "reached_audience_demographics (need breakdown: age/city/country/gender; the last "
                       "two also need timeframe: this_week or this_month) | online_followers (followers online per hour). "
                       "Usually empty for accounts under 100 followers; then available=false with a reason."))
    @_safe
    async def get_audience_insights(metric: Literal[
            "follower_demographics", "engaged_audience_demographics",
            "reached_audience_demographics", "online_followers"],
            breakdown: Literal["age", "city", "country", "gender"] | None = None,
            timeframe: Literal["this_week", "this_month"] | None = None):
        return await S().get_audience_insights(metric, breakdown, timeframe)

    @tool(description="Access-token health via debug_token: validity, expiry, days left. Never shows the token.")
    @_safe
    async def get_token_status():
        return await S().get_token_status()

    return mcp


def build_http_app(host: str = "127.0.0.1", port: int = 8000, *, allow_no_auth: bool = False,
                   allow_path_key: bool = True):
    """ASGI app for Streamable HTTP, wrapped in bearer auth. Refuses to run unauthenticated
    unless explicitly allowed (and then only on loopback)."""
    s = get_settings()
    key = s.mcp_auth_token.get_secret_value() if s.mcp_auth_token else ""
    if not key:
        if not (allow_no_auth and host in ("127.0.0.1", "localhost", "::1")):
            raise ConfigError("MCP_AUTH_TOKEN is not set. Set it in .env (>= %d chars). "
                              "--no-auth is allowed only on 127.0.0.1." % MIN_KEY_LENGTH)
        log.warning("Running WITHOUT authentication (loopback only)")
        return create_server(host=host, port=port).streamable_http_app()
    app = create_server(host=host, port=port, behind_auth=True).streamable_http_app()
    return BearerAuthMiddleware(app, key, allow_path_key=allow_path_key)


def main() -> None:
    p = argparse.ArgumentParser(prog="flashly-mcp")
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="streamable-http")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-auth", action="store_true", help="loopback testing only")
    p.add_argument("--no-path-key", action="store_true", help="disable the /mcp/<key> URL-path key")
    a = p.parse_args()
    setup_logging()
    s = get_settings()
    log.info("Starting in %s mode (%s:%s, %s)", s.mode, a.host, a.port, a.transport)
    if a.transport == "stdio":
        create_server().run(transport="stdio")
        return
    import uvicorn
    try:
        app = build_http_app(a.host, a.port, allow_no_auth=a.no_auth, allow_path_key=not a.no_path_key)
    except ConfigError as e:
        raise SystemExit(f"Config error: {mask(e)}") from None
    # access_log off: URLs carry the /mcp/<key> credential.
    uvicorn.run(app, host=a.host, port=a.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
