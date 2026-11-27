"""Metric registry and value conversion.

Rules enforced here:
* Watch-time metrics arrive in milliseconds. raw value is kept, and the
  presented value is converted to seconds with explicit unit labels.
* ig_reels_avg_watch_time is a single average, never a retention curve.
* Missing data is None plus a reason, never a placeholder or estimate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

AVG_WATCH_NOTE = (
    "Average watch time per view (a single number). The Instagram API provides no "
    "per-second audience-retention curve; none is derived or estimated here."
)
TOTAL_WATCH_NOTE = "Total watch time summed over all views of this reel."


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: str  # count | average_time_ms | total_time_ms
    reels_only: bool = False
    note: str | None = None


MEDIA_METRICS: dict[str, MetricSpec] = {s.name: s for s in [
    MetricSpec("views", "count"),
    MetricSpec("reach", "count"),
    MetricSpec("saved", "count"),
    MetricSpec("shares", "count"),
    MetricSpec("total_interactions", "count"),
    MetricSpec("ig_reels_avg_watch_time", "average_time_ms", True, AVG_WATCH_NOTE),
    MetricSpec("ig_reels_video_view_total_time", "total_time_ms", True, TOTAL_WATCH_NOTE),
]}
DEFAULT_MEDIA_METRICS = list(MEDIA_METRICS)

ACCOUNT_METRICS = ["reach", "views", "accounts_engaged", "total_interactions", "profile_views",
                   "likes", "comments", "shares", "saves", "follows_and_unfollows", "follower_count"]
DEFAULT_ACCOUNT_METRICS = ["reach", "views", "accounts_engaged", "total_interactions"]


class MetricValue(BaseModel):
    name: str
    available: bool
    value: float | int | None = None        # presented value (seconds for time metrics)
    unit: str | None = None                 # "count" | "seconds"
    raw_value: float | int | None = None    # exactly as returned by the API
    raw_unit: str | None = None             # "milliseconds" for time metrics
    aggregation: str | None = None          # "average" | "total"
    reason: str | None = None               # why value is null
    note: str | None = None


def ms_to_seconds(ms: float | int) -> float:
    return round(ms / 1000.0, 3)


def null_metric(name: str, reason: str) -> MetricValue:
    spec = MEDIA_METRICS.get(name)
    unit = None
    if spec and spec.kind != "count":
        unit = "seconds"
    elif spec:
        unit = "count"
    return MetricValue(name=name, available=False, value=None, unit=unit, reason=reason,
                       note=spec.note if spec else None)


def extract_value(item: dict) -> Any:
    """Pull the scalar out of an insights `data[]` item. None if absent."""
    if isinstance(item.get("total_value"), dict):
        return item["total_value"].get("value")
    values = item.get("values") or []
    if values and isinstance(values[0], dict):
        return values[0].get("value")
    return None


def build_media_metric(name: str, item: dict | None, reason: str | None = None) -> MetricValue:
    if item is None:
        return null_metric(name, reason or "metric not returned by the API")
    raw = extract_value(item)
    if raw is None or isinstance(raw, (dict, list)):
        return null_metric(name, reason or "API returned the metric without a value")
    spec = MEDIA_METRICS[name]
    if spec.kind == "count":
        return MetricValue(name=name, available=True, value=raw, unit="count",
                           raw_value=raw, raw_unit="count")
    return MetricValue(
        name=name, available=True, value=ms_to_seconds(raw), unit="seconds",
        raw_value=raw, raw_unit="milliseconds",
        aggregation="average" if spec.kind == "average_time_ms" else "total", note=spec.note,
    )
