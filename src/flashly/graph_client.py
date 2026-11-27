"""Async Graph API client: retries, error mapping, pagination, rate-limit tracking.

Mock mode uses the exact same code path; only the httpx transport differs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings
from .errors import (
    GraphAPIError,
    NetworkError,
    TransientError,
    parse_error,
)
from .masking import mask

log = logging.getLogger("flashly.graph")

# httpx logs full request URLs (incl. access_token) at INFO: keep it quiet by default.
for _n in ("httpx", "httpcore"):
    logging.getLogger(_n).setLevel(logging.WARNING)

USAGE_WARN_PERCENT = 80


@dataclass
class AppUsage:
    """Parsed x-app-usage / x-business-use-case-usage headers (percentages)."""

    call_count: float | None = None
    total_time: float | None = None
    total_cputime: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def max_percent(self) -> float:
        vals = [v for v in (self.call_count, self.total_time, self.total_cputime) if v is not None]
        return max(vals) if vals else 0.0

    def to_dict(self) -> dict:
        return {"call_count": self.call_count, "total_time": self.total_time,
                "total_cputime": self.total_cputime}


def parse_app_usage(headers: httpx.Headers | dict) -> AppUsage | None:
    raw = headers.get("x-app-usage")
    if raw:
        try:
            d = json.loads(raw)
            return AppUsage(d.get("call_count"), d.get("total_time"), d.get("total_cputime"), d)
        except (ValueError, AttributeError):
            return None
    buc = headers.get("x-business-use-case-usage")
    if buc:
        try:
            d = json.loads(buc)
            # {"<id>": [{"call_count":..,"total_time":..,"total_cputime":..,"type":..}]}
            entries = [e for v in d.values() for e in v]
            if entries:
                worst = max(entries, key=lambda e: e.get("call_count", 0))
                return AppUsage(worst.get("call_count"), worst.get("total_time"),
                                worst.get("total_cputime"), worst)
        except (ValueError, AttributeError, TypeError):
            return None
    return None


@dataclass
class Page:
    data: list[dict]
    next_cursor: str | None  # cursor to pass as `after`; None when no further page
    raw: dict


Sleep = Callable[[float], Awaitable[None]]


class GraphClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=f"{settings.graph_base_url.rstrip('/')}/{settings.graph_api_version}/",
            timeout=settings.http_timeout_seconds,
            transport=transport,
        )
        self.last_app_usage: AppUsage | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- token handling -------------------------------------------------
    def _token(self, override: str | None) -> str:
        if override:
            return override
        if self.settings.mode == "mock":
            return "mock-token"
        return self.settings.token()

    # -- core request ---------------------------------------------------
    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        access_token: str | None = None,
    ) -> dict:
        q = {k: v for k, v in (params or {}).items() if v is not None}
        q["access_token"] = self._token(access_token)
        path = path.lstrip("/")
        attempt = 0
        while True:
            try:
                resp = await self._http.get(path, params=q)
            except httpx.HTTPError as e:
                # httpx messages/tracebacks can embed the URL; drop the chain.
                err: GraphAPIError = NetworkError(f"{type(e).__name__} while calling /{path}")
                if not await self._maybe_retry(err, attempt):
                    raise err from None
                attempt += 1
                continue

            usage = parse_app_usage(resp.headers)
            if usage:
                self.last_app_usage = usage
                if usage.max_percent >= USAGE_WARN_PERCENT:
                    log.warning("Graph API usage high: %s", usage.to_dict())

            body = self._json(resp)
            if resp.status_code < 400 and not (isinstance(body, dict) and "error" in body):
                log.debug("GET /%s -> %s", path, resp.status_code)
                return body
            err = parse_error(resp.status_code, body, dict(resp.headers))
            log.info("GET /%s -> %s %s", path, resp.status_code, err.category)
            if not await self._maybe_retry(err, attempt):
                raise err
            attempt += 1

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            if resp.status_code >= 400:
                return None
            raise TransientError("Graph API returned non-JSON body",
                                 http_status=resp.status_code) from None

    async def _maybe_retry(self, err: GraphAPIError, attempt: int) -> bool:
        if not err.retryable or attempt >= self.settings.max_retries:
            return False
        base = self.settings.retry_base_delay_seconds * (2**attempt)
        delay = min(base + random.uniform(0, base / 4), self.settings.retry_max_delay_seconds)
        if err.retry_after:
            delay = min(max(delay, err.retry_after), self.settings.retry_max_delay_seconds)
        log.warning("Retry %d/%d in %.1fs after %s", attempt + 1, self.settings.max_retries,
                    delay, mask(err.describe()))
        await self._sleep(delay)
        return True

    # -- pagination -----------------------------------------------------
    async def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        after: str | None = None,
        max_pages: int = 20,
    ) -> AsyncIterator[Page]:
        """Yield pages. Continues with the `after` cursor, never by following
        `paging.next`, because that URL embeds the access token."""
        cursor = after
        for _ in range(max_pages):
            q = dict(params or {})
            if cursor:
                q["after"] = cursor
            body = await self.get(path, q)
            paging = body.get("paging") or {}
            nxt = paging.get("cursors", {}).get("after") if paging.get("next") else None
            yield Page(data=body.get("data", []), next_cursor=nxt, raw=body)
            if not nxt:
                return
            cursor = nxt
