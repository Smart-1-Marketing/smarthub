"""The MCP gateway's read of the cam pages a client owns.

Ask SmartHub calls `get_cam_performance` when someone asks how the
cam did last month, or which sponsor is running best. The tool's
period convention matches `get_client_performance` (hub/periods.py),
so a chat session that picked "last_month" for the ad tool passes
the same string here and the answer is on the same window.

Reads only the rollup, never the raw event table, so the query is
cheap enough to answer inside a chat turn.
"""
from __future__ import annotations

import logging
from datetime import date

from . import reports as _reports
from . import store as _store
from . import tracking as _tracking
from .models import Placement, boot_error, session

log = logging.getLogger("hub")


def _agg(rows: list[dict]) -> dict:
    imp = sum(r["impressions"] for r in rows)
    clk = sum(r["clicks"] for r in rows)
    return {"impressions": imp, "clicks": clk,
            "ctr": round(100.0 * clk / imp, 2) if imp else 0.0,
            "unique_sessions": sum(r["unique_sessions"] for r in rows)}


def _placements_for(page_ids: list[int]) -> dict[int, dict]:
    if not page_ids:
        return {}
    from sqlalchemy import select
    out: dict[int, dict] = {}
    with session() as s:
        pls = s.execute(select(Placement).where(
            Placement.page_id.in_(page_ids))).scalars().all()
        for pl in pls:
            out[pl.id] = {"id": pl.id, "page_id": pl.page_id,
                          "position": pl.position,
                          "name": pl.name or "", "sponsor_id": pl.sponsor_id or 0,
                          "is_house": bool(pl.is_house)}
    return out


def get_cam_performance(client_name: str, period: str = "last_30",
                        compare: str = "previous_period",
                        start_date: str = "", end_date: str = "",
                        limit: int = 50, today: date | None = None) -> dict:
    """The cam pages a client owns, for one period, with per-placement
    numbers. Returns the same "found/available/error" shape the other
    MCP tools use so Ask SmartHub can render a not-available answer
    without special casing."""
    from hub import periods
    name = (client_name or "").strip()
    if not name:
        return {"found": False, "available": False,
                "error": "A client name is required."}
    err = boot_error()
    if err:
        return {"found": False, "available": False, "client": name,
                "error": f"CamHub is offline: {err[:200]}",
                "reason": "unavailable"}
    try:
        window = periods.resolve(period, start=start_date, end=end_date,
                                 today=today)
        compare_win = periods.compare_window(window, compare)
    except ValueError as exc:
        return {"found": True, "available": False, "client": name,
                "error": str(exc)[:300], "reason": "invalid_period",
                "periods": list(periods.PERIODS),
                "compares": list(periods.COMPARES)}
    pages = [p for p in _store.list_pages()
             if (p.get("client_name") or "").lower() == name.lower()]
    if not pages:
        return {"found": True, "available": True, "client": name,
                "pages": [], "reason": "no_pages",
                "message": f"{name} has no CamHub page."}
    limit = max(1, min(int(limit or 50), 200))
    per_page = []
    combined_now: list[dict] = []
    combined_was: list[dict] = []
    for page in pages:
        now = _tracking.stats(page["id"], window.start.isoformat(),
                              window.end.isoformat())
        was = (_tracking.stats(page["id"], compare_win.start.isoformat(),
                               compare_win.end.isoformat())
               if compare_win is not None else
               {"page": {}, "placements": {}})
        pl_map = _placements_for([page["id"]])
        placement_rows = []
        for pl_id, pl in pl_map.items():
            rn = (now.get("placements") or {}).get(pl_id) or {}
            rw = (was.get("placements") or {}).get(pl_id) or {}
            row = {"placement_id": pl_id,
                   "name": pl["name"] or ("House ad" if pl["is_house"] else "Placement"),
                   "position": pl["position"], "is_house": pl["is_house"],
                   "impressions": int(rn.get("impressions") or 0),
                   "clicks": int(rn.get("clicks") or 0),
                   "unique_sessions": int(rn.get("unique_sessions") or 0),
                   "prior_impressions": int(rw.get("impressions") or 0),
                   "prior_clicks": int(rw.get("clicks") or 0)}
            row["ctr"] = round(100.0 * row["clicks"] / row["impressions"], 2) if row["impressions"] else 0.0
            row["prior_ctr"] = (round(100.0 * row["prior_clicks"] / row["prior_impressions"], 2)
                                if row["prior_impressions"] else 0.0)
            row["viewable_share"] = rn.get("viewable_share")
            placement_rows.append(row)
            combined_now.append(row)
            combined_was.append({"impressions": row["prior_impressions"],
                                 "clicks": row["prior_clicks"],
                                 "unique_sessions": 0})
        placement_rows.sort(key=lambda r: (-(r["impressions"] or 0), r["placement_id"]))
        per_page.append({
            "slug": page["slug"], "title": page["title"],
            "pageviews": int((now.get("page") or {}).get("pageviews") or 0),
            "unique_sessions": int((now.get("page") or {}).get("unique_sessions") or 0),
            "prior_pageviews": int((was.get("page") or {}).get("pageviews") or 0),
            "placements": placement_rows[:limit],
        })
    return {"found": True, "available": True, "client": name,
            "window": window.as_dict(),
            "compare": (compare_win.as_dict() if compare_win is not None else None),
            "pages": per_page,
            "totals": {"now": _agg(combined_now),
                       "prior": _agg([{"impressions": r["impressions"],
                                       "clicks": r["clicks"],
                                       "unique_sessions": r["unique_sessions"]}
                                      for r in combined_was])},
            "message": (f"{name}: "
                        f"{sum(p['pageviews'] for p in per_page):,} pageviews, "
                        f"{_agg(combined_now)['impressions']:,} viewable "
                        f"impressions on {window.label}.")}
