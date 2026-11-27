from __future__ import annotations

from pydantic import BaseModel

from .metrics import MetricValue


class Profile(BaseModel):
    id: str
    username: str | None = None
    name: str | None = None
    biography: str | None = None
    followers_count: int | None = None
    follows_count: int | None = None
    media_count: int | None = None
    profile_picture_url: str | None = None
    website: str | None = None


class Child(BaseModel):
    id: str
    media_type: str | None = None
    media_url: str | None = None
    thumbnail_url: str | None = None


class MediaItem(BaseModel):
    id: str
    media_type: str | None = None          # IMAGE | VIDEO | CAROUSEL_ALBUM
    media_product_type: str | None = None  # FEED | REELS | STORY
    caption: str | None = None
    permalink: str | None = None
    timestamp: str | None = None
    media_url: str | None = None           # for videos/reels: the MP4 link (temporary)
    thumbnail_url: str | None = None       # cover
    like_count: int | None = None
    like_count_reason: str | None = None
    comments_count: int | None = None
    children: list[Child] | None = None
    notes: list[str] = []


class MediaList(BaseModel):
    items: list[MediaItem]
    next_cursor: str | None
    has_more: bool
    pages_fetched: int


class MediaInsights(BaseModel):
    media_id: str
    media_type: str | None
    media_product_type: str | None
    metrics: dict[str, MetricValue]
    notes: list[str]


class AccountMetric(BaseModel):
    name: str
    available: bool
    value: float | int | None = None
    unit: str | None = None
    series: list[dict] | None = None
    reason: str | None = None


class AccountInsights(BaseModel):
    period: str
    metric_type: str
    since: str | None
    until: str | None
    metrics: dict[str, AccountMetric]
    notes: list[str]


class TokenStatus(BaseModel):
    is_valid: bool | None
    token_type: str | None
    app_id: str | None
    expires_at: str | None
    never_expires: bool
    days_left: float | None
    data_access_expires_at: str | None
    data_access_days_left: float | None
    scopes: list[str]
    warning: str | None = None


class AudienceInsights(BaseModel):
    metric: str
    breakdown: str | None
    timeframe: str | None
    available: bool
    results: list[dict] | None = None   # demographics: [{"dimension": "US", "value": 5}]
    hourly: dict[str, int | None] | None = None  # online_followers: hour "0".."23" -> followers
    reason: str | None = None
    notes: list[str] = []
