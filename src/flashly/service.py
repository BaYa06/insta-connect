"""Business logic on top of GraphClient. Read-only by construction: the client
only exposes GET."""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from .config import Settings
from .errors import ConfigError, GraphPermissionError, InvalidRequestError
from .graph_client import GraphClient
from .masking import mask
from .metrics import (
    ACCOUNT_METRICS,
    DEFAULT_ACCOUNT_METRICS,
    DEFAULT_MEDIA_METRICS,
    MEDIA_METRICS,
    build_media_metric,
    extract_value,
    null_metric,
)
from .models import (
    AccountInsights,
    AccountMetric,
    AudienceInsights,
    Child,
    MediaInsights,
    MediaItem,
    MediaList,
    Profile,
    TokenStatus,
)

log = logging.getLogger("flashly.service")

PROFILE_FIELDS = ("id,username,name,biography,followers_count,follows_count,"
                  "media_count,profile_picture_url,website")
MEDIA_FIELDS = ("id,caption,media_type,media_product_type,media_url,thumbnail_url,"
                "permalink,timestamp,like_count,comments_count")
DETAIL_FIELDS = MEDIA_FIELDS + ",children{id,media_type,media_url,thumbnail_url}"
MEDIA_TYPE_FILTERS = {"IMAGE", "VIDEO", "CAROUSEL_ALBUM", "REELS", "FEED", "STORY"}
DEMOGRAPHIC_METRICS = {"follower_demographics", "engaged_audience_demographics",
                       "reached_audience_demographics"}
TIMEFRAME_METRICS = {"engaged_audience_demographics", "reached_audience_demographics"}
AUDIENCE_METRICS = DEMOGRAPHIC_METRICS | {"online_followers"}
BREAKDOWNS = {"age", "city", "country", "gender"}
# Verified live on v27.0: last_14/30/90_days and prev_month are rejected (code 100).
TIMEFRAMES = {"this_month", "this_week"}
MS_NOTE = ("Watch-time metrics are returned by the API in milliseconds; `raw_value` keeps "
           "the original ms, `value` is converted to seconds.")


def parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")


def _to_dt(d: str | date | datetime | None, end: bool = False) -> datetime | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    if isinstance(d, date):
        d = d.isoformat()
    s = d.strip()
    if len(s) == 10:  # YYYY-MM-DD
        dt = datetime.fromisoformat(s).replace(tzinfo=UTC)
        return dt.replace(hour=23, minute=59, second=59) if end else dt
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def matches_type(item: dict, wanted: str | None) -> bool:
    if not wanted:
        return True
    w = wanted.upper()
    if w == "REELS":
        return item.get("media_product_type") == "REELS"
    if w in ("FEED", "STORY"):
        return item.get("media_product_type") == w
    return item.get("media_type") == w


def to_media_item(raw: dict, *, with_children: bool = False) -> MediaItem:
    notes: list[str] = []
    like_reason = None
    if raw.get("like_count") is None:
        like_reason = "like_count absent in API response (likes may be hidden or unavailable)"
    if raw.get("media_type") == "VIDEO" and raw.get("media_url"):
        notes.append("media_url is a temporary MP4 link; it expires, so re-fetch before use.")
    children = None
    if with_children and raw.get("children"):
        children = [Child(**{k: c.get(k) for k in Child.model_fields})
                    for c in raw["children"].get("data", [])]
    return MediaItem(
        **{k: raw.get(k) for k in ("id", "media_type", "media_product_type", "caption",
                                   "permalink", "timestamp", "media_url", "thumbnail_url",
                                   "like_count", "comments_count")},
        like_count_reason=like_reason, children=children, notes=notes,
    )


class InstagramService:
    def __init__(self, client: GraphClient, settings: Settings) -> None:
        self.client = client
        self.s = settings
        self.ig = settings.ig_user_id

    # ---- profile -------------------------------------------------------
    async def get_profile(self) -> Profile:
        raw = await self.client.get(self.ig, {"fields": PROFILE_FIELDS})
        return Profile(**{k: raw.get(k) for k in Profile.model_fields})

    # ---- media list ----------------------------------------------------
    async def list_media(
        self,
        *,
        limit: int = 25,
        cursor: str | None = None,
        media_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        max_pages: int = 10,
    ) -> MediaList:
        if media_type and media_type.upper() not in MEDIA_TYPE_FILTERS:
            raise ValueError(f"media_type must be one of {sorted(MEDIA_TYPE_FILTERS)}")
        limit = max(1, min(limit, 100))
        since_dt, until_dt = _to_dt(since), _to_dt(until, end=True)
        items: list[MediaItem] = []
        next_cursor, pages, exhausted_by_date = cursor, 0, False

        async for page in self.client.paginate(
            f"{self.ig}/media", {"fields": MEDIA_FIELDS, "limit": min(limit, 50)},
            after=cursor, max_pages=max_pages,
        ):
            pages += 1
            next_cursor = page.next_cursor
            for raw in page.data:
                ts = parse_ts(raw["timestamp"]) if raw.get("timestamp") else None
                if ts and since_dt and ts < since_dt:
                    exhausted_by_date = True  # feed is newest-first; nothing older matches
                    continue
                if ts and until_dt and ts > until_dt:
                    continue
                if matches_type(raw, media_type):
                    items.append(to_media_item(raw))
            if exhausted_by_date or len(items) >= limit:
                break
        has_more = bool(next_cursor) and not exhausted_by_date
        return MediaList(items=items, next_cursor=next_cursor if has_more else None,
                         has_more=has_more, pages_fetched=pages)

    # ---- media details -------------------------------------------------
    async def get_media_details(self, media_id: str) -> MediaItem:
        raw = await self.client.get(media_id, {"fields": DETAIL_FIELDS})
        return to_media_item(raw, with_children=True)

    # ---- generic metric fetch with per-metric isolation ----------------
    async def _fetch_metrics(
        self, path: str, metrics: list[str], extra: dict[str, Any]
    ) -> dict[str, tuple[dict | None, str | None]]:
        """One batch call; on code 100 fall back to one call per metric so a single
        unsupported metric does not hide the others."""
        try:
            body = await self.client.get(path, {**extra, "metric": ",".join(metrics)})
            by_name = {d.get("name"): d for d in body.get("data", [])}
            return {m: (by_name.get(m), None if m in by_name else "metric not present in API response")
                    for m in metrics}
        except InvalidRequestError as batch_err:
            if len(metrics) == 1:
                return {metrics[0]: (None, batch_err.message)}
        out: dict[str, tuple[dict | None, str | None]] = {}
        for m in metrics:
            try:
                body = await self.client.get(path, {**extra, "metric": m})
                item = next((d for d in body.get("data", []) if d.get("name") == m), None)
                out[m] = (item, None if item else "metric not present in API response")
            except (InvalidRequestError, GraphPermissionError) as e:
                out[m] = (None, e.message)
        return out

    # ---- media insights ------------------------------------------------
    async def get_media_insights(self, media_id: str, metrics: list[str] | None = None) -> MediaInsights:
        wanted = metrics or DEFAULT_MEDIA_METRICS
        unknown = [m for m in wanted if m not in MEDIA_METRICS]
        if unknown:
            raise ValueError(f"Unsupported metrics {unknown}. Supported: {list(MEDIA_METRICS)}")
        head = await self.client.get(media_id, {"fields": "id,media_type,media_product_type"})
        mpt = head.get("media_product_type")

        result: dict[str, Any] = {}
        to_call: list[str] = []
        for m in wanted:
            if MEDIA_METRICS[m].reels_only and mpt != "REELS":
                result[m] = null_metric(
                    m, f"reels-only metric; this media is {mpt or 'unknown product type'}, not REELS "
                       "(not requested from the API)")
            else:
                to_call.append(m)
        if to_call:
            fetched = await self._fetch_metrics(f"{media_id}/insights", to_call, {})
            for m, (item, reason) in fetched.items():
                result[m] = build_media_metric(m, item, reason)

        notes = [MS_NOTE] if any(MEDIA_METRICS[m].kind != "count" for m in wanted) else []
        if "ig_reels_avg_watch_time" in wanted:
            notes.append("ig_reels_avg_watch_time is an average, not a retention curve; the API "
                         "offers no per-second retention data.")
        return MediaInsights(media_id=media_id, media_type=head.get("media_type"),
                             media_product_type=mpt,
                             metrics={m: result[m] for m in wanted}, notes=notes)

    # ---- account insights ----------------------------------------------
    async def get_account_insights(
        self,
        metrics: list[str] | None = None,
        period: str = "day",
        since: str | None = None,
        until: str | None = None,
        metric_type: str = "total_value",
    ) -> AccountInsights:
        wanted = metrics or DEFAULT_ACCOUNT_METRICS
        unknown = [m for m in wanted if m not in ACCOUNT_METRICS]
        if unknown:
            raise ValueError(f"Unsupported metrics {unknown}. Supported: {ACCOUNT_METRICS}")
        if metric_type not in ("total_value", "time_series"):
            raise ValueError("metric_type must be total_value or time_series")
        extra: dict[str, Any] = {"period": period, "metric_type": metric_type}
        if (d := _to_dt(since)):
            extra["since"] = int(d.timestamp())
        if (d := _to_dt(until, end=True)):
            extra["until"] = int(d.timestamp())
        fetched = await self._fetch_metrics(f"{self.ig}/insights", wanted, extra)
        out: dict[str, AccountMetric] = {}
        for m, (item, reason) in fetched.items():
            if item is None:
                out[m] = AccountMetric(name=m, available=False, reason=reason)
            elif metric_type == "time_series":
                vals = item.get("values") or []
                out[m] = (AccountMetric(name=m, available=True, unit="count", series=vals)
                          if vals else AccountMetric(name=m, available=False,
                                                     reason="API returned an empty series"))
            else:
                v = extract_value(item)
                out[m] = (AccountMetric(name=m, available=True, value=v, unit="count")
                          if v is not None else AccountMetric(
                              name=m, available=False, reason="API returned no value"))
        return AccountInsights(period=period, metric_type=metric_type, since=since, until=until,
                               metrics=out,
                               notes=["Account insights cover a limited window set by Meta; "
                                      "values for missing data are null with a reason."])

    # ---- audience insights (demographics, online followers) -------------
    async def get_audience_insights(
        self, metric: str, breakdown: str | None = None, timeframe: str | None = None
    ) -> AudienceInsights:
        if metric not in AUDIENCE_METRICS:
            raise ValueError(f"metric must be one of {sorted(AUDIENCE_METRICS)}")
        params: dict[str, Any] = {"metric": metric, "period": "lifetime"}
        if metric in DEMOGRAPHIC_METRICS:
            if breakdown not in BREAKDOWNS:
                raise ValueError(f"{metric} needs breakdown in {sorted(BREAKDOWNS)}")
            params["metric_type"] = "total_value"
            params["breakdown"] = breakdown
        elif breakdown:
            raise ValueError("online_followers does not take a breakdown")
        if metric in TIMEFRAME_METRICS:
            if timeframe not in TIMEFRAMES:
                raise ValueError(f"{metric} needs timeframe in {sorted(TIMEFRAMES)}")
            params["timeframe"] = timeframe
        elif timeframe:
            raise ValueError(f"{metric} does not take a timeframe")

        base = dict(metric=metric, breakdown=breakdown, timeframe=timeframe)
        notes = ["Meta generally withholds audience data for accounts with fewer than 100 "
                 "followers (per Meta docs; the API response itself gives no reason)."]
        try:
            body = await self.client.get(f"{self.ig}/insights", params)
        except (InvalidRequestError, GraphPermissionError) as e:
            return AudienceInsights(**base, available=False, reason=e.message, notes=notes)
        item = next((d for d in body.get("data", []) if d.get("name") == metric), None)
        if item is None:
            return AudienceInsights(**base, available=False, notes=notes,
                                    reason="API returned an empty data list (no data for this account/period)")
        if metric == "online_followers":
            vals = item.get("values") or []
            hourly = vals[-1].get("value") if vals else None
            if not isinstance(hourly, dict) or not hourly:
                return AudienceInsights(**base, available=False, notes=notes,
                                        reason="API returned no hourly values")
            return AudienceInsights(**base, available=True, hourly=hourly, notes=[
                "Estimated followers online per hour of day (key = hour 0-23). "
                "The time zone of the hours is defined by Meta; not verified here."])
        results = []
        for bd in (item.get("total_value") or {}).get("breakdowns", []):
            for r in bd.get("results", []):
                results.append({"dimension": ", ".join(r.get("dimension_values", [])),
                                "value": r.get("value")})
        if not results:
            return AudienceInsights(**base, available=False, notes=notes,
                                    reason="API returned the metric without breakdown results")
        return AudienceInsights(**base, available=True, results=results)

    # ---- token status --------------------------------------------------
    async def get_token_status(self, now: datetime | None = None) -> TokenStatus:
        now = now or datetime.now(UTC)
        token = self.s.token() if self.s.mode == "live" else "mock-token"
        app_secret = self.s.meta_app_secret.get_secret_value() if self.s.meta_app_secret else None
        # App access token (APP_ID|APP_SECRET) when available, else the token itself.
        inspector = f"{self.s.meta_app_id}|{app_secret}" if app_secret else token
        body = await self.client.get("debug_token", {"input_token": token}, access_token=inspector)
        d = body.get("data", {})

        def ts(v: int | None) -> datetime | None:
            return datetime.fromtimestamp(v, UTC) if v else None

        exp, dexp = ts(d.get("expires_at")), ts(d.get("data_access_expires_at"))
        never = d.get("expires_at") == 0
        days = (exp - now).total_seconds() / 86400 if exp else None
        warning = None
        if d.get("is_valid") is False:
            warning = "Token is not valid. Re-issue it."
        elif days is not None and days <= 14:
            warning = f"Token expires in {days:.1f} days; refresh it."
        return TokenStatus(
            is_valid=d.get("is_valid"), token_type=d.get("type"), app_id=d.get("app_id"),
            expires_at=exp.isoformat() if exp else None, never_expires=never,
            days_left=round(days, 2) if days is not None else None,
            data_access_expires_at=dexp.isoformat() if dexp else None,
            data_access_days_left=round((dexp - now).total_seconds() / 86400, 2) if dexp else None,
            scopes=d.get("scopes", []), warning=warning,
        )


def build_service(settings: Settings | None = None, *, transport=None, sleep=None) -> InstagramService:
    """Factory: mock mode wires the fixture transport; live mode validates config."""
    from .config import get_settings
    from .mock import build_mock_transport

    settings = settings or get_settings()
    kwargs: dict[str, Any] = {}
    if sleep:
        kwargs["sleep"] = sleep
    if transport is None and settings.mode == "mock":
        transport = build_mock_transport(settings.fixtures_dir)
    elif settings.mode == "live":
        settings.validate_for_live()
    client = GraphClient(settings, transport=transport, **kwargs)
    return InstagramService(client, settings)


__all__ = ["InstagramService", "build_service", "ConfigError", "mask"]
