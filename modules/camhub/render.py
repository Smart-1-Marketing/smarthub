"""The server-rendered page: everything a crawler must see is in the initial
HTML -- the conditions strip, the verdict, the forecast, the sponsor copy,
the prose -- because a video iframe and a client-side widget give a search
engine nothing to index. The current temperature is injected into the meta
description on every render; the JSON-LD carries the cam as a live
VideoObject, the host as a LocalBusiness and the lake as a Place.

Sponsors: until Sprint 3 gives placements a table and an editor, every slot
renders its house ad from the page config, so an unsold slot is still
working and never an empty box.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .tiles import build_strip, fresh
from .verdict import verdict

log = logging.getLogger("hub")
WEATHER_KEYS = ("weather_now", "observation", "lake_level", "alerts", "advisories")


def _tz(page):
    try:
        return ZoneInfo(page.get("timezone") or "America/New_York")
    except Exception:  # noqa: BLE001
        return timezone.utc


def updated_at(cache: dict, page: dict) -> tuple[datetime | None, str]:
    latest = None
    for key in WEATHER_KEYS:
        f = (cache.get(key) or {}).get("fetched_at")
        if f and (latest is None or f > latest):
            latest = f
    if not latest:
        return None, ""
    local = latest.astimezone(_tz(page))
    return latest, local.strftime("%I:%M %p %Z").lstrip("0")


def _age_words(then: datetime | None, now: datetime) -> str:
    if not then:
        return ""
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} days ago"


def _advisory_bars(cache: dict, now: datetime) -> list[dict]:
    """Rendered only when something is active -- never an empty 'no
    advisories' box 350 days a year. BeachGuard fails closed: no data, no bar."""
    bars = []
    alerts = (fresh(cache, "alerts", 90, now) or {}).get("alerts") or []
    for a in alerts:
        kind = "warning" if (a.get("severity") in ("Extreme", "Severe")
                             or "Warning" in str(a.get("event") or "")) else "watch"
        bars.append({"kind": kind, "tag": a.get("event") or "Alert",
                     "text": a.get("headline") or a.get("event") or "",
                     "url": a.get("url"), "link_text": "NWS details"})
    beach = fresh(cache, "advisories", 60 * 24, now) or {}
    if beach.get("in_season", True):
        for adv in beach.get("active") or []:
            sev = adv.get("severity") or 0
            bars.append({"kind": "warning" if sev >= 3 else "advisory",
                         "tag": "Advisory",
                         "text": f"{adv.get('type') or 'Recreational public health advisory'} — "
                                 f"{beach.get('beach_name') or 'public beach'}. "
                                 f"{adv.get('reason') or 'Avoid swimming and keep pets out of the water.'}",
                         "url": beach.get("url"), "link_text": "Ohio BeachGuard details"})
    return bars


def _forecast(cache: dict, page: dict, now: datetime) -> list[dict]:
    fc = fresh(cache, "forecast_daily", 180, now) or {}
    days = []
    today = now.astimezone(_tz(page)).date().isoformat()
    for d in fc.get("days") or []:
        dow = "Today" if d.get("date") == today else (
            datetime.fromisoformat(d["date"]).strftime("%a") if d.get("date") else d.get("name", ""))
        wind = str(d.get("wind") or "").replace(" to ", "–").replace(" mph", "")
        days.append({**d, "dow": dow, "today": d.get("date") == today,
                     "wind_text": f"{d.get('wind_dir') or ''} {wind}".strip()})
    return days[:7]


def _sponsor_slots(page: dict, preview: dict | None = None) -> dict:
    """The five slots as sponsors.py picks them for this page load; a
    preview claim (a signed token from the editor, plus the unsaved draft)
    forces one placement into its position."""
    from . import sponsors
    try:
        slots = sponsors.select_slots(page)
    except Exception as exc:  # noqa: BLE001 -- the page must render with house ads, whatever the tables do
        log.warning("camhub: placements unreadable for %s: %s", page.get("slug"), exc)
        cfg = sponsors._config_house(page)
        slots = {"presenting": sponsors._slot(cfg["presenting"], 0, False, page),
                 "supporting": [sponsors._slot(t, i + 1, False, page) for i, t in enumerate(cfg["supporting"])],
                 "sold": False, "sold_supporting": 0}
        while len(slots["supporting"]) < sponsors.SUPPORTING_SLOTS:
            slots["supporting"].append(sponsors._slot(
                {"name": "Sponsor this tile", "body": "Reach lake visitors while they plan the day.",
                 "cta_label": "Get the rate card"}, len(slots["supporting"]) + 1, False, page))
    if preview:
        slots = sponsors.apply_preview(slots, page, preview["claim"], preview.get("draft") or {})
    return slots


def _jsonld(page: dict, ctx: dict) -> list[dict]:
    cfg = page.get("config") or {}
    canonical = ctx.get("canonical") or ""
    out: list[dict] = []
    if page.get("cam_embed_url"):
        video = {
            "@context": "https://schema.org", "@type": "VideoObject",
            "name": cfg.get("h1") or page["title"],
            "description": ctx["meta_description"],
            "uploadDate": (page.get("created_at") or datetime.now(timezone.utc)).date().isoformat(),
            "embedUrl": page["cam_embed_url"],
            "publication": {"@type": "BroadcastEvent", "isLiveBroadcast": True,
                            "startDate": (page.get("created_at") or datetime.now(timezone.utc)).date().isoformat()},
        }
        if cfg.get("cam_thumbnail_url"):
            video["thumbnailUrl"] = cfg["cam_thumbnail_url"]
        if canonical:
            video["url"] = canonical
        out.append(video)
    biz = cfg.get("business") or {}
    if biz.get("name"):
        business = {"@context": "https://schema.org", "@type": "LocalBusiness", "name": biz["name"],
                    "address": {"@type": "PostalAddress", "streetAddress": biz.get("street", ""),
                                "addressLocality": biz.get("city", ""), "addressRegion": biz.get("state", ""),
                                "postalCode": biz.get("zip", ""), "addressCountry": "US"},
                    "geo": {"@type": "GeoCoordinates", "latitude": page["lat"], "longitude": page["lon"]}}
        if biz.get("url"):
            business["url"] = biz["url"]
        if biz.get("phone"):
            business["telephone"] = biz["phone"]
        out.append(business)
    lake = cfg.get("lake") or {}
    if lake.get("name"):
        out.append({"@context": "https://schema.org", "@type": "Place", "name": lake["name"],
                    "description": lake.get("description", ""),
                    "geo": {"@type": "GeoCoordinates", "latitude": page["lat"], "longitude": page["lon"]}})
    if canonical:
        home = canonical.rsplit("/", 1)[0] + "/" if "/" in canonical.split("//", 1)[-1] else canonical
        out.append({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": home},
            {"@type": "ListItem", "position": 2, "name": cfg.get("h1") or page["title"], "item": canonical}]})
    return out


def build(page: dict, cache: dict, now: datetime | None = None,
          preview: dict | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg = page.get("config") or {}
    strip = build_strip(page, cache, now)
    v = verdict(page, cache, now)
    wx = fresh(cache, "weather_now", 90, now) or fresh(cache, "observation", 90, now) or {}
    lake = fresh(cache, "lake_level", 120, now) or {}
    latest, updated_text = updated_at(cache, page)

    bits = []
    if wx.get("temp_f") is not None:
        bits.append(f"currently {wx['temp_f']}°F")
    if wx.get("wind_mph") is not None:
        bits.append(f"{wx.get('wind_dir') or ''} wind {wx['wind_mph']} mph".strip())
    if lake.get("elevation_ft") is not None:
        bits.append(f"lake level {lake['elevation_ft']:.2f} ft")
    place = cfg.get("place") or page.get("location_name") or ""
    meta = (f"Live {page.get('location_name') or 'lake'} webcam with today's conditions"
            + (": " + ", ".join(bits) if bits else "")
            + (f". Updated {updated_text}." if updated_text else "."))
    title = page["title"]
    ctx = {
        "page": page, "cfg": cfg, "now": now,
        "title": title, "meta_description": meta[:300],
        "h1": cfg.get("h1") or page["title"],
        "place": place,
        "current_temp": wx.get("temp_f"),
        "current_text": (wx.get("text") or _sky_words(wx)) if wx else "",
        "updated_text": updated_text, "updated_age": _age_words(latest, now),
        "updated_iso": latest.isoformat(timespec="seconds") if latest else "",
        "stale": bool(latest) and (now - latest).total_seconds() > 45 * 60,
        "strip": strip, "verdict": v,
        "advisories": _advisory_bars(cache, now),
        "forecast": _forecast(cache, page, now),
        "sponsors": _sponsor_slots(page, preview),
        "canonical": cfg.get("canonical_url") or "",
        "embed": _embed(page),
        "theme": cfg.get("theme") or {},
    }
    ctx["jsonld"] = json.dumps(_jsonld(page, ctx), ensure_ascii=False)
    return ctx


def _sky_words(wx: dict) -> str:
    t = wx.get("day_max_thunder_pct") or 0
    p = wx.get("day_max_precip_pct") or 0
    sky = wx.get("sky_pct")
    if t >= 30:
        return "chance of thunderstorms"
    if p >= 40:
        return "chance of rain"
    if sky is None:
        return ""
    if sky < 25:
        return "clear"
    if sky < 60:
        return "partly cloudy"
    return "mostly cloudy"


def _embed(page: dict) -> dict:
    url = (page.get("cam_embed_url") or "").strip()
    kind = page.get("cam_embed_type") or "youtube"
    if not url:
        return {"ok": False, "reason": "No stream is set on this page yet."}
    if kind == "youtube":
        sep = "&" if "?" in url else "?"
        return {"ok": True, "kind": "youtube",
                "src": f"{url}{sep}autoplay=1&mute=1&playsinline=1&rel=0"}
    return {"ok": True, "kind": "iframe", "src": url}
