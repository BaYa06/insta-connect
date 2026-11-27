"""Mock Mode: serves fixtures/ through an httpx.MockTransport.

Responses mimic the real Graph API shape, including error bodies, so the real
client code path (retries, error mapping, pagination) is exercised without network.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

CURSOR_P2 = "QVFHcGFnZTI"


def _err(code: int, message: str, status: int = 400, sub: int | None = None) -> httpx.Response:
    e = {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "MOCKTRACE"}
    if sub:
        e["error_subcode"] = sub
    return httpx.Response(status, json={"error": e})


def build_mock_transport(fixtures: Path) -> httpx.MockTransport:
    def load(*parts: str) -> dict:
        return json.loads(Path(fixtures, *parts).read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        segs = [s for s in urlparse(str(url)).path.split("/") if s]
        segs = segs[1:] if segs and segs[0].startswith("v") else segs  # drop version
        q = dict(url.params)
        profile = load("profile.json")

        if segs == ["debug_token"]:
            d = load("debug_token.json")
            exp = int(time.time() + d["days_until_expiry"] * 86400)
            return httpx.Response(200, json={"data": {
                "app_id": d["app_id"], "type": "USER", "application": "Flashly",
                "is_valid": d["is_valid"], "expires_at": exp, "issued_at": exp - 60 * 86400,
                "data_access_expires_at": exp + 30 * 86400, "scopes": d["scopes"],
                "user_id": d["user_id"]}})

        if len(segs) == 1 and segs[0] == profile["id"]:
            return httpx.Response(200, json=profile)

        if len(segs) == 2 and segs[0] == profile["id"] and segs[1] == "media":
            page = "media_page2.json" if q.get("after") == CURSOR_P2 else "media_page1.json"
            return httpx.Response(200, json=load(page))

        if len(segs) == 2 and segs[0] == profile["id"] and segs[1] == "insights":
            if q.get("metric") in ("follower_demographics", "engaged_audience_demographics",
                                   "reached_audience_demographics", "online_followers"):
                return _audience_insights(load("audience_insights.json"), q)
            return _account_insights(load("account_insights.json"), q)

        if len(segs) == 1 and segs[0].isdigit():
            p = Path(fixtures, "media", f"{segs[0]}.json")
            if not p.exists():
                return _err(100, "Unsupported get request. Object does not exist.", 400, 33)
            return httpx.Response(200, json=json.loads(p.read_text(encoding="utf-8")))

        if len(segs) == 2 and segs[0].isdigit() and segs[1] == "insights":
            p = Path(fixtures, "insights", f"{segs[0]}.json")
            if not p.exists():
                return _err(100, "Unsupported get request. Object does not exist.", 400, 33)
            return _media_insights(json.loads(p.read_text(encoding="utf-8")), q)

        return _err(100, f"Mock has no route for /{'/'.join(segs)}", 404)

    return httpx.MockTransport(handler)


def _media_insights(fx: dict, q: dict) -> httpx.Response:
    requested = [m for m in q.get("metric", "").split(",") if m]
    for m in requested:
        if m in fx.get("unsupported", {}):
            return _err(100, fx["unsupported"][m])
    data = []
    for m in requested:
        if m not in fx["metrics"] and m not in fx.get("empty", []):
            return _err(100, f"(#100) metric {m} is not supported for this media")
        values = [] if m in fx.get("empty", []) else [{"value": fx["metrics"][m]}]
        data.append({"name": m, "period": "lifetime", "values": values,
                     "title": m, "description": m, "id": f"{q.get('x', 'm')}/insights/{m}/lifetime"})
    return httpx.Response(200, json={"data": data})


def _account_insights(fx: dict, q: dict) -> httpx.Response:
    requested = [m for m in q.get("metric", "").split(",") if m]
    series = q.get("metric_type") == "time_series"
    data = []
    for m in requested:
        if m in fx.get("unsupported", {}):
            return _err(100, fx["unsupported"][m])
        if m not in fx["metrics"]:
            return _err(100, f"(#100) metric {m} is not supported")
        if series:
            vals = [{"value": v, "end_time": t} for t, v in fx["series"].get(m, [])]
            data.append({"name": m, "period": q.get("period", "day"), "values": vals})
        else:
            data.append({"name": m, "period": q.get("period", "day"),
                         "total_value": {"value": fx["metrics"][m]}})
    return httpx.Response(200, json={"data": data})


def _audience_insights(fx: dict, q: dict) -> httpx.Response:
    m = q["metric"]
    if m in ("engaged_audience_demographics", "reached_audience_demographics") and not q.get("timeframe"):
        return _err(100, f"(#100) Metrics ({m}) require timeframe parameter to be specified")
    if m == "online_followers" and q.get("metric_type") == "time_series":
        return _err(100, "(#100) The following metric (online_followers) is incompatible with the metric type (time_series)")
    if m in fx.get("empty", []):
        return httpx.Response(200, json={"data": []})
    if m == "online_followers":
        return httpx.Response(200, json={"data": [{"name": m, "period": "lifetime",
            "values": [{"value": fx["online_followers"], "end_time": "2026-11-27T08:00:00+0000"}]}]})
    results = [{"dimension_values": [k], "value": v} for k, v in fx["demographics"][q["breakdown"]].items()]
    return httpx.Response(200, json={"data": [{"name": m, "period": "lifetime", "total_value": {
        "breakdowns": [{"dimension_keys": [q["breakdown"]], "results": results}]}}]})
