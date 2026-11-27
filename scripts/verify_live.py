#!/usr/bin/env python
"""Run every endpoint and every metric; print a table: работает / не работает / нет данных.

  python scripts/verify_live.py            # uses MODE from .env
  python scripts/verify_live.py --mode mock

Read-only. Tokens are never printed (all output passes through mask()).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashly.config import get_settings  # noqa: E402
from flashly.errors import ConfigError, GraphAPIError, TokenError  # noqa: E402
from flashly.masking import mask, setup_logging  # noqa: E402
from flashly.metrics import ACCOUNT_METRICS, MEDIA_METRICS  # noqa: E402
from flashly.service import build_service  # noqa: E402

OK, FAIL, NODATA = "работает", "не работает", "нет данных"


@dataclass
class Row:
    area: str
    check: str
    status: str
    detail: str = ""


class Abort(Exception):
    pass


async def attempt(rows: list[Row], area: str, check: str, coro_fn, describe):
    """Run one check. Exceptions -> 'не работает'. describe(result) -> (status, detail)."""
    try:
        res = await coro_fn()
    except TokenError as e:
        rows.append(Row(area, check, FAIL, mask(e.describe())))
        raise Abort("Токен недействителен/истёк: дальнейшие проверки бессмысленны.") from None
    except GraphAPIError as e:
        rows.append(Row(area, check, FAIL, mask(e.describe())))
        return None
    except Exception as e:  # noqa: BLE001
        rows.append(Row(area, check, FAIL, mask(f"{type(e).__name__}: {e}")))
        return None
    status, detail = describe(res)
    rows.append(Row(area, check, status, mask(detail)))
    return res


def present(v, label="") -> tuple[str, str]:
    return (OK, f"{label}{v}") if v not in (None, "") else (NODATA, "поле пустое/отсутствует")


async def run(args) -> list[Row]:
    s = get_settings()
    if args.mode:
        s = s.model_copy(update={"mode": args.mode})
    svc = build_service(s)
    rows: list[Row] = []
    try:
        # 1. token
        st = await attempt(rows, "token", "debug_token", svc.get_token_status,
                           lambda r: (OK if r.is_valid else FAIL,
                                      f"valid={r.is_valid}, days_left={r.days_left}, "
                                      f"scopes={len(r.scopes)}"))
        # 2. profile
        prof = await attempt(rows, "profile", "get_profile", svc.get_profile,
                             lambda r: (OK, f"@{r.username}, followers={r.followers_count}, "
                                            f"media={r.media_count}"))
        if prof:
            for f in ("followers_count", "follows_count", "media_count", "biography"):
                rows.append(Row("profile", f, *present(getattr(prof, f))))

        # 3. media list + pagination
        first = await attempt(rows, "media", "list_media (стр.1)",
                              lambda: svc.list_media(limit=25, max_pages=1),
                              lambda r: (OK if r.items else NODATA,
                                         f"{len(r.items)} шт., has_more={r.has_more}"))
        if first and first.next_cursor:
            await attempt(rows, "media", "list_media (пагинация, стр.2)",
                          lambda: svc.list_media(limit=25, cursor=first.next_cursor, max_pages=1),
                          lambda r: (OK if r.items else NODATA, f"{len(r.items)} шт."))
        elif first:
            rows.append(Row("media", "list_media (пагинация, стр.2)", NODATA, "следующей страницы нет"))

        # 4. samples per type (scan up to 4 pages)
        wanted = {"REELS": None, "IMAGE": None, "CAROUSEL_ALBUM": None}
        cursor = None
        for _ in range(4):
            if all(wanted.values()):
                break
            page = await svc.list_media(limit=50, cursor=cursor, max_pages=1)
            for it in page.items:
                key = ("REELS" if it.media_product_type == "REELS" else
                       it.media_type if it.media_type in ("IMAGE", "CAROUSEL_ALBUM") else None)
                if key and not wanted[key]:
                    wanted[key] = it
            cursor = page.next_cursor
            if not cursor:
                break
        for t in ("REELS", "IMAGE", "CAROUSEL_ALBUM"):
            if not wanted[t]:
                rows.append(Row("media", f"пример {t}", NODATA, "в последних 200 постах не найден"))

        # 5. details + 6. insights per metric
        for t, item in wanted.items():
            if not item:
                continue
            area = f"{t} {item.id}"
            d = await attempt(rows, area, "get_media_details",
                              lambda i=item: svc.get_media_details(i.id),
                              lambda r: (OK, f"ts={r.timestamp}"))
            if d:
                rows.append(Row(area, "caption", *present((d.caption or "")[:40] or None)))
                rows.append(Row(area, "cover (thumbnail_url/media_url)",
                                *present(d.thumbnail_url or (d.media_url if t != "REELS" else None))))
                rows.append(Row(area, "like_count", *(
                    (OK, str(d.like_count)) if d.like_count is not None
                    else (NODATA, d.like_count_reason or ""))))
                rows.append(Row(area, "comments_count", *present(d.comments_count)))
                if t == "REELS":
                    rows.append(Row(area, "MP4 link (media_url)", *(
                        (OK, "есть (временная ссылка)") if d.media_url else (NODATA, "отсутствует"))))
            for m in MEDIA_METRICS:
                async def one(i=item, m=m):
                    return await svc.get_media_insights(i.id, [m])

                def desc(r, m=m):
                    mv = r.metrics[m]
                    if mv.available:
                        extra = (f" (raw {mv.raw_value} {mv.raw_unit})"
                                 if mv.raw_unit == "milliseconds" else "")
                        return OK, f"{mv.value} {mv.unit}{extra}"
                    return NODATA, f"null: {mv.reason}"
                await attempt(rows, area, f"insight {m}", one, desc)

        # 7. account insights
        until = datetime.now(UTC).date()
        since = until - timedelta(days=7)
        for m in ACCOUNT_METRICS:
            mt = "time_series" if m == "follower_count" else "total_value"

            async def acc(m=m, mt=mt):
                return await svc.get_account_insights([m], "day", since.isoformat(),
                                                      until.isoformat(), mt)

            def adesc(r, m=m):
                x = r.metrics[m]
                if x.available:
                    return OK, (f"{x.value}" if x.series is None else f"{len(x.series)} точек")
                return NODATA, f"null: {x.reason}"
            await attempt(rows, "account", f"insight {m}", acc, adesc)
    except Abort as a:
        rows.append(Row("abort", "-", FAIL, str(a)))
    finally:
        await svc.client.aclose()
    return rows


def render(rows: list[Row], mode: str) -> str:
    w1 = max(len(r.area) for r in rows)
    w2 = max(len(r.check) for r in rows)
    lines = [f"MODE={mode}", "", f"{'ОБЪЕКТ':<{w1}}  {'ПРОВЕРКА':<{w2}}  {'СТАТУС':<11}  ДЕТАЛИ",
             "-" * (w1 + w2 + 40)]
    for r in rows:
        lines.append(f"{r.area:<{w1}}  {r.check:<{w2}}  {r.status:<11}  {r.detail}")
    n = {k: sum(r.status == k for r in rows) for k in (OK, FAIL, NODATA)}
    lines += ["", f"ИТОГО: {OK}={n[OK]}  {FAIL}={n[FAIL]}  {NODATA}={n[NODATA]}"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "live"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    setup_logging(logging.WARNING)
    try:
        rows = asyncio.run(run(args))
    except ConfigError as e:
        print(f"Конфигурация: {mask(e)}\nДобавьте значения в .env (см. .env.example).", file=sys.stderr)
        return 2
    mode = args.mode or get_settings().mode
    if args.json:
        print(json.dumps([r.__dict__ for r in rows], ensure_ascii=False, indent=2))
    else:
        print(render(rows, mode))
    return 1 if any(r.status == FAIL for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
