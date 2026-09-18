"""What Client 360 shows about the cam pages a client owns.

The spec's Client 360 card lives in the record's workbench, opened
by the client's name. It answers three sponsor-time questions in one
glance -- how much traffic is the page moving, how are its placements
performing this month, is the data pipeline healthy -- and links into
the builder for the fuller view. This module is the data feed; the
card itself is rendered in hub/templates/client360.html.

Reads only:
- `camhub_pages` for the client's slug and title
- `camhub_daily_stats` (via `tracking.stats`) for the month
- `store.health` for the source health rollup

Never touches the raw event table or the sponsor tables directly;
placement labels come from `sponsors.list_placements`, and the sponsor
name a slot shows is whatever `render.build` uses on the live page."""
from __future__ import annotations

import logging
from datetime import date

from . import sponsors as _sponsors
from . import store as _store
from . import tracking as _tracking
from .models import boot_error

log = logging.getLogger("hub")


def _summary(health_rows: list[dict]) -> str:
    """Green / amber / red for the whole page's source pipeline."""
    if not health_rows:
        return "amber"
    reds = sum(1 for r in health_rows if r.get("state") == "red")
    ambers = sum(1 for r in health_rows if r.get("state") == "amber")
    if reds:
        return "red"
    if ambers:
        return "amber"
    return "green"


def for_client(client_name: str, today: date | None = None) -> dict:
    """The card for one client. Never raises; a boot error, a missing
    client or an unavailable rollup shows up as `measured=False`."""
    name = (client_name or "").strip()
    err = boot_error()
    if err:
        return {"measured": False, "client": name, "pages": [],
                "reason": err[:200]}
    today = today or date.today()
    try:
        pages = [p for p in _store.list_pages() if (p.get("client_name") or "").lower() == name.lower()]
    except Exception as exc:  # noqa: BLE001
        return {"measured": False, "client": name, "pages": [],
                "reason": type(exc).__name__}
    if not pages:
        return {"measured": True, "client": name, "pages": []}
    start, end = _tracking.month_bounds(today)
    out = []
    for page in pages:
        health_rows = _store.health(page["id"])
        month = _tracking.stats(page["id"], start, end)
        placements = _sponsors.list_placements(page["id"])
        rows = []
        for pl in placements:
            per = (month.get("placements") or {}).get(pl["id"]) or {}
            rows.append({
                "id": pl["id"],
                "name": pl.get("name") or pl.get("sponsor_name") or "Placement",
                "position": pl["position"], "is_house": bool(pl.get("is_house")),
                "impressions": int(per.get("impressions") or 0),
                "clicks": int(per.get("clicks") or 0),
                "ctr": (round(100.0 * (per.get("clicks") or 0) / (per.get("impressions") or 1), 2)
                        if per.get("impressions") else 0.0),
                "viewable_share": per.get("viewable_share"),
                "sponsor_name": pl.get("sponsor_name") or "",
            })
        rows.sort(key=lambda r: (0 if r["position"] == "presenting" else 1,
                                 -(r["impressions"] or 0), r["id"]))
        out.append({
            "slug": page["slug"], "title": page["title"],
            "live_url": f"/tools/camhub/cam/{page['slug']}",
            "staff_url": f"/tools/camhub/pages/{page['slug']}",
            "pageviews_month": int((month.get("page") or {}).get("pageviews") or 0),
            "unique_sessions_month": int((month.get("page") or {}).get("unique_sessions") or 0),
            "placements": rows[:5],
            "sources": {"green": sum(1 for r in health_rows if r.get("state") == "green"),
                        "amber": sum(1 for r in health_rows if r.get("state") == "amber"),
                        "red": sum(1 for r in health_rows if r.get("state") == "red")},
            "health": _summary(health_rows),
            "period": {"start": start, "end": end},
        })
    return {"measured": True, "client": name, "pages": out}
